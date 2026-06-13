"""DAG executor — runs AgentSpecs with dependencies in topological order.

Plnt's old fan_out treated every spec as independent. Kimi's actual win is
**linked outputs**: agent B's input includes agent A's structured result.
This module models that.

Each AgentSpec may carry depends_on=[agent_id, ...]. The DAG executor:
  1. Runs all specs with no remaining deps in parallel (a layer).
  2. After each completes, its output (a dict) lands in a shared store.
  3. Dependents see the upstream outputs injected into their inputs.from_agents.
  4. Repeats until everything has run or the budget is gone.

A single cycle in deps fails the run cleanly with an error event.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from plnt.control.acc import ACCMonitor
from plnt.control.budget import BudgetExceeded, BudgetGovernor
from plnt.execution.blackboard import Blackboard
from plnt.execution.sandbox import get_sandbox
from plnt.execution.sandbox.base import SandboxResult
from plnt.execution.spec import AgentSpec

logger = logging.getLogger(__name__)


@dataclass
class DAGResult:
    spawned: int
    completed: int
    killed: int
    results: list[SandboxResult] = field(default_factory=list)
    outputs: dict[str, dict] = field(default_factory=dict)  # agent_id -> output dict


class DAGExecutor:
    """Topo-order executor with dependency injection."""

    def __init__(
        self,
        blackboard: Blackboard,
        budget: BudgetGovernor,
        acc: ACCMonitor | None = None,
        max_concurrency: int | None = None,
    ):
        import os
        self.bb = blackboard
        self.budget = budget
        self.acc = acc
        # Tight cap for CPU-only small models: too many parallel agents make
        # the model server queue and total wall-time balloons.
        if max_concurrency is None:
            max_concurrency = int(os.environ.get("PLNT_MAX_CONCURRENCY", "3"))
        self.max_concurrency = max(1, max_concurrency)

    def run(self, specs: list[AgentSpec]) -> DAGResult:
        deps = self._collect_deps(specs)
        if self._has_cycle(deps):
            self.bb.emit("error", payload={"reason": "DAG cycle detected; aborting swarm"})
            return DAGResult(spawned=0, completed=0, killed=0)

        # In-degree count per agent_id
        in_degree: dict[str, int] = {s.id: len(deps[s.id]) for s in specs}
        specs_by_id: dict[str, AgentSpec] = {s.id: s for s in specs}
        outputs: dict[str, dict] = {}
        results: list[SandboxResult] = []
        completed = 0
        killed = 0

        lock = threading.Lock()
        # Reverse map: agent_id -> [dependents]
        dependents: dict[str, list[str]] = defaultdict(list)
        for s in specs:
            for upstream in deps[s.id]:
                dependents[upstream].append(s.id)

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            # Loop over layers — submit anything that's ready, wait, repeat.
            remaining = set(specs_by_id.keys())
            while remaining:
                ready = [aid for aid in remaining if in_degree[aid] == 0]
                if not ready:
                    self.bb.emit("error", payload={"reason": "DAG stuck — deps unresolvable"})
                    break

                # Inject upstream outputs into each ready spec
                ready_specs = [self._inject(specs_by_id[aid], outputs, deps[aid]) for aid in ready]

                # Budget pre-check
                for spec in ready_specs:
                    try:
                        self.budget.check_spawn(spec)
                    except BudgetExceeded as e:
                        self.bb.emit("error", agent_id=spec.id, payload={"reason": f"budget pre-check: {e}"})

                futures = {pool.submit(self._run_one, spec): spec for spec in ready_specs}

                for fut, spec in futures.items():
                    try:
                        res: SandboxResult = fut.result()
                    except Exception as e:  # noqa: BLE001
                        self.bb.emit("error", agent_id=spec.id, payload={"reason": f"spawn crashed: {e}"})
                        continue
                    results.append(res)
                    completed += 1
                    if res.killed:
                        killed += 1

                    out = self._extract_output(res)
                    with lock:
                        outputs[spec.id] = out

                    for child in dependents[spec.id]:
                        in_degree[child] -= 1

                    remaining.discard(spec.id)

        return DAGResult(
            spawned=len(specs),
            completed=completed,
            killed=killed,
            results=results,
            outputs=outputs,
        )

    # ----- helpers --------------------------------------------------------

    def _collect_deps(self, specs: list[AgentSpec]) -> dict[str, list[str]]:
        d: dict[str, list[str]] = {}
        valid_ids = {s.id for s in specs}
        for s in specs:
            raw = s.inputs.get("depends_on", []) if isinstance(s.inputs, dict) else []
            if not isinstance(raw, list):
                raw = []
            d[s.id] = [str(x) for x in raw if isinstance(x, str) and x in valid_ids]
        return d

    def _has_cycle(self, deps: dict[str, list[str]]) -> bool:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = dict.fromkeys(deps.keys(), WHITE)

        def visit(node: str) -> bool:
            if color[node] == GRAY:
                return True
            if color[node] == BLACK:
                return False
            color[node] = GRAY
            for up in deps.get(node, []):
                if visit(up):
                    return True
            color[node] = BLACK
            return False

        return any(visit(n) for n in deps if color[n] == WHITE)

    def _inject(self, spec: AgentSpec, outputs: dict[str, dict], deps: list[str]) -> AgentSpec:
        if not deps:
            return spec
        upstream = {aid: outputs.get(aid, {}) for aid in deps}
        new_inputs = dict(spec.inputs)
        new_inputs["from_agents"] = upstream
        return spec.model_copy(update={"inputs": new_inputs})

    def _extract_output(self, res: SandboxResult) -> dict:
        if not res.output:
            return {}
        if isinstance(res.output, dict):
            inner = res.output.get("output")
            if isinstance(inner, dict):
                return inner
            return res.output
        return {"value": res.output}

    def _run_one(self, spec: AgentSpec) -> SandboxResult:
        SandboxCls = get_sandbox(spec.isolation)
        sandbox = SandboxCls(blackboard=self.bb)
        return sandbox.run(spec)
