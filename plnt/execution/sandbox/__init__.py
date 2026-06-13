"""Sandbox primitives — the ladder.

rung 0: process    (this v0)
rung 1: gvisor     (next)
rung 2: microvm    (Firecracker)
rung 3: wasm       (capability-first)

Pick from a registry keyed by the AgentSpec.isolation field.
"""

from __future__ import annotations

from plnt.execution.sandbox.base import Sandbox, SandboxResult
from plnt.execution.sandbox.process import ProcessSandbox

_REGISTRY: dict[str, type[Sandbox]] = {
    "process": ProcessSandbox,
}


def get_sandbox(isolation: str) -> type[Sandbox]:
    try:
        return _REGISTRY[isolation]
    except KeyError as e:
        rungs = sorted(_REGISTRY.keys())
        raise ValueError(
            f"isolation {isolation!r} not supported in this build. "
            f"available rungs: {rungs}. Build higher rungs when threat model demands it."
        ) from e


__all__ = ["Sandbox", "SandboxResult", "ProcessSandbox", "get_sandbox"]
