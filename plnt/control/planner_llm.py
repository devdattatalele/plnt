"""LLM-driven planner — turns one *complex* intent into a DAG of AgentSpecs.

Only called when triage classifies the intent as `complex_task`. The
planner emits a JSON plan with:

  - dynamic role names (not constrained to the installed skills),
  - explicit dependencies between agents (depends_on),
  - per-agent intents, model hints, search roots.

Roles unknown to the skill registry get a default persona that's still
constrained to the two-tool surface. This is Kimi's "InferenceStackResearcher,
QuantizationHardwareResearcher" pattern — names come from the task, not
a dropdown.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid

from plnt.compute.router import LLMRouter
from plnt.control.skills import Skill, SkillRegistry
from plnt.execution.spec import AgentSpec, Budget

logger = logging.getLogger(__name__)


PLANNER_SYSTEM = """\
You are the Planner inside Plnt, a local multi-agent runtime.

You decompose ONE complex user intent into a small graph of micro-agents.
Each agent is short-lived, sandboxed, has exactly search() and execute()
over allowed file paths, and produces structured output.

Respond with ONE JSON object only — no prose, no code fences:

{
  "plan": "<one sentence: what you're decomposing into and why>",
  "agents": [
    {
      "id": "<short kebab-case id, must be unique within this plan>",
      "role": "<descriptive role name; can be invented per task>",
      "intent": "<focused sub-intent, outcome-shaped>",
      "search_roots": ["<absolute path>", ...],
      "model_hint": "small" | "deep",
      "depends_on": ["<id of agent whose output this one needs>", ...]
    }
  ]
}

RULES:
- Emit between 1 and 5 agents. Use fewer if the task is tightly coupled
  or sequential. Critical Steps rule: more agents only helps if it
  shortens the slowest path.
- Use depends_on to chain agents whose output the next one needs.
  Parallel branches → empty depends_on. Sequential pipeline → chain.
- role names should be specific to the task (e.g. "log-grepper",
  "diff-summarizer") — NOT generic. Anything goes; the framework will
  give unknown roles a base persona.
- search_roots are absolute paths the agent can read. Use $HOME or the
  cwd if the user didn't name a path.
- Do not invent tools beyond search and execute.
- Final agent in a chain typically synthesizes the result for the user.
"""


def llm_planner(intent: str, registry: SkillRegistry, router: LLMRouter | None = None) -> list[AgentSpec]:
    """Return a list of AgentSpecs. Always non-empty."""
    router = router or LLMRouter()
    user_msg = _build_user_msg(intent, registry)

    try:
        decision = router.step(
            system=PLANNER_SYSTEM,
            user=user_msg,
            tools=[],
            model_hint="small",
            raw=True,
        )
        text = decision.text or ""
    except Exception as e:
        logger.warning("planner LLM step failed: %s", e)
        return [_default_spec(intent, registry)]

    plan = _extract_json(text)
    if not plan or "agents" not in plan or not isinstance(plan["agents"], list):
        logger.info("planner returned non-JSON; falling back: %r", text[:200])
        return [_default_spec(intent, registry)]

    specs: list[AgentSpec] = []
    seen_ids: set[str] = set()
    for raw in plan["agents"][:5]:
        if not isinstance(raw, dict):
            continue
        spec = _spec_from_plan(raw, intent, registry, seen_ids)
        if spec is None:
            continue
        specs.append(spec)
        seen_ids.add(spec.id)

    if not specs:
        return [_default_spec(intent, registry)]
    return specs


def _build_user_msg(intent: str, registry: SkillRegistry) -> str:
    lines = ["Available installed skills (you MAY use these role names, or invent new ones):"]
    for r in registry.list():
        sk = registry.get(r)
        if sk:
            head = sk.prompt.splitlines()[0] if sk.prompt else ""
            lines.append(f"- {r}: {head[:100]}")
    lines.append("")
    lines.append(f"User intent: {intent}")
    lines.append(f"HOME: {os.path.expanduser('~')}")
    lines.append(f"CWD:  {os.getcwd()}")
    return "\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _spec_from_plan(raw: dict, intent: str, registry: SkillRegistry, seen_ids: set[str]) -> AgentSpec | None:
    short_id = str(raw.get("id", "")).strip().lower()
    if not _ID_RE.match(short_id) or short_id in seen_ids:
        short_id = f"a-{uuid.uuid4().hex[:8]}"
    agent_id = f"a-{short_id}" if not short_id.startswith("a-") else short_id
    if not _ID_RE.match(agent_id):
        agent_id = f"a-{uuid.uuid4().hex[:8]}"

    role = str(raw.get("role", "")).strip() or "general-helper"
    role = _sanitize_role(role)
    sub_intent = str(raw.get("intent", "")).strip() or intent
    roots = raw.get("search_roots") or []
    if not isinstance(roots, list):
        roots = []
    roots = [str(r) for r in roots if isinstance(r, str)]
    hint = str(raw.get("model_hint", "small")).strip()
    if hint not in ("small", "deep", "auto"):
        hint = "small"
    depends_on = raw.get("depends_on") or []
    if not isinstance(depends_on, list):
        depends_on = []

    sk = registry.get(role)
    return _make_spec(agent_id, role, sub_intent, roots, hint, sk, depends_on)


def _sanitize_role(s: str) -> str:
    # turn "Log Grepper" → "log-grepper"; clamp length
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", s.strip().lower()).strip("-")
    if not s:
        return "general-helper"
    return s[:40]


def _make_spec(
    agent_id: str,
    role: str,
    intent: str,
    roots: list[str],
    hint: str,
    sk: Skill | None,
    depends_on: list[str],
) -> AgentSpec:
    prompt = sk.prompt if sk else _default_persona(role)
    return AgentSpec(
        id=agent_id,
        role=role,
        run_id=f"r-{uuid.uuid4().hex[:10]}",  # orchestrator overwrites
        depth=0,
        lifetime="ephemeral",
        isolation=os.environ.get("PLNT_DEFAULT_ISOLATION", "process"),  # type: ignore[arg-type]
        tools=sk.tools if sk else ["search", "execute"],
        inputs={
            "intent": intent,
            "search_roots": roots,
            "skill_prompt": prompt,
            "max_steps": 5,
            "depends_on": depends_on,
        },
        model_hint=hint,  # type: ignore[arg-type]
        budget=Budget(
            tokens=sk.budget.get("tokens", 12_000) if sk else 12_000,
            wall_seconds=sk.budget.get("wall_seconds", 180) if sk else 180,
            joules=sk.budget.get("joules", 0) if sk else 0,
        ),
    )


def _default_persona(role: str) -> str:
    """System prompt for an LLM-invented role with no installed skill."""
    return (
        f"You are {role}, a single-purpose micro-agent in a Plnt swarm.\n"
        "You have two tools: search(pattern, root) and execute(argv).\n"
        "Do exactly what your inputs.intent says and nothing more.\n"
        "If inputs.from_agents has outputs from prior agents, use them as ground truth.\n"
        "Cite paths and line numbers. Stop when you have a concrete answer."
    )


def _default_spec(intent: str, registry: SkillRegistry) -> AgentSpec:
    sk = registry.get("general-helper")
    return _make_spec(
        agent_id=f"a-{uuid.uuid4().hex[:8]}",
        role="general-helper",
        intent=intent,
        roots=[os.getcwd()],
        hint="small",
        sk=sk,
        depends_on=[],
    )
