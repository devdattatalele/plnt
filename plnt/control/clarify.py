"""Deterministic clarifier — composes follow-up questions from skill schemas.

The LLM triage is good at deciding what KIND of task something is, but
small models are inconsistent at composing useful clarifying questions.
When we know which skill is the likely match AND we can see what the
skill requires, we can compose a precise question from the schema —
no model call needed.

Used both as a fallback for the LLM triage and as a deterministic
'rule-based' first pass before triage runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from plnt.control.skill_schema import RequiredInput, SkillManifest


@dataclass
class Clarification:
    """Deterministic clarifying question + the missing fields."""

    text: str
    missing: list[str]


def clarification_for_manifest(
    manifest: SkillManifest,
    intent: str,
    user_inputs: dict | None = None,
) -> Clarification | None:
    """Return a clarification if any required input is missing.

    `user_inputs` is whatever the orchestrator has already extracted from
    the intent (free-form for v0.2; deterministic match against required
    names later). For now: missing fields = required fields where the
    name doesn't textually appear in the intent.
    """
    if not manifest.requires.inputs:
        return None
    user_inputs = user_inputs or {}
    intent_l = (intent or "").lower()

    missing: list[RequiredInput] = []
    for req in manifest.requires.inputs:
        if req.name in user_inputs:
            continue
        # Heuristic: if any of these tokens appear in the intent, assume the
        # user has already provided it. Cheap but useful for free-form text.
        name_tokens = {req.name.lower().replace("_", " ")}
        if req.type in ("path", "file", "directory"):
            name_tokens.update({"path", "dir", "directory", "file", "/"})
        if any(tok in intent_l for tok in name_tokens):
            continue
        missing.append(req)

    if not missing:
        return None

    text = _compose_question(manifest, missing)
    return Clarification(text=text, missing=[r.name for r in missing])


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


def first_match(intent: str, registry) -> SkillManifest | None:
    """Cheap keyword match: which skill mentions a noun from the intent?

    Used by triage when the LLM doesn't pin down a role. Returns the first
    skill (by stable order) whose tags or name appear in the intent.
    """
    lower = (intent or "").lower()
    for role in registry.list():
        sk = registry.get(role)
        if not sk or not sk.manifest:
            continue
        toks = [sk.manifest.meta.name.lower()]
        toks.extend(t.lower() for t in sk.manifest.meta.tags)
        if any(t and t in lower for t in toks):
            return sk.manifest
    return None
