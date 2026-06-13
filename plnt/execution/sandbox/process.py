"""Process sandbox — rung 0 on the isolation ladder.

Spawns the agent runner as a subprocess inside a private tempdir, with
rlimits where the OS supports them. Communication is one JSON envelope on
stdin (the AgentSpec) and a JSONL event stream on stdout.

Threat model: TRUSTED CODE ONLY. Use this for personal-machine deployment.
For multi-tenant or LLM-emitted-code scenarios, climb to gVisor or microVM.
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from plnt.execution.blackboard import Blackboard
from plnt.execution.sandbox.base import SandboxResult
from plnt.execution.spec import AgentSpec


class ProcessSandbox:
    """Subprocess-based sandbox. One instance per agent spawn."""

    def __init__(self, blackboard: Blackboard, runner_cmd: list[str] | None = None):
        self.bb = blackboard
        # Default runner is `python -m plnt.execution.runner` — overridable for tests.
        self.runner_cmd = runner_cmd or [sys.executable, "-m", "plnt.execution.runner"]
        self._proc: subprocess.Popen | None = None
        self._kill_reason = ""
        self._kill_lock = threading.Lock()

    # ---------------------------------------------------------- lifecycle

    def run(self, spec: AgentSpec) -> SandboxResult:
        started = time.monotonic()

        # 1. Carve out an ephemeral workdir.
        workdir = Path(tempfile.mkdtemp(prefix=f"plnt-{spec.id}-"))
        try:
            self.bb.emit(
                "spawn",
                agent_id=spec.id,
                payload={
                    "role": spec.role,
                    "parent_id": spec.parent_id,
                    "depth": spec.depth,
                    "isolation": spec.isolation,
                    "tools": spec.tools,
                    "model_hint": spec.model_hint,
                    "workdir": str(workdir),
                },
            )

            env = self._build_env(spec, workdir)
            envelope = json.dumps(spec.model_dump(mode="json")) + "\n"

            # 2. Launch.
            self._proc = subprocess.Popen(
                self.runner_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=str(workdir),
                text=True,
                preexec_fn=self._apply_rlimits(spec) if sys.platform != "win32" else None,
            )
            assert self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(envelope)
            self._proc.stdin.close()

            # 3. Watchdog thread enforces wall_seconds.
            wd_stop = threading.Event()
            watchdog = threading.Thread(
                target=self._watchdog,
                args=(spec.budget.wall_seconds, wd_stop),
                daemon=True,
            )
            watchdog.start()

            # 4. Drain the runner's stdout into the blackboard.
            # The runner is a subprocess and has no direct handle on the
            # blackboard. Every event it emits on stdout is forwarded into
            # the run's events.jsonl here. This is the contract: the runner
            # emits, the sandbox persists.
            events_out: list[dict] = []
            output: dict | None = None
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    self.bb.emit("log", agent_id=spec.id, payload={"raw": line})
                    continue
                # Forward into the shared event log so `cat events.jsonl`
                # tells the whole story.
                self.bb.emit(
                    evt.get("kind", "log"),
                    agent_id=evt.get("agent_id") or spec.id,
                    payload=evt.get("payload"),
                )
                events_out.append(evt)
                if evt.get("kind") == "result":
                    output = evt.get("payload")

            # 5. Wait for exit.
            rc = self._proc.wait()
            wd_stop.set()

            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            if stderr.strip():
                self.bb.emit("log", agent_id=spec.id, payload={"stderr": stderr[:4000]})

            wall = time.monotonic() - started
            self.bb.emit(
                "finished",
                agent_id=spec.id,
                payload={
                    "exit_code": rc,
                    "wall_seconds": round(wall, 3),
                    "killed": bool(self._kill_reason),
                    "kill_reason": self._kill_reason,
                },
            )

            return SandboxResult(
                agent_id=spec.id,
                exit_code=rc,
                output=output,
                events=events_out,
                wall_seconds=wall,
                killed=bool(self._kill_reason),
                kill_reason=self._kill_reason,
            )

        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            self._proc = None

    # ----------------------------------------------------------- kill

    def kill(self, agent_id: str, reason: str) -> bool:
        with self._kill_lock:
            p = self._proc
            if p is None or p.poll() is not None:
                return False
            self._kill_reason = reason
            self.bb.emit("killed", agent_id=agent_id, payload={"reason": reason})
            try:
                p.terminate()
            except ProcessLookupError:
                return True
            # SIGTERM grace, then SIGKILL.
            for _ in range(50):  # 5s
                if p.poll() is not None:
                    return True
                time.sleep(0.1)
            try:
                p.kill()
            except ProcessLookupError:
                pass
            return True

    # ----------------------------------------------------------- helpers

    def _watchdog(self, wall_seconds: int, stop: threading.Event) -> None:
        deadline = time.monotonic() + wall_seconds
        while not stop.is_set():
            if time.monotonic() > deadline:
                self.kill("__watchdog__", f"wall_seconds budget {wall_seconds}s exceeded")
                return
            time.sleep(0.2)

    def _build_env(self, spec: AgentSpec, workdir: Path) -> dict[str, str]:
        # Pass declared search roots to the child so the offline echo planner
        # picks a useful default root.
        search_roots = spec.inputs.get("search_roots", []) if isinstance(spec.inputs, dict) else []
        roots_str = ":".join(str(r) for r in search_roots) if isinstance(search_roots, list) else ""
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(workdir),  # box the agent's $HOME inside the workdir
            "PLNT_AGENT_ID": spec.id,
            "PLNT_RUN_ID": spec.run_id,
            "PLNT_ROLE": spec.role,
            "PLNT_WORKDIR": str(workdir),
            "PLNT_BLACKBOARD_DIR": str(self.bb.dir),
            "PLNT_HOME": os.environ.get("PLNT_HOME", ""),
            "PLNT_SEARCH_ROOTS": roots_str,
        }
        # Pass through every PLNT_* env var so the child's backend_picker sees
        # the same configuration as the parent (local/cloud URLs, API keys,
        # required SSD path, model names, force overrides).
        for k, v in os.environ.items():
            if k.startswith("PLNT_") and k not in env:
                env[k] = v
        # Carry through anything the operator explicitly allow-lists.
        passthrough = os.environ.get("PLNT_ENV_PASSTHROUGH", "").split(",")
        for k in (k.strip() for k in passthrough if k.strip()):
            if k in os.environ:
                env[k] = os.environ[k]
        return env

    def _apply_rlimits(self, spec: AgentSpec):
        """Return a preexec_fn that applies POSIX rlimits to the child."""
        wall = max(1, spec.budget.wall_seconds + 5)

        def _set_limits():
            # CPU seconds — secondary defence to the watchdog wall clock.
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (wall, wall + 5))
            except (ValueError, OSError):
                pass
            # Address space cap — 2 GiB per spawn unless explicitly raised.
            try:
                # macOS doesn't honour RLIMIT_AS; that's fine, this is best-effort.
                resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
            except (ValueError, OSError):
                pass
            # Disallow new sessions / detached children.
            try:
                os.setpgrp()
            except OSError:
                pass

        return _set_limits
