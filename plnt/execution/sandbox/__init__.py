"""Sandbox primitives — the ladder.

rung 0   : process       (this v0, trusted-code default)
rung 0.5 : docker        (operational isolation: cpu/mem caps, monitoring, cleanup)
rung 1   : gvisor        (syscall isolation)
rung 2   : microvm       (Firecracker; LLM-emitted code)
rung 3   : wasm          (capability-first)

Pick from a registry keyed by the AgentSpec.isolation field.
"""

from __future__ import annotations

from plnt.execution.sandbox.base import Sandbox, SandboxResult
from plnt.execution.sandbox.process import ProcessSandbox

_REGISTRY: dict[str, type[Sandbox]] = {
    "process": ProcessSandbox,
}

# Docker is optional — only available if the `docker` python package is installed.
try:
    from plnt.execution.sandbox.docker import DockerSandbox  # noqa: F401

    _REGISTRY["docker"] = DockerSandbox
except ImportError:
    DockerSandbox = None  # type: ignore[assignment,misc]


def get_sandbox(isolation: str) -> type[Sandbox]:
    try:
        return _REGISTRY[isolation]
    except KeyError as e:
        rungs = sorted(_REGISTRY.keys())
        raise ValueError(
            f"isolation {isolation!r} not supported in this build. "
            f"available rungs: {rungs}. Build higher rungs when threat model demands it."
        ) from e


def available_rungs() -> list[str]:
    return sorted(_REGISTRY.keys())


__all__ = ["Sandbox", "SandboxResult", "ProcessSandbox", "get_sandbox", "available_rungs"]
