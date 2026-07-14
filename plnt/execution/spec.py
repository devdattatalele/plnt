"""AgentSpec — the only object that crosses Control -> Execution.

The planner LLM emits one of these per spawn. The execution plane validates
and runs it inside the chosen sandbox. Everything about a micro-agent's
identity, capability, budget, and output contract is here, and nowhere else.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from plnt.config import HARD_MAX_DEPTH, HARD_MAX_TOKENS_PER_SPAWN, HARD_MAX_WALL_SECONDS_PER_SPAWN

# RLM pattern (Agentnetes / MIT CSAIL): every micro-agent gets exactly two tools.
# Anything else is reachable *through* them — through the filesystem.
ALLOWED_TOOLS: set[str] = {"search", "execute"}

ISOLATION_RUNGS = ("process", "docker", "gvisor", "microvm", "wasm")
LIFETIMES = ("ephemeral", "resident")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class Budget(BaseModel):
    """Per-spawn hard ceilings. Hit any one -> spawn is killed."""

    tokens: int = Field(default=20_000, ge=100, le=HARD_MAX_TOKENS_PER_SPAWN)
    wall_seconds: int = Field(default=300, ge=1, le=HARD_MAX_WALL_SECONDS_PER_SPAWN)
    # Joules are an estimate — on edge devices, energy is real (Beyond Scaling).
    # 0 disables the check.
    joules: int = Field(default=0, ge=0)


class AgentSpec(BaseModel):
    """Declarative spec for one micro-agent spawn."""

    # Identity
    id: str = Field(default_factory=lambda: f"a-{uuid.uuid4().hex[:10]}")
    role: str  # maps to a skill bundle in /skills/<role>.md
    run_id: str  # which run does this spawn belong to
    parent_id: str | None = None  # spawn tree edge
    depth: int = Field(default=0, ge=0, le=HARD_MAX_DEPTH)

    # Capability
    lifetime: Literal["ephemeral", "resident"] = "ephemeral"
    isolation: Literal["process", "docker", "gvisor", "microvm", "wasm"] = "process"
    tools: list[str] = Field(default_factory=lambda: ["search", "execute"])

    # Inputs / outputs
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    blackboard_scope: str | None = None  # glob over run_ids the agent may read

    # Compute hints — the router decides; the planner only hints.
    model_hint: Literal["small", "deep", "auto"] = "auto"

    # Budget
    budget: Budget = Field(default_factory=Budget)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"id must match {_ID_RE.pattern}, got {v!r}")
        return v

    @field_validator("role")
    @classmethod
    def _role_format(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"role must match {_ID_RE.pattern}, got {v!r}")
        return v

    @field_validator("run_id")
    @classmethod
    def _run_id_format(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(f"run_id must match {_ID_RE.pattern}, got {v!r}")
        return v

    @field_validator("tools")
    @classmethod
    def _tools_subset(cls, v: list[str]) -> list[str]:
        unknown = [t for t in v if t not in ALLOWED_TOOLS]
        if unknown:
            raise ValueError(
                f"tools must be a subset of {sorted(ALLOWED_TOOLS)}; "
                f"got unknown: {unknown}. The RLM pattern says: search+execute, nothing more."
            )
        if not v:
            raise ValueError("at least one tool required")
        return v

    @model_validator(mode="after")
    def _resident_no_budget_explosion(self) -> "AgentSpec":
        # Resident agents must not request ephemeral budgets — they live forever
        # under the planner's running budget, not a per-spawn one.
        if self.lifetime == "resident" and self.budget.wall_seconds < 60:
            raise ValueError("resident agents need wall_seconds >= 60")
        return self
