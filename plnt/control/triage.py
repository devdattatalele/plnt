"""Intent triage — decide whether to spawn agents at all.

Inspired by Kimi K2's pattern: tightly-coupled or trivial tasks get
*zero* sub-agents. Spawning many agents for "hi" is the worst possible
UX — coordination overhead with no parallelism payoff.

Triage runs the small model with a strict classification prompt and
returns one of:

  - chat          : greeting, small talk, factual Q&A the model can answer
                    directly. Plnt responds in one shot, no spawn.
  - simple_task   : one well-scoped action ("list files in X"). 1 agent.
  - complex_task  : multi-step, parallelizable, or genuinely composite.
                    Hand off to the planner for a real swarm.

If triage itself fails (no model, bad parse), we degrade to `simple_task`
so the user always gets *something*.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from plnt.compute.router import LLMRouter

logger = logging.getLogger(__name__)


TriageKind = Literal["chat", "simple_task", "complex_task"]


@dataclass
class TriageResult:
    kind: TriageKind
    reply: str = ""        # populated for kind=chat — the direct answer
    reason: str = ""       # short why
    estimated_agents: int = 0


TRIAGE_SYSTEM = """\
You triage user intents in the Plnt local-agent runtime.

For each intent, decide ONE category and respond with ONE JSON object only:

  - "chat" — greetings, small talk, casual questions, or factual questions
    you can answer directly without filesystem access or tools. Also
    include a "reply" field with the actual answer.

  - "simple_task" — one well-scoped operation that needs at most one
    specialist (e.g. "list files in X", "search for Y in Z"). One agent
    is enough.

  - "complex_task" — multi-step, composite, or research-style work that
    benefits from parallel specialists or sequential pipeline.

Schema:
  {"kind": "chat" | "simple_task" | "complex_task",
   "reply": "<answer text; only when kind=chat>",
   "reason": "<one sentence>",
   "estimated_agents": <0 for chat, 1 for simple, 2-5 for complex>}

Be honest about complexity. Avoid spawning agents when a direct reply
will do. "Hello", "what is 2+2", "thanks", "what's a kernel" → chat.
"""


def triage(intent: str, router: LLMRouter | None = None) -> TriageResult:
    router = router or LLMRouter()
    try:
        decision = router.step(
            system=TRIAGE_SYSTEM,
            user=intent,
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
        # No model output → assume simple to be safe and useful.
        return TriageResult(
            kind="simple_task",
            reason="triage unparseable → default simple",
        )

    kind = str(parsed.get("kind", "")).strip()
    if kind not in ("chat", "simple_task", "complex_task"):
        kind = "simple_task"

    return TriageResult(
        kind=kind,  # type: ignore[arg-type]
        reply=str(parsed.get("reply", "")).strip(),
        reason=str(parsed.get("reason", "")).strip(),
        estimated_agents=int(parsed.get("estimated_agents", 1) or 1),
    )


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
