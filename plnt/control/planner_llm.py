"""LLM-driven planner — turns one intent into N AgentSpecs.

The keyword-router was a placeholder. The real planner asks the model:
"given this intent and these skills, what set of micro-agents should I spawn?"
The model returns a JSON array; we validate each entry into an AgentSpec and
the orchestrator fans them out.

The planner uses the *small* model — it's a routing decision, not the work.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any

from plnt.compute.router import LLMRouter
from plnt.control.skills import Skill, SkillRegistry
from plnt.execution.spec import AgentSpec, Budget

logger = logging.getLogger(__name__)


PLANNER_SYSTEM = """\
You are the Planner inside the Plnt personal-twin runtime.

Given a user intent and the available specialist skills, you decide which
micro-agents to spawn. Each micro-agent is short-lived, runs in a sandbox,
and has exactly two tools: search() and execute() over allowed file paths.

You respond with ONE JSON object on a single line — nothing before, nothing
after, no code fences. Schema:

{
  "plan": "<one sentence: why these agents and what each does>",
  "agents": [
    {
      "role": "<one of the available skill roles>",
      "intent": "<the focused sub-intent for this agent>",
      "search_roots": ["<absolute path>", ...],
      "model_hint": "small" | "deep" | "auto"
    },
    ...
  ]
}

Rules:
- Emit 1 to 5 agents. Use multiple when the work has independent sub-tasks.
  Use one when the task is naturally serial.
- Choose roles ONLY from the list of available skills you are given.
- Paths in search_roots must be absolute and likely to exist; if the user
  did not name a path, use a sensible default like $HOME or the cwd token.
- Keep each sub-intent short, concrete, and outcome-shaped.
- Do not invent tools, roles, or capabilities.
"""


def llm_planner(intent: str, registry: SkillRegistry, router: LLMRouter | None = None) -> list[AgentSpec]:
    """Return a list of AgentSpecs to fan out. Always non-empty."""
    roles = registry.list()
    if not roles:
        # No skills installed → ship a single safe default so the user sees
        # *something* land. Real installs always have at least general-helper.
        return [_default_spec(intent)]

    router = router or LLMRouter()
    user_msg = _build_user_msg(intent, registry, roles)

    try:
        decision = router.step(
            system=PLANNER_SYSTEM,
            user=user_msg,
            tools=[],
            model_hint="small",
            raw=True,  # planner needs raw JSON, not parsed TOOL/FINAL
        )
        text = decision.text or ""
    except Exception as e:
        logger.warning("planner LLM step failed: %s", e)
        return [_default_spec(intent, registry)]

    plan = _extract_json(text)
    if not plan or "agents" not in plan or not isinstance(plan["agents"], list):
        logger.info("planner returned non-JSON or no agents; falling back: %r", text[:200])
        return [_default_spec(intent, registry)]

    specs: list[AgentSpec] = []
    for raw in plan["agents"][:5]:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role", "")).strip() or "general-helper"
        if role not in roles:
            role = "general-helper" if "general-helper" in roles else roles[0]
        sub_intent = str(raw.get("intent", "")).strip() or intent
        roots = raw.get("search_roots") or []
        if not isinstance(roots, list):
            roots = []
        roots = [str(r) for r in roots if isinstance(r, str)]
        hint = str(raw.get("model_hint", "auto")).strip()
        if hint not in ("small", "deep", "auto"):
            hint = "auto"

        sk = registry.get(role)
        specs.append(_make_spec(role, sub_intent, roots, hint, sk))

    if not specs:
        specs.append(_default_spec(intent, registry))
    return specs


def _build_user_msg(intent: str, registry: SkillRegistry, roles: list[str]) -> str:
    lines = ["Available skills:"]
    for r in roles:
        sk = registry.get(r)
        if sk:
            head = sk.prompt.splitlines()[0] if sk.prompt else ""
            lines.append(f"- {r}: {head[:120]}")
    lines.append("")
    lines.append(f"Intent from the user: {intent}")
    lines.append(f"User HOME: {os.path.expanduser('~')}")
    lines.append(f"User CWD:  {os.getcwd()}")
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        # strip a leading fence and optional language token
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _make_spec(role: str, intent: str, roots: list[str], hint: str, sk: Skill | None) -> AgentSpec:
    return AgentSpec(
        role=role,
        run_id=f"r-{uuid.uuid4().hex[:10]}",  # orchestrator overwrites
        depth=0,
        lifetime="ephemeral",
        isolation=os.environ.get("PLNT_DEFAULT_ISOLATION", "process"),  # type: ignore[arg-type]
        tools=sk.tools if sk else ["search", "execute"],
        inputs={
            "intent": intent,
            "search_roots": roots,
            "skill_prompt": sk.prompt if sk else None,
            "max_steps": 6,
        },
        model_hint=hint,  # type: ignore[arg-type]
        budget=Budget(
            tokens=sk.budget.get("tokens", 20_000) if sk else 20_000,
            wall_seconds=sk.budget.get("wall_seconds", 300) if sk else 300,
            joules=sk.budget.get("joules", 0) if sk else 0,
        ),
    )


def _default_spec(intent: str, registry: SkillRegistry | None = None) -> AgentSpec:
    sk = registry.get("general-helper") if registry else None
    return _make_spec(
        role="general-helper",
        intent=intent,
        roots=[os.getcwd()],
        hint="small",
        sk=sk,
    )
