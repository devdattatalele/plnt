"""Blackboard — the per-run JSONL event bus.

Every observable thing — spawn, tool call, tool result, log, error, result,
budget tick, kill — lands here as one JSON line. `tail -f` is the debugger.
`grep`, `jq`, and `wc -l` are the metrics stack. There is no other source of
truth for a run.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from plnt.config import paths

EventKind = Literal[
    "intent",          # surface received a user intent
    "spawn",           # control plane spawned a micro-agent
    "started",         # micro-agent process started
    "tool_call",       # agent invoked search/execute
    "tool_result",     # tool returned
    "log",             # free-form log line from anywhere
    "model_call",      # compute plane called the LLM
    "model_result",    # LLM responded (tokens, latency)
    "budget_tick",     # governor sampled spend
    "result",          # agent returned its final structured output
    "error",           # agent or framework errored
    "killed",          # ACC/budget governor killed the agent
    "finished",        # agent finished (success or otherwise)
]


class Blackboard:
    """Append-only JSONL log + artifacts dir for one run.

    Thread-safe. Single-process writers — multi-process safety relies on the
    OS append-write guarantee for lines smaller than PIPE_BUF (~4KB on macOS,
    ~4KB on Linux). Events are budgeted accordingly; payloads larger than a
    threshold spill to artifacts/.
    """

    SPILL_THRESHOLD = 2_000  # chars

    def __init__(self, run_id: str, root: Path | None = None):
        self.run_id = run_id
        base = root or paths().runs
        self.dir = base / run_id
        self.events_path = self.dir / "events.jsonl"
        self.artifacts_dir = self.dir / "artifacts"
        self._lock = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        # Touch the events file so SSE subscribers don't race-404 the path
        # before the first emit lands.
        self.events_path.touch(exist_ok=True)

    # ----- writer -----

    def emit(
        self,
        kind: EventKind,
        *,
        agent_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one event. Returns the event dict written."""
        evt: dict[str, Any] = {
            "ts": time.time(),
            "run_id": self.run_id,
            "kind": kind,
        }
        if agent_id is not None:
            evt["agent_id"] = agent_id
        if payload:
            payload = self._spill_if_large(kind, payload)
            evt["payload"] = payload

        line = json.dumps(evt, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            # Open per-write so concurrent processes also writing to this file
            # see each other's bytes flushed.
            with open(self.events_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        return evt

    def _spill_if_large(self, kind: EventKind, payload: dict[str, Any]) -> dict[str, Any]:
        """Spill large fields to artifacts/ to keep events.jsonl grep-able."""
        out: dict[str, Any] = {}
        for k, v in payload.items():
            s = v if isinstance(v, str) else json.dumps(v, default=str)
            if isinstance(s, str) and len(s) > self.SPILL_THRESHOLD:
                fname = f"{kind}-{int(time.time() * 1000)}-{k}.txt"
                (self.artifacts_dir / fname).write_text(s, encoding="utf-8")
                out[k] = {"_spilled": fname, "_bytes": len(s)}
            else:
                out[k] = v
        return out

    # ----- reader -----

    def read_all(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(self.events_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # Don't let one bad line corrupt a read. Surface as a
                    # synthetic error event so the caller knows.
                    out.append({"kind": "error", "payload": {"reason": "bad jsonl line"}})
        return out

    def tail(self, offset: int = 0, poll: float = 0.1) -> Iterator[dict[str, Any]]:
        """Yield events from byte `offset` forever. Cancel via the caller's loop."""
        while True:
            if self.events_path.exists():
                with open(self.events_path, encoding="utf-8") as f:
                    f.seek(offset)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        offset = f.tell()
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            time.sleep(poll)

    # ----- artifacts -----

    def write_artifact(self, name: str, data: bytes | str) -> Path:
        path = self.artifacts_dir / name
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(path, mode) as f:
            f.write(data)
        return path

    def read_artifact(self, name: str) -> bytes:
        return (self.artifacts_dir / name).read_bytes()
