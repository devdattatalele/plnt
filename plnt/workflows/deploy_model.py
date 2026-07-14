"""DeployModelWorkflow — the deploy saga for one InferenceModel.

Five steps. Any step failing triggers compensation (`helm rollback`).
Retries are per-activity with budgets; some errors (bad manifest, unpullable
image) short-circuit as non-retryable.

Register with a Temporal worker (see `plnt/workflows/worker.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from plnt.workflows.activities import (
        helm_install_canary,
        helm_rollback,
        promote_to_stable,
        pull_and_verify_weights,
        run_smoke_test,
        validate_manifest,
    )


DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    backoff_coefficient=2.0,
    non_retryable_error_types=[
        "ManifestInvalidError",
        "ImageUnpullableError",
        "GpuClassUnavailableError",
    ],
)


@dataclass
class DeployRequest:
    namespace: str
    name: str
    spec: dict[str, Any]


@dataclass
class DeployResult:
    status: str            # "ready" | "rolled_back" | "failed"
    endpoint: str | None
    reason: str | None
    workflow_run_id: str


@workflow.defn
class DeployModelWorkflow:
    """Deploys one InferenceModel end-to-end with saga semantics."""

    @workflow.run
    async def run(self, req: DeployRequest) -> DeployResult:
        release_info: dict | None = None

        try:
            # 1. Validate the manifest against schema + cluster reality.
            await workflow.execute_activity(
                validate_manifest,
                req.spec,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )

            # 2. Pull weights from the registry, hash-verify. Long timeout —
            #    a 70B model at gigabit takes minutes.
            await workflow.execute_activity(
                pull_and_verify_weights,
                req.spec,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=DEFAULT_RETRY,
                heartbeat_timeout=timedelta(seconds=60),
            )

            # 3. helm install the canary at 5% traffic weight.
            release_info = await workflow.execute_activity(
                helm_install_canary,
                {"namespace": req.namespace, "name": req.name, "spec": req.spec},
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=DEFAULT_RETRY,
            )

            # 4. Run smoke test — TTFT / TPOT / tokens-per-s against baseline.
            smoke = await workflow.execute_activity(
                run_smoke_test,
                {"release": release_info, "spec": req.spec},
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=DEFAULT_RETRY,
            )
            if not smoke["passed"]:
                await workflow.execute_activity(
                    helm_rollback,
                    release_info,
                    start_to_close_timeout=timedelta(minutes=2),
                )
                return DeployResult(
                    status="rolled_back",
                    endpoint=None,
                    reason=smoke["reason"],
                    workflow_run_id=workflow.info().run_id,
                )

            # 5. Promote to 100% traffic.
            await workflow.execute_activity(
                promote_to_stable,
                release_info,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=DEFAULT_RETRY,
            )

            return DeployResult(
                status="ready",
                endpoint=release_info["endpoint"],
                reason=None,
                workflow_run_id=workflow.info().run_id,
            )

        except Exception as exc:
            # Compensate — best-effort rollback of anything the earlier steps
            # created. If release_info is None we haven't installed anything.
            if release_info is not None:
                try:
                    await workflow.execute_activity(
                        helm_rollback,
                        release_info,
                        start_to_close_timeout=timedelta(minutes=2),
                    )
                except Exception:  # noqa: BLE001
                    pass  # nothing else we can do from inside the workflow
            return DeployResult(
                status="failed",
                endpoint=None,
                reason=f"{type(exc).__name__}: {exc}",
                workflow_run_id=workflow.info().run_id,
            )
