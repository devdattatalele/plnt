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
from pathlib import Path  # noqa: F401 (used in string annotations)

from plnt.compute.router import LLMRouter
from plnt.control.skills import Skill, SkillRegistry
from plnt.execution.spec import AgentSpec, Budget

logger = logging.getLogger(__name__)


PLANNER_SYSTEM = """\
You are the Planner inside Plnt, a local multi-agent runtime.

You decompose ONE complex user intent into a small graph of micro-agents.
Every agent in this plan operates in the SAME shared WORKDIR (a single
project directory). Sibling agents see each other's files via the
filesystem — that's how a "scaffolder" can hand a project off to an
"add-navbar" agent.

Each agent has TWO tools:

  search(pattern, root)   — grep/find under allowed paths.
  execute(argv)           — run a shell command in the WORKDIR. argv is a
                            JSON array of strings, e.g.
                              ["npm","create","vite@latest",".","--","--template","react"]
                            Powerful: mkdir, cat, cp, mv, rm, git, npm,
                            pnpm, yarn, pip, uv, curl, wget, python, node,
                            and anything else on the host PATH.

CRITICAL PATH RULE:
- Do NOT include absolute paths like /Users/... or /home/... in execute()
  argv. Your cwd is ALREADY the workdir. Use "." or relative paths.
- For scaffolders, prefer "npm create vite@latest . --yes" (the dot means
  "scaffold here"). Never pass an absolute target path.

CRITICAL SEQUENCING RULE:
- If a step PRODUCES the project (scaffolder, init, bootstrap, create-app)
  and other steps EDIT it (add-navbar, add-route, write-page), the editors
  MUST list the producer in depends_on. Parallel race-conditions on an
  empty dir produce empty output.

Use the conversation context if it's provided — the user may have
answered an earlier clarifying question or the previous turn may have
created files in this WORKDIR. Build on what's already there.

Respond with ONE JSON object only — no prose, no code fences:

{
  "plan": "<one sentence: what you're decomposing into and why>",
  "agents": [
    {
      "id": "<short kebab-case id, must be unique within this plan>",
      "role": "<descriptive role name; specific to this task>",
      "intent": "<focused sub-task outcome, written as a goal>",
      "model_hint": "small" | "deep",
      "depends_on": ["<id of upstream agent>", ...]
    }
  ]
}

RULES:
- 1–6 agents. Independent branches in parallel; depends_on chains when
  output of one feeds the next.
- Role names task-specific: "vite-scaffolder", "navbar-writer",
  "homepage-writer", "deploy-helper", "review-critic".
- For construction tasks (build / scaffold / deploy / set up X), most
  agents rely on execute(), not search().
- Don't invent tools beyond search and execute.
"""


def llm_planner(
    intent: str,
    registry: SkillRegistry,
    router: LLMRouter | None = None,
    history: list | None = None,
    project_dir: "Path | str | None" = None,
) -> list[AgentSpec]:
    """Return a list of AgentSpecs. Always non-empty."""
    router = router or LLMRouter()
    user_msg = _build_user_msg(intent, registry, history or [], project_dir)

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

    # Cap the swarm tightly. On a small CPU model 3 parallel agents is
    # already the practical ceiling — beyond that the model server queues
    # and total wall-clock balloons. The cap is configurable for users on
    # bigger hardware via PLNT_MAX_AGENTS.
    import os as _os
    max_agents = int(_os.environ.get("PLNT_MAX_AGENTS", "3"))

    specs: list[AgentSpec] = []
    seen_ids: set[str] = set()
    for raw in plan["agents"][:max_agents]:
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


def _build_user_msg(
    intent: str,
    registry: SkillRegistry,
    history: list | None,
    project_dir: "Path | str | None" = None,
) -> str:
    lines: list[str] = []
    if history:
        lines.append("Recent conversation (oldest first):")
        for t in (history or [])[-6:]:
            p = getattr(t, "prompt", "") or (t.get("prompt", "") if isinstance(t, dict) else "")
            a = getattr(t, "answer", "") or (t.get("answer", "") if isinstance(t, dict) else "")
            if p:
                lines.append(f"  user: {p}")
            if a:
                ans = a if len(a) <= 300 else a[:297] + "…"
                lines.append(f"  plnt: {ans}")
        lines.append("")
    lines.append("Skills available on disk (you may invent new role names too):")
    for r in registry.list():
        sk = registry.get(r)
        if sk:
            head = sk.prompt.splitlines()[0] if sk.prompt else ""
            lines.append(f"- {r}: {head[:100]}")
    lines.append("")
    lines.append(f"Latest user intent: {intent}")
    if project_dir is not None:
        lines.append(f"WORKDIR (every agent's cwd): {project_dir}")
        # Show what's already in the workdir so the planner can build on it
        # instead of re-scaffolding. This is the session-continuity hook.
        try:
            from pathlib import Path as _P
            wd = _P(str(project_dir))
            if wd.exists():
                existing = sorted(
                    p.name for p in wd.iterdir() if not p.name.startswith(".")
                )[:20]
                if existing:
                    lines.append(f"WORKDIR contents: {', '.join(existing)}")
                else:
                    lines.append("WORKDIR contents: (empty)")
        except OSError:
            pass
    lines.append(f"HOME: {os.path.expanduser('~')}")
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
    # Fail-fast budget: a 3B-class model on CPU does ~5-20s per turn. 4 steps
    # × 20s × 1.5x slack = 120s. Don't let a hung agent eat 15 minutes.
    fast_wall = int(os.environ.get("PLNT_AGENT_WALL_SECONDS", "120"))
    fast_steps = int(os.environ.get("PLNT_AGENT_MAX_STEPS", "4"))
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
            "max_steps": fast_steps,
            "depends_on": depends_on,
        },
        model_hint=hint,  # type: ignore[arg-type]
        budget=Budget(
            tokens=sk.budget.get("tokens", 12_000) if sk else 12_000,
            wall_seconds=min(sk.budget.get("wall_seconds", fast_wall) if sk else fast_wall, fast_wall),
            joules=sk.budget.get("joules", 0) if sk else 0,
        ),
    )


def _default_persona(role: str) -> str:
    """System prompt for an LLM-invented role with no installed skill."""
    return (
        f"You are {role}, a single-purpose micro-agent in a Plnt swarm.\n"
        "Tools:\n"
        "  search(pattern, root) — grep/find under allowed paths\n"
        "  execute(argv)        — run shell. mkdir, ls, cat, cp, mv, rm,\n"
        "                         git, npm, pnpm, curl, wget, python, node,\n"
        "                         and any other host CLI work. You CAN build\n"
        "                         and modify files, scaffold projects, init\n"
        "                         repos, install packages.\n\n"
        "Do what inputs.intent says. If inputs.from_agents includes outputs\n"
        "from upstream agents, use them as ground truth.\n\n"
        "Be concrete: prefer doing > talking. When the intent is a build or\n"
        "setup task, USE execute to make it happen — don't just describe.\n"
        "When done, return one short paragraph explaining what you did,\n"
        "what worked, and what the user should check or run next."
    )


def _default_spec(
    intent: str,
    registry: SkillRegistry,
    project_dir: "Path | str | None" = None,
) -> AgentSpec:
    sk = registry.get("general-helper")
    roots = [str(project_dir)] if project_dir is not None else [os.getcwd()]
    return _make_spec(
        agent_id=f"a-{uuid.uuid4().hex[:8]}",
        role="general-helper",
        intent=intent,
        roots=roots,
        hint="small",
        sk=sk,
        depends_on=[],
    )
