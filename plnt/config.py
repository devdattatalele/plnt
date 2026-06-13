"""Plnt paths and runtime defaults.

Everything mutable lives under $PLNT_HOME (default ~/.plnt). Inside it:
  runs/<run_id>/        — per-run blackboard (events.jsonl + artifacts/)
  skills/*.md           — versioned-by-git skill bundles
  episodic/YYYY/MM/     — long-term memory (append-only JSONL)
  index/                — derived semantic index (rebuildable)
  identity.toml         — planner identity & preferences
  sockets/              — Unix sockets for sandbox IPC
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _home() -> Path:
    raw = os.environ.get("PLNT_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".plnt"


@dataclass(frozen=True)
class PlntPaths:
    home: Path

    @property
    def runs(self) -> Path:
        return self.home / "runs"

    @property
    def skills(self) -> Path:
        return self.home / "skills"

    @property
    def episodic(self) -> Path:
        return self.home / "episodic"

    @property
    def index(self) -> Path:
        return self.home / "index"

    @property
    def sockets(self) -> Path:
        return self.home / "sockets"

    @property
    def identity_file(self) -> Path:
        return self.home / "identity.toml"

    def ensure(self) -> None:
        for d in (self.home, self.runs, self.skills, self.episodic, self.index, self.sockets):
            d.mkdir(parents=True, exist_ok=True)


def paths() -> PlntPaths:
    return PlntPaths(home=_home())


# Runtime defaults — overridable via env.
DEFAULT_SURFACE_HOST = os.environ.get("PLNT_SURFACE_HOST", "127.0.0.1")
DEFAULT_SURFACE_PORT = int(os.environ.get("PLNT_SURFACE_PORT", "7777"))
DEFAULT_COMPUTE_URL = os.environ.get("PLNT_COMPUTE_URL", "http://127.0.0.1:11434")  # ollama
DEFAULT_PLANNER_MODEL = os.environ.get("PLNT_PLANNER_MODEL", "llama3.2:3b")
DEFAULT_DEEP_MODEL = os.environ.get("PLNT_DEEP_MODEL", "llama3.1:8b")

# Hard budget caps — last line of defense against runaway swarms.
HARD_MAX_TOKENS_PER_SPAWN = 200_000
HARD_MAX_WALL_SECONDS_PER_SPAWN = 1800
HARD_MAX_DEPTH = 3  # planner → specialist → ephemeral. Anything deeper smells like a fork bomb.
