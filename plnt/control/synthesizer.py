"""Synthesizer — reconciles the swarm's outputs into a user-facing answer.

Kimi's swarm: after sub-agents complete in parallel, a *coordinator* reads
the shared state and produces the deliverable. This is that step. One
small-model call that takes:

  - the original user intent,
  - the plan emitted by the planner,
  - every leaf agent's structured output,

…and returns one piece of clean text the user actually reads.

If the model can't be reached, we fall back to a deterministic concatenation
so the user sees *something* rather than nothing.
"""

from __future__ import annotations

import logging

from plnt.compute.router import LLMRouter

logger = logging.getLogger(__name__)


SYNTH_SYSTEM = """\
You are the Synthesizer inside Plnt's swarm runtime.

You receive:
  - the user's original intent,
  - the plan you delegated to specialist sub-agents,
  - a JSON dict of {agent_id: structured_output} with their findings.

You produce a SHORT, plain-text answer for the user. Style:
  - Direct. Lead with the answer.
  - When relevant, cite file paths and line numbers from the agents'
    findings — never invent sources.
  - If an agent failed or returned nothing useful, mention which one.
  - No headers, no bullet lists unless the user asked for one, no
    markdown code fences around the whole thing.
"""


def synthesize(
    intent: str,
    plan_text: str,
    outputs: dict[str, dict],
    router: LLMRouter | None = None,
) -> str:
    if not outputs:
        return "(no agents produced output)"

    router = router or LLMRouter()
    user_msg = _build_user(intent, plan_text, outputs)
    try:
        decision = router.step(
            system=SYNTH_SYSTEM,
            user=user_msg,
            tools=[],
            model_hint="small",
            raw=True,
        )
        text = (decision.text or "").strip()
        if text:
            return text
    except Exception as e:
        logger.warning("synth step failed: %s", e)

    # Fallback — concatenate the agents' answers verbatim.
    parts = []
    for aid, out in outputs.items():
        ans = out.get("answer") if isinstance(out, dict) else None
        if ans:
            parts.append(f"[{aid}] {ans}")
    if not parts:
        return "(agents returned no answers)"
    return "\n\n".join(parts)


def _build_user(intent: str, plan_text: str, outputs: dict[str, dict]) -> str:
    import json

    return (
        f"User intent: {intent}\n\n"
        f"Plan: {plan_text}\n\n"
        f"Agent outputs (JSON):\n{json.dumps(outputs, default=str, indent=2)[:8000]}"
    )
