"""Orchestrator — ties the four planes together for one run.

Lifecycle of one intent:
  1. Surface hands an intent to Orchestrator.start_run().
  2. Orchestrator emits the `intent` event, opens a Blackboard.
  3. Planner LLM picks a resident specialist.
  4. Specialist emits AgentSpec(s) for ephemeral spawns.
  5. Each spawn runs in its sandbox; events flow back into the Blackboard.
  6. ACC + budget governor watch the stream and kill misbehaviour.
  7. Final result is written to a markdown file under the user's choice of
     output dir, and a `result` event closes the run.

v0 keeps step 3 simple: the planner is implemented in code as a router that
maps intent → role via keyword match, and falls back to `general-helper`.
A future revision swaps in an LLM-based planner without changing this file's
shape — the planner is just a function from (intent, registry) → AgentSpec.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from plnt.control.acc import ACCMonitor
from plnt.control.budget import BudgetExceeded, BudgetGovernor, RunBudget
from plnt.control.skills import SkillRegistry
from plnt.execution.blackboard import Blackboard
from plnt.execution.sandbox import get_sandbox
from plnt.execution.sandbox.base import SandboxResult
from plnt.execution.spec import AgentSpec, Budget


@dataclass
class RunHandle:
    run_id: str
    intent: str
    blackboard: Blackboard
    budget: BudgetGovernor
    acc: ACCMonitor
    result: SandboxResult | None = None


PlannerFn = Callable[[str, SkillRegistry], AgentSpec]


def keyword_planner(intent: str, registry: SkillRegistry) -> AgentSpec:
    """Default planner — keyword routing onto the loaded skills."""
    lower = intent.lower()
    available = registry.list()

    # Cheap heuristic; the skill bundles list their own routing keywords in
    # the front-matter `keywords:` line. We don't read those yet — v0 just
    # falls back to a `general-helper` if no name matches.
    chosen = "general-helper"
    for role in available:
        if role in lower or role.replace("-", " ") in lower:
            chosen = role
            break

    sk = registry.get(chosen) or registry.get("general-helper")
    tools = sk.tools if sk else ["search", "execute"]
    model_hint = (sk.model_hint if sk else "auto") or "auto"
    if model_hint not in ("small", "deep", "auto"):
        model_hint = "auto"

    return AgentSpec(
        role=chosen,
        run_id=f"r-{uuid.uuid4().hex[:10]}",  # overwritten by Orchestrator
        depth=0,
        lifetime="ephemeral",
        isolation="process",
        tools=tools,
        inputs={
            "intent": intent,
            "skill_prompt": sk.prompt if sk else None,
        },
        model_hint=model_hint,  # type: ignore[arg-type]
        budget=Budget(
            tokens=sk.budget.get("tokens", 20_000) if sk else 20_000,
            wall_seconds=sk.budget.get("wall_seconds", 300) if sk else 300,
            joules=sk.budget.get("joules", 0) if sk else 0,
        ),
    )


class Orchestrator:
    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
        run_budget: RunBudget | None = None,
        planner: PlannerFn | None = None,
        runs_root: Path | None = None,
    ):
        self.skills = skill_registry or SkillRegistry()
        self.run_budget = run_budget or RunBudget()
        self.planner = planner or keyword_planner
        self.runs_root = runs_root

    def start_run(self, intent: str) -> RunHandle:
        run_id = f"r-{uuid.uuid4().hex[:10]}"
        bb = Blackboard(run_id, root=self.runs_root)
        bb.emit("intent", payload={"text": intent})
        budget = BudgetGovernor(run_id, self.run_budget, blackboard=bb)

        # ACC will call back via the sandbox.kill() once it's constructed.
        kill_target: dict[str, Callable] = {}

        def kill(agent_id: str, reason: str) -> bool:
            fn = kill_target.get("kill")
            if fn:
                return bool(fn(agent_id, reason))
            return False

        acc = ACCMonitor(kill_fn=kill)

        spec = self.planner(intent, self.skills)
        spec = spec.model_copy(update={"run_id": run_id})

        try:
            budget.check_spawn(spec)
        except BudgetExceeded as e:
            bb.emit("error", payload={"reason": f"budget pre-check: {e}"})
            bb.emit("finished")
            return RunHandle(run_id, intent, bb, budget, acc)

        sandbox_cls = get_sandbox(spec.isolation)
        sandbox = sandbox_cls(blackboard=bb)
        kill_target["kill"] = sandbox.kill

        # Feed the ACC the events we just appended (the runner will emit more
        # via the subprocess; those land in the same file, so ACC will see
        # them on the post-hoc re-read if we want batch mode).
        for evt in bb.read_all():
            acc.observe(evt)

        result = sandbox.run(spec)
        # Post-run: replay events into ACC so any deferred detections record.
        for evt in result.events:
            acc.observe(evt)

        bb.emit("finished")
        return RunHandle(run_id, intent, bb, budget, acc, result=result)

    def write_outcome(self, run: RunHandle, out_dir: Path) -> Path | None:
        if not run.result or not run.result.output:
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"plnt-{run.run_id}-{int(time.time())}.md"
        body = run.result.output.get("answer") or str(run.result.output)
        path.write_text(f"# Plnt run {run.run_id}\n\nIntent: {run.intent}\n\n{body}\n", encoding="utf-8")
        return path
