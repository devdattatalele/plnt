"""Deterministic clarifier — composes follow-up questions from skill schemas.

History-aware: if the most recent assistant turn was already a clarification,
the user's current message is most likely the answer — DO NOT re-ask.

The LLM triage is good at deciding what KIND of task something is, but
small models are inconsistent at composing useful clarifying questions
and at recognising that a short reply is an answer to the prior question.
This module handles both deterministically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from plnt.control.skill_schema import RequiredInput, SkillManifest


@dataclass
class Clarification:
    text: str
    missing: list[str]


# --- history awareness --------------------------------------------------------


_QUESTION_RE = re.compile(r"\?")
_PATH_RE = re.compile(r"(~|/[A-Za-z0-9_./-]+)")


def last_assistant_message(history: list) -> str:
    """Return the most recent assistant ('plnt') reply, or empty string."""
    if not history:
        return ""
    for t in reversed(history):
        ans = getattr(t, "answer", "") if not isinstance(t, dict) else t.get("answer", "")
        if ans:
            return ans
    return ""


def assistant_was_clarifying(history: list) -> bool:
    """Heuristic: was the last assistant message a clarifying question?"""
    ans = last_assistant_message(history)
    if not ans:
        return False
    lowered = ans.lower()
    if "need a few things" in lowered or "could you share" in lowered:
        return True
    if "send those and i'll plan" in lowered:
        return True
    # Question mark + short follow-up phrasing.
    if "?" in ans and any(
        w in lowered for w in ["which", "what", "where", "do you", "is there", "would you"]
    ):
        return True
    return False


def collect_user_values(history: list, current_intent: str) -> dict:
    """Gather inputs the user has explicitly provided across the conversation.

    For now: detect filesystem paths anywhere in the conversation. Any path
    string discovered is treated as a satisfaction of any required input of
    type path/file/directory. This is intentionally permissive — the runner
    will validate paths at use time.
    """
    found: dict[str, str] = {}
    all_text = current_intent + " "
    for t in history or []:
        prompt = getattr(t, "prompt", "") if not isinstance(t, dict) else t.get("prompt", "")
        all_text += " " + prompt
    paths = _PATH_RE.findall(all_text)
    if paths:
        found["__has_path__"] = paths[0]
    return found


# --- compose questions --------------------------------------------------------


def clarification_for_manifest(
    manifest: SkillManifest,
    intent: str,
    history: list | None = None,
) -> Clarification | None:
    """Return a clarification, or None if no question is needed.

    Skips if:
      - the manifest has no required inputs, OR
      - the user has visibly provided every required input in the
        conversation so far, OR
      - the last assistant message was already a clarification (the
        current intent IS the answer; pass it through to the planner).
    """
    if not manifest.requires.inputs:
        return None

    history = history or []
    if assistant_was_clarifying(history):
        return None

    user_values = collect_user_values(history, intent)
    intent_l = (intent or "").lower()

    missing: list[RequiredInput] = []
    for req in manifest.requires.inputs:
        # Already provided in inputs?
        if req.name in user_values:
            continue
        # Path-type requirements satisfied if any path appears in the convo.
        if req.type in ("path", "file", "directory") and "__has_path__" in user_values:
            continue
        # Cheap textual presence — name as words or filesystem hints.
        name_tokens = {req.name.lower().replace("_", " ")}
        if req.type in ("path", "file", "directory"):
            name_tokens.update({"path", "dir", "directory", "file", "/"})
        if any(tok in intent_l for tok in name_tokens):
            continue
        missing.append(req)

    if not missing:
        return None

    return Clarification(text=_compose_question(manifest, missing), missing=[r.name for r in missing])


def _compose_question(manifest: SkillManifest, missing: list[RequiredInput]) -> str:
    role = manifest.role
    parts = [f"To get {role} started I need a few things:"]
    for i, req in enumerate(missing, 1):
        line = f"  {i}. {req.description or req.name}"
        if req.type in ("path", "file", "directory"):
            line += f" ({req.type})"
        if req.example:
            line += f" — e.g. {req.example}"
        parts.append(line)
    parts.append("Send those and I'll plan the work.")
    return "\n".join(parts)


# --- skill routing ------------------------------------------------------------


def first_match(intent: str, registry, history: list | None = None) -> SkillManifest | None:
    """Pick the most likely skill based on the full conversation context.

    Searches across the *current intent + recent history* so a follow-up
    answer doesn't get routed to a fresh skill just because a tag word
    appears in it. Returns the first skill (by stable order) whose name
    or tags appear anywhere in the combined text.
    """
    text = (intent or "").lower()
    for t in history or []:
        prompt = getattr(t, "prompt", "") if not isinstance(t, dict) else t.get("prompt", "")
        answer = getattr(t, "answer", "") if not isinstance(t, dict) else t.get("answer", "")
        text += " " + prompt.lower() + " " + answer.lower()

    best: tuple[int, SkillManifest] | None = None  # (count, manifest)
    for role in registry.list():
        sk = registry.get(role)
        if not sk or not sk.manifest:
            continue
        toks = [sk.manifest.meta.name.lower()]
        toks.extend(t.lower() for t in sk.manifest.meta.tags)
        score = sum(1 for t in toks if t and t in text)
        if score > 0 and (best is None or score > best[0]):
            best = (score, sk.manifest)
    return best[1] if best else None
