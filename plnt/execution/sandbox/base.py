"""Sandbox protocol — what every isolation rung must implement."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from plnt.execution.spec import AgentSpec


@dataclass
class SandboxResult:
    """What comes out of a finished spawn."""

    agent_id: str
    exit_code: int
    output: dict[str, Any] | None = None  # parsed final "result" event payload
    events: list[dict[str, Any]] = field(default_factory=list)
    wall_seconds: float = 0.0
    killed: bool = False
    kill_reason: str = ""


class Sandbox(Protocol):
    """Run one AgentSpec to completion (or to a kill).

    Implementations MUST:
      - emit a `started` event on the run's blackboard before any user code runs,
      - emit `tool_call`/`tool_result`/`log`/`model_call`/`model_result` events
        as the agent does its work,
      - emit either a `result` event with structured output OR an `error` event,
      - emit a `finished` event when done (success, error, kill — all cases).

    Implementations MUST NOT:
      - allow the agent to read/write outside its declared scope,
      - allow the agent to spawn deeper than HARD_MAX_DEPTH,
      - leak file descriptors or zombie processes when killed.
    """

    def run(self, spec: AgentSpec) -> SandboxResult: ...

    def kill(self, agent_id: str, reason: str) -> bool: ...
