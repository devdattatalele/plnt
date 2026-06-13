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

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from plnt.control.acc import ACCMonitor
from plnt.control.budget import BudgetExceeded, BudgetGovernor, RunBudget
from plnt.control.parallel import ParallelOrchestrator
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
    # Single-spawn legacy field — populated by start_run.
    result: SandboxResult | None = None
    # Swarm path — populated by start_swarm.
    plan_text: str = ""
    results: list[SandboxResult] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.results is None:
            self.results = []


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

    def start_swarm(self, intent: str, history: list | None = None) -> RunHandle:
        """LLM-driven planner emits N AgentSpecs; fan out under one Blackboard."""
        run_id = f"r-{uuid.uuid4().hex[:10]}"
        return self.start_swarm_with_id(intent, run_id, history=history)

    def start_swarm_with_id(
        self,
        intent: str,
        run_id: str,
        blackboard: Blackboard | None = None,
        history: list | None = None,
    ) -> RunHandle:
        """Triage → (chat | one agent | DAG fan-out) → synthesize.

        - triage classifies the intent. "hi" returns kind=chat with a direct
          reply; no agents are spawned.
        - simple_task → one agent.
        - complex_task → planner emits a DAG; DAGExecutor runs it; synthesizer
          reconciles outputs into a single user-facing answer.
        """
        from plnt.control.dag import DAGExecutor
        from plnt.control.planner_llm import llm_planner
        from plnt.control.synthesizer import synthesize
        from plnt.control.triage import Turn as TriTurn
        from plnt.control.triage import triage

        bb = blackboard or Blackboard(run_id, root=self.runs_root)
        bb.emit("intent", payload={"text": intent})
        budget = BudgetGovernor(run_id, self.run_budget, blackboard=bb)
        acc = ACCMonitor()

        # Normalise history to TriTurn list (callers may pass dicts).
        tri_history: list[TriTurn] = []
        for t in history or []:
            if isinstance(t, TriTurn):
                tri_history.append(t)
            elif isinstance(t, dict):
                tri_history.append(TriTurn(prompt=t.get("prompt", ""), answer=t.get("answer", "")))

        bb.emit("triage_start")
        tri = triage(intent, history=tri_history)
        bb.emit("triage", payload={
            "kind": tri.kind,
            "reason": tri.reason,
            "estimated_agents": tri.estimated_agents,
            "missing_info": tri.missing_info,
        })

        # --- chat path: no swarm, just reply ----------------------------------
        if tri.kind == "chat":
            bb.emit("answer", payload={"text": tri.reply or "(no reply)", "source": "triage"})
            bb.emit("finished", payload={"spawned": 0, "completed": 0, "killed": 0})
            handle = RunHandle(run_id, intent, bb, budget, acc)
            handle.plan_text = "chat: replied directly without spawning agents"
            handle.results = []
            return handle

        # --- clarification path: ask the user before spawning ------------------
        if tri.kind == "needs_clarification":
            reply = tri.reply or "I need a bit more info before I can start. Could you share more detail?"
            bb.emit("answer", payload={"text": reply, "source": "clarify", "missing_info": tri.missing_info})
            bb.emit("finished", payload={"spawned": 0, "completed": 0, "killed": 0})
            handle = RunHandle(run_id, intent, bb, budget, acc)
            handle.plan_text = "clarify: asked the user for the missing info"
            handle.results = []
            return handle

        # --- complex path: full plan + DAG ------------------------------------
        if tri.kind == "complex_task":
            bb.emit("planner_start", payload={"intent": intent})
            specs = llm_planner(intent, self.skills, history=tri_history)
        else:
            # simple_task: one direct agent, no planner LLM call
            from plnt.control.planner_llm import _default_spec
            specs = [_default_spec(intent, self.skills)]

        specs = [s.model_copy(update={"run_id": run_id}) for s in specs]
        bb.emit("plan", payload={
            "agent_count": len(specs),
            "agents": [
                {"id": s.id, "role": s.role, "intent": s.inputs.get("intent", ""),
                 "depends_on": s.inputs.get("depends_on", [])}
                for s in specs
            ],
        })

        executor = DAGExecutor(bb, budget, acc)
        out = executor.run(specs)

        # Produce the user-facing answer.
        if out.outputs:
            if len(out.outputs) == 1 and tri.kind == "simple_task":
                # Single agent — use its answer verbatim. No need to round-trip
                # through the synthesizer for a one-shot reply.
                only = next(iter(out.outputs.values()))
                ans = only.get("answer") if isinstance(only, dict) else None
                if not ans:
                    ans = str(only)[:2000]
                bb.emit("answer", payload={"text": ans, "source": "agent"})
            else:
                bb.emit("synth_start")
                answer = synthesize(intent, "swarm", out.outputs)
                bb.emit("answer", payload={"text": answer, "source": "synth"})
        elif tri.kind != "chat":
            bb.emit("answer", payload={
                "text": "(the agent(s) produced no output — try a more specific prompt with a path to search)",
                "source": "fallback",
            })

        bb.emit("finished", payload={
            "spawned": out.spawned,
            "completed": out.completed,
            "killed": out.killed,
        })

        handle = RunHandle(run_id, intent, bb, budget, acc)
        handle.results = out.results
        return handle

    def write_outcome(self, run: RunHandle, out_dir: Path) -> Path | None:
        if os.environ.get("PLNT_WRITE_MD", "0") != "1":
            return None
        results = run.results or ([run.result] if run.result else [])
        if not results:
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"plnt-{run.run_id}-{int(time.time())}.md"
        lines = [f"# Plnt run {run.run_id}", "", f"Intent: {run.intent}", ""]
        for r in results:
            inner = (r.output or {}).get("output") or r.output or {}
            ans = inner.get("answer") if isinstance(inner, dict) else str(inner)
            lines.append(f"## {r.agent_id}")
            lines.append(str(ans or "(no answer)"))
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
