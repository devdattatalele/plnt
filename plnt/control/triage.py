"""Intent triage — decide whether to spawn, ask back, or chat.

Four outcomes:

  - chat                : casual reply, no work needed (greetings, factual Qs).
  - needs_clarification : task-shaped, but critical info is missing.
                          Reply asks for what's needed before any spawn.
  - simple_task         : one well-scoped step, 1 agent.
  - complex_task        : multi-step construction or research → planner DAG.

Triage receives the recent conversation so it can recognise "they're
answering my prior question" and shift to a real plan.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from plnt.compute.router import LLMRouter

logger = logging.getLogger(__name__)


TriageKind = Literal["chat", "needs_clarification", "simple_task", "complex_task"]


@dataclass
class Turn:
    """One past exchange — what the user asked, what we said."""

    prompt: str = ""
    answer: str = ""


@dataclass
class TriageResult:
    kind: TriageKind
    reply: str = ""        # populated for chat / needs_clarification
    reason: str = ""
    estimated_agents: int = 0
    missing_info: list[str] = field(default_factory=list)


TRIAGE_SYSTEM = """\
You triage user intents in Plnt — a local multi-agent runtime that can:
  - read and search files,
  - run shell commands (git, npm, mkdir, curl, etc.),
  - spawn multiple specialist sub-agents in parallel.

You DO have memory of the recent conversation. Use it. If the user is
answering a question you asked in the last turn, move on to the real plan
— don't ask again.

For each user message, classify into ONE of:

  "chat"
    Casual reply, no work needed. Greetings, thanks, definitional Qs.
    You must also include a "reply" field with the answer.

  "needs_clarification"
    The user wants something done, but you genuinely cannot start without
    more info. Include "reply" that asks the smallest number of pointed
    questions to unblock you. Be helpful — propose defaults the user can
    accept. Also fill "missing_info" with short labels.
    DO NOT pick this if the conversation already gave you enough info.

  "simple_task"
    One well-scoped operation. One sub-agent will run.

  "complex_task"
    Real construction or research that benefits from 2-6 specialist agents
    in parallel or in a pipeline. Building a portfolio website (frontend
    scaffold + template search + git init + deployment etc.) is complex.
    Multi-source research is complex.

Respond with ONE JSON object only, no prose, no code fences:

  {
    "kind": "chat" | "needs_clarification" | "simple_task" | "complex_task",
    "reply": "<answer for chat OR clarifying question for needs_clarification>",
    "missing_info": ["<short labels>", ...],
    "reason": "<one sentence>",
    "estimated_agents": <integer; 0 for chat/clarification, 1 for simple, 2-6 for complex>
  }

Rules:
- Prefer needs_clarification over guessing — but ONLY when missing info
  would force the agent to produce nothing useful.
- If the user gave a project goal AND you have at least an output dir OR
  the cwd, treat it as complex_task and let the planner ask agents to ask
  more later if needed.
- For known-incomplete tasks like "build a portfolio site" with no
  resume/dir/stack mentioned: needs_clarification with a short list of
  practical questions and proposed defaults.
"""


def triage(
    intent: str,
    history: list[Turn] | None = None,
    router: LLMRouter | None = None,
) -> TriageResult:
    router = router or LLMRouter()
    user_msg = _build_user(intent, history or [])
    try:
        decision = router.step(
            system=TRIAGE_SYSTEM,
            user=user_msg,
            tools=[],
            model_hint="small",
            raw=True,
        )
        text = (decision.text or "").strip()
    except Exception as e:
        logger.warning("triage step failed: %s", e)
        return TriageResult(kind="simple_task", reason="triage error → default simple")

    parsed = _extract_json(text)
    if not parsed:
        return TriageResult(kind="simple_task", reason="triage unparseable → default simple")

    kind = str(parsed.get("kind", "")).strip()
    if kind not in ("chat", "needs_clarification", "simple_task", "complex_task"):
        kind = "simple_task"

    missing = parsed.get("missing_info") or []
    if not isinstance(missing, list):
        missing = []
    missing = [str(x) for x in missing if isinstance(x, str)]

    reply = str(parsed.get("reply", "")).strip()

    # Code-level fix-up: small models often emit a clarifying question while
    # still classifying as simple/complex. If the reply looks like a question
    # and missing_info is non-empty, treat as needs_clarification — that's
    # what the model wanted anyway.
    looks_like_question = "?" in reply
    if kind in ("simple_task", "complex_task") and looks_like_question and missing:
        kind = "needs_clarification"

    return TriageResult(
        kind=kind,  # type: ignore[arg-type]
        reply=reply,
        reason=str(parsed.get("reason", "")).strip(),
        estimated_agents=int(parsed.get("estimated_agents", 1) or 1),
        missing_info=missing,
    )


def _build_user(intent: str, history: list[Turn]) -> str:
    parts: list[str] = []
    if history:
        parts.append("Recent conversation (oldest first):")
        for t in history[-6:]:  # last 6 turns max
            if t.prompt:
                parts.append(f"  user: {t.prompt}")
            if t.answer:
                ans = t.answer if len(t.answer) <= 300 else t.answer[:297] + "…"
                parts.append(f"  plnt: {ans}")
        parts.append("")
    parts.append(f"Latest user message: {intent}")
    return "\n".join(parts)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


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
