"""InferenceModel controller.

Watches `plnt.work/v1/InferenceModel` resources and reconciles them by driving
a Temporal `DeployModelWorkflow`.

Design:

* One workflow per (namespace, name).
* Create → start workflow. Update (generation bump) → cancel + restart.
  Delete → cancel + helm uninstall.
* The workflow does the real work — retries, saga, compensation. This
  controller is a thin bridge between K8s events and Temporal.
* Status subresource is patched on every workflow milestone via the callbacks
  the workflow emits (see plnt/workflows/deploy_model.py).

Run:
    kopf run plnt/operators/inferencemodel_controller.py --namespace=plnt-system
"""

from __future__ import annotations

import logging
import os
from typing import Any

import kopf
from temporalio.client import Client

log = logging.getLogger("plnt.operator")

TEMPORAL_ADDR = os.environ.get("TEMPORAL_ADDRESS", "temporal-frontend.plnt-system.svc:7233")
TASK_QUEUE = os.environ.get("PLNT_TASK_QUEUE", "plnt-deploys")


def _wf_id(namespace: str, name: str) -> str:
    return f"deploy-{namespace}-{name}"


async def _client() -> Client:
    return await Client.connect(TEMPORAL_ADDR)


@kopf.on.create("plnt.work", "v1", "inferencemodels")
async def on_create(spec: dict, name: str, namespace: str, patch: dict, **_: Any) -> None:
    log.info("InferenceModel/%s created — starting DeployModelWorkflow", name)
    from plnt.workflows.deploy_model import DeployModelWorkflow

    client = await _client()
    handle = await client.start_workflow(
        DeployModelWorkflow.run,
        args=[{"namespace": namespace, "name": name, "spec": spec}],
        id=_wf_id(namespace, name),
        task_queue=TASK_QUEUE,
    )
    patch.status["phase"] = "Validating"
    patch.status["workflowRunId"] = handle.result_run_id


@kopf.on.update("plnt.work", "v1", "inferencemodels")
async def on_update(
    spec: dict,
    name: str,
    namespace: str,
    old: dict,
    new: dict,
    patch: dict,
    **_: Any,
) -> None:
    # Only react to spec changes — status patches would loop forever.
    if old.get("spec") == new.get("spec"):
        return
    log.info("InferenceModel/%s spec changed — restarting workflow", name)
    client = await _client()
    old_handle = client.get_workflow_handle(_wf_id(namespace, name))
    try:
        await old_handle.cancel()
    except Exception:  # noqa: BLE001
        pass

    from plnt.workflows.deploy_model import DeployModelWorkflow

    handle = await client.start_workflow(
        DeployModelWorkflow.run,
        args=[{"namespace": namespace, "name": name, "spec": spec}],
        id=_wf_id(namespace, name),
        task_queue=TASK_QUEUE,
    )
    patch.status["phase"] = "Validating"
    patch.status["workflowRunId"] = handle.result_run_id


@kopf.on.delete("plnt.work", "v1", "inferencemodels")
async def on_delete(name: str, namespace: str, **_: Any) -> None:
    log.info("InferenceModel/%s deleted — cancelling workflow + helm uninstall", name)
    client = await _client()
    handle = client.get_workflow_handle(_wf_id(namespace, name))
    try:
        await handle.cancel()
    except Exception:  # noqa: BLE001
        pass
    # The workflow's cancellation path handles `helm uninstall` via the
    # compensating activity — nothing else to do here.
