"""Skills — markdown bundles loaded from disk, versioned by git.

A skill is one file: `<role>.md`. Front-matter (YAML-ish key:value lines until
`---`) declares default tools, model_hint, and budget; the body is the
system prompt. Hot-reloads on mtime change.

This is the *framework owns skills* rule from Beyond Scaling. The planner
does not invent system prompts; it picks a role, and the framework loads
the skill from disk.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plnt.config import paths


@dataclass
class Skill:
    role: str
    prompt: str
    tools: list[str] = field(default_factory=lambda: ["search", "execute"])
    model_hint: str = "auto"
    budget: dict[str, int] = field(default_factory=dict)
    source_path: Path | None = None
    mtime: float = 0.0


_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill(role: str, text: str, source_path: Path | None = None, mtime: float = 0.0) -> Skill:
    front: dict[str, Any] = {}
    body = text
    m = _FRONT_MATTER_RE.match(text)
    if m:
        front_text = m.group(1)
        body = text[m.end():]
        for line in front_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            front[k.strip()] = v.strip()

    tools = _parse_list(front.get("tools", "search,execute"))
    model_hint = front.get("model_hint", "auto")
    budget: dict[str, int] = {}
    for k in ("tokens", "wall_seconds", "joules"):
        if k in front:
            try:
                budget[k] = int(front[k])
            except ValueError:
                pass

    return Skill(
        role=role,
        prompt=body.strip(),
        tools=tools,
        model_hint=model_hint,
        budget=budget,
        source_path=source_path,
        mtime=mtime,
    )


def _parse_list(s: str) -> list[str]:
    return [t.strip() for t in s.split(",") if t.strip()]


class SkillRegistry:
    """Filesystem-backed skill registry with mtime-driven hot reload."""

    def __init__(self, skills_dir: Path | None = None):
        self.dir = skills_dir or paths().skills
        self._cache: dict[str, Skill] = {}
        self._lock = threading.Lock()

    def list(self) -> list[str]:
        if not self.dir.exists():
            return []
        return sorted(p.stem for p in self.dir.glob("*.md"))

    def get(self, role: str) -> Skill | None:
        path = self.dir / f"{role}.md"
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._cache.get(role)
            if cached and cached.mtime == mtime:
                return cached
            text = path.read_text(encoding="utf-8")
            sk = parse_skill(role, text, source_path=path, mtime=mtime)
            self._cache[role] = sk
            return sk

    def must_get(self, role: str) -> Skill:
        sk = self.get(role)
        if sk is None:
            raise KeyError(f"no skill registered for role {role!r}")
        return sk
