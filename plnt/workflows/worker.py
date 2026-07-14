"""Temporal worker for plnt deploy workflows.

Runs alongside the operator in the plnt-system namespace. One Deployment
scales to N replicas — activities are idempotent and can execute anywhere.

Start:
    python -m plnt.workflows.worker
"""

from __future__ import annotations

import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from plnt.workflows.activities import (
    helm_install_canary,
    helm_rollback,
    promote_to_stable,
    pull_and_verify_weights,
    run_smoke_test,
    validate_manifest,
)
from plnt.workflows.deploy_model import DeployModelWorkflow

TEMPORAL_ADDR = os.environ.get("TEMPORAL_ADDRESS", "temporal-frontend.plnt-system.svc:7233")
TASK_QUEUE = os.environ.get("PLNT_TASK_QUEUE", "plnt-deploys")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(TEMPORAL_ADDR)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[DeployModelWorkflow],
        activities=[
            validate_manifest,
            pull_and_verify_weights,
            helm_install_canary,
            run_smoke_test,
            promote_to_stable,
            helm_rollback,
        ],
    )
    logging.info("plnt worker started · queue=%s · temporal=%s", TASK_QUEUE, TEMPORAL_ADDR)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
