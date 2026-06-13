"""Skills — filesystem-backed, two formats supported.

Plnt v0.1: `<role>.md` with YAML-ish frontmatter (still works).
Plnt v0.2: `<role>/skill.toml + prompt.md` directory (recommended).

The registry probes both. Hot-reload by mtime.

The new format gives the triage layer real [requires] data so it can ask
intelligent clarifying questions; gives the synthesizer the output schema
so it can merge agent outputs properly; gives the planner a [graph] bound
on child spawns; and marks HTML-formatted output fields for the rich
storage / TUI rendering path.
"""

from __future__ import annotations

import re
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from plnt.config import paths
from plnt.control.skill_schema import SkillManifest


@dataclass
class Skill:
    """Runtime view — backed by either a SkillManifest or legacy frontmatter."""

    role: str
    prompt: str
    tools: list[str] = field(default_factory=lambda: ["search", "execute"])
    model_hint: str = "auto"
    budget: dict[str, int] = field(default_factory=dict)
    source_path: Path | None = None
    mtime: float = 0.0
    manifest: SkillManifest | None = None  # populated for new-format skills


# ---------------------------------------------------------------- parsing

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_legacy_md(role: str, text: str, source_path: Path | None = None, mtime: float = 0.0) -> Skill:
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


# Back-compat alias.
def parse_skill(role: str, text: str, source_path: Path | None = None, mtime: float = 0.0) -> Skill:
    return parse_legacy_md(role, text, source_path, mtime)


def parse_manifest_dir(role: str, dir_path: Path) -> Skill:
    """Parse the new `<role>/skill.toml + prompt.md` format."""
    toml_path = dir_path / "skill.toml"
    prompt_path = dir_path / "prompt.md"
    examples_path = dir_path / "examples.md"

    if not toml_path.exists():
        raise FileNotFoundError(f"missing skill.toml in {dir_path}")

    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    if "meta" not in data:
        data["meta"] = {}
    if "name" not in data["meta"]:
        data["meta"]["name"] = role

    try:
        manifest = SkillManifest.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"skill.toml validation failed for {role}: {e}") from e

    manifest.prompt = prompt_path.read_text(encoding="utf-8").strip() if prompt_path.exists() else ""
    manifest.examples = examples_path.read_text(encoding="utf-8").strip() if examples_path.exists() else ""

    mtime = max(
        toml_path.stat().st_mtime,
        prompt_path.stat().st_mtime if prompt_path.exists() else 0,
        examples_path.stat().st_mtime if examples_path.exists() else 0,
    )

    full_prompt = manifest.prompt
    if manifest.examples:
        full_prompt += "\n\n## Examples\n\n" + manifest.examples

    return Skill(
        role=role,
        prompt=full_prompt,
        tools=manifest.runtime.tools,
        model_hint=manifest.runtime.model_hint,
        budget={
            "tokens": manifest.budget.tokens,
            "wall_seconds": manifest.budget.wall_seconds,
            "joules": manifest.budget.joules,
        },
        source_path=toml_path,
        mtime=mtime,
        manifest=manifest,
    )


# ---------------------------------------------------------------- registry


class SkillRegistry:
    """Hot-reloading skill registry. Supports both v0.1 and v0.2 formats."""

    def __init__(self, skills_dir: Path | None = None):
        self.dir = skills_dir or paths().skills
        self._cache: dict[str, Skill] = {}
        self._lock = threading.Lock()

    def list(self) -> list[str]:
        if not self.dir.exists():
            return []
        roles: set[str] = set()
        for d in self.dir.iterdir():
            if d.is_dir() and (d / "skill.toml").exists():
                roles.add(d.name)
        for p in self.dir.glob("*.md"):
            roles.add(p.stem)
        return sorted(roles)

    def get(self, role: str) -> Skill | None:
        dir_path = self.dir / role
        toml_path = dir_path / "skill.toml"
        md_path = self.dir / f"{role}.md"

        path_to_load: Path | None
        loader: str
        if toml_path.exists():
            path_to_load, loader = toml_path, "manifest"
        elif md_path.exists():
            path_to_load, loader = md_path, "legacy"
        else:
            return None

        mtime = path_to_load.stat().st_mtime
        with self._lock:
            cached = self._cache.get(role)
            if cached and cached.mtime == mtime:
                return cached

        try:
            if loader == "manifest":
                sk = parse_manifest_dir(role, dir_path)
            else:
                text = path_to_load.read_text(encoding="utf-8")
                sk = parse_legacy_md(role, text, source_path=path_to_load, mtime=mtime)
        except Exception as e:
            import sys
            print(f"[skills] failed to load {role}: {e}", file=sys.stderr)
            return None

        with self._lock:
            self._cache[role] = sk
        return sk

    def must_get(self, role: str) -> Skill:
        sk = self.get(role)
        if sk is None:
            raise KeyError(f"no skill registered for role {role!r}")
        return sk
