"""Parallel orchestrator — fan out N AgentSpecs concurrently.

Sits beside Orchestrator (not on top of it) so single-spawn paths stay simple.
Use this when one user intent should materialise as many independent micro-
agents — the "research-librarian spawns five paper-readers" pattern.

Concurrency is capped per-host. Default: min(8, cpu_count - 2). On a personal
machine that's the right scale; the OS scheduler and the sandbox's own
cpu/mem caps do the rest.

The Docker rung benefits most from this: 8 containers running in parallel
with `--cpus=1 --memory=1g` each don't starve the host the way 8 native
processes can.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from plnt.control.acc import ACCMonitor
from plnt.control.budget import BudgetExceeded, BudgetGovernor
from plnt.execution.blackboard import Blackboard
from plnt.execution.sandbox import get_sandbox
from plnt.execution.sandbox.base import Sandbox, SandboxResult
from plnt.execution.spec import AgentSpec

logger = logging.getLogger(__name__)


def default_cap() -> int:
    cores = os.cpu_count() or 4
    return max(1, min(8, cores - 2))


@dataclass
class FanOutResult:
    spawned: int
    completed: int
    killed: int
    results: list[SandboxResult]


class ParallelOrchestrator:
    """Spawn many agents at once. One Blackboard, one budget, one ACC."""

    def __init__(
        self,
        blackboard: Blackboard,
        budget: BudgetGovernor,
        acc: ACCMonitor | None = None,
        max_concurrency: int | None = None,
    ):
        self.bb = blackboard
        self.budget = budget
        self.acc = acc
        self.max_concurrency = max_concurrency or default_cap()
        # Sandboxes are stateful (they hold the running container/process).
        # We track them by agent_id so kill() can find the right one.
        self._sandboxes: dict[str, Sandbox] = {}
        self._lock = threading.Lock()

    def fan_out(self, specs: list[AgentSpec]) -> FanOutResult:
        """Run all specs concurrently. Returns when all finish (or are killed)."""
        # Pre-flight budget check — if even the smallest can't fit, bail.
        for spec in specs:
            try:
                self.budget.check_spawn(spec)
            except BudgetExceeded as e:
                self.bb.emit(
                    "error",
                    agent_id=spec.id,
                    payload={"reason": f"budget pre-check: {e}"},
                )
                # Don't raise — let the rest of the batch try.

        # Wire ACC to kill into our registry if it has anyone to kill.
        if self.acc is not None:
            original = self.acc.kill_fn

            def kill_dispatch(agent_id: str, reason: str) -> bool:
                with self._lock:
                    sb = self._sandboxes.get(agent_id)
                if sb is None:
                    return False
                return sb.kill(agent_id, reason)

            self.acc.kill_fn = kill_dispatch
            try:
                return self._run_pool(specs)
            finally:
                self.acc.kill_fn = original
        return self._run_pool(specs)

    def _run_pool(self, specs: list[AgentSpec]) -> FanOutResult:
        results: list[SandboxResult] = []
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            future_map: dict[Future, AgentSpec] = {}
            for spec in specs:
                fut = pool.submit(self._run_one, spec)
                future_map[fut] = spec

            for fut in as_completed(future_map):
                spec = future_map[fut]
                try:
                    res = fut.result()
                    results.append(res)
                except Exception as e:  # noqa: BLE001
                    self.bb.emit(
                        "error",
                        agent_id=spec.id,
                        payload={"reason": f"spawn crashed: {e}"},
                    )

        killed = sum(1 for r in results if r.killed)
        return FanOutResult(
            spawned=len(specs),
            completed=len(results),
            killed=killed,
            results=results,
        )

    def _run_one(self, spec: AgentSpec) -> SandboxResult:
        SandboxCls = get_sandbox(spec.isolation)
        sandbox = SandboxCls(blackboard=self.bb)
        with self._lock:
            self._sandboxes[spec.id] = sandbox
        try:
            return sandbox.run(spec)
        finally:
            with self._lock:
                self._sandboxes.pop(spec.id, None)
