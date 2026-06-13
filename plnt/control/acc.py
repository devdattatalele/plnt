"""ACC — Anterior Cingulate Cortex.

A framework-level conflict-monitor/loop-detector, named after Beyond Scaling
(arXiv:2605.18535). It reads the run's event stream and looks for three
failure modes that small local models reliably get wrong:

  1. Tool-call loop — the same (tool, args) called repeatedly.
  2. Fan-out blowup — agents spawning agents past the depth cap.
  3. Planner ↔ specialist ping-pong — alternating identical handoffs.

When it detects one, it calls back to the orchestrator to kill the offending
agent. The ACC is a *signal*, not a policy — what to do is the orchestrator's
choice.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable

from plnt.config import HARD_MAX_DEPTH


@dataclass
class Detection:
    kind: str          # "loop" | "fanout" | "pingpong"
    agent_id: str      # who to kill
    reason: str        # human-readable
    score: float       # confidence in [0, 1]


KillFn = Callable[[str, str], bool]  # (agent_id, reason) -> killed?


class ACCMonitor:
    """Streaming monitor. Feed events; get detections; let the kill_fn act."""

    LOOP_THRESHOLD = 3   # same tool call N times → loop
    PINGPONG_THRESHOLD = 4

    def __init__(self, kill_fn: KillFn | None = None, max_depth: int = HARD_MAX_DEPTH):
        self.kill_fn = kill_fn
        self.max_depth = max_depth
        # per-agent tool-call signatures
        self._sigs: dict[str, Counter[str]] = defaultdict(Counter)
        # spawn-tree depth per agent
        self._depth: dict[str, int] = {}
        # handoff log: list of (from_agent, to_role) per run
        self._handoffs: list[tuple[str, str]] = []

    def observe(self, evt: dict) -> list[Detection]:
        """Process one event; return detections (possibly empty)."""
        kind = evt.get("kind")
        agent = evt.get("agent_id") or ""
        payload = evt.get("payload") or {}
        out: list[Detection] = []

        if kind == "spawn":
            depth = int(payload.get("depth", 0))
            self._depth[agent] = depth
            if depth > self.max_depth:
                out.append(
                    Detection(
                        kind="fanout",
                        agent_id=agent,
                        reason=f"depth {depth} > max {self.max_depth}",
                        score=1.0,
                    )
                )
            parent = payload.get("parent_id") or ""
            role = payload.get("role") or ""
            if parent and role:
                self._handoffs.append((parent, role))
                if self._pingpong_detected():
                    out.append(
                        Detection(
                            kind="pingpong",
                            agent_id=parent,
                            reason="alternating identical handoffs",
                            score=0.85,
                        )
                    )

        elif kind == "tool_call":
            sig = self._sig(payload.get("tool", ""), payload.get("args"))
            self._sigs[agent][sig] += 1
            if self._sigs[agent][sig] >= self.LOOP_THRESHOLD:
                out.append(
                    Detection(
                        kind="loop",
                        agent_id=agent,
                        reason=f"identical tool call repeated {self._sigs[agent][sig]}x",
                        score=0.95,
                    )
                )

        # Act on detections.
        if self.kill_fn:
            for d in out:
                self.kill_fn(d.agent_id, f"ACC:{d.kind}:{d.reason}")
        return out

    # ----- helpers --------------------------------------------------------

    @staticmethod
    def _sig(tool: str, args) -> str:
        try:
            blob = json.dumps({"t": tool, "a": args}, sort_keys=True, default=str)
        except TypeError:
            blob = f"{tool}::{args!r}"
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def _pingpong_detected(self) -> bool:
        if len(self._handoffs) < self.PINGPONG_THRESHOLD:
            return False
        last = self._handoffs[-self.PINGPONG_THRESHOLD:]
        # Alternating pattern: (A,b), (B,a), (A,b), (B,a)
        return len({last[i] for i in (0, 2)}) == 1 and len({last[i] for i in (1, 3)}) == 1
