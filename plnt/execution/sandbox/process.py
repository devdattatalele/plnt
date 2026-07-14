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

# Files/dirs that mean "the agent ran tooling but produced no real work."
# An agent whose entire output is .npm/_logs is functionally silent — we
# treat it as such so the synth doesn't pretend it succeeded.
_LOG_ONLY_PREFIXES = (
    ".npm/_logs/",
    ".npm/_cacache/",
    ".npm/_update-notifier",
    ".cache/",
    ".local/share/pnpm-state/",
    ".yarn/cache/",
    "node_modules/.package-lock",
)

# Dirs we never want to enumerate when reporting "files written" — they
# blow up file counts to 20k+ and add no signal. The user cares about
# what their AGENT wrote, not what npm cached. These match by path segment
# (we skip the whole subtree).
_NOISE_DIR_NAMES = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
    "dist", "build", ".cache", ".pytest_cache", "target", ".turbo",
    ".npm", ".yarn", ".pnpm-store",
}


def _is_log_only_path(rel: str) -> bool:
    return any(rel.startswith(p) for p in _LOG_ONLY_PREFIXES)


def _scan_workdir_files(workdir: Path, max_files: int = 5000) -> list[str]:
    """List relative file paths in workdir, skipping noise subtrees.

    Manual walk (not rglob) so we can prune .git/node_modules/etc. and not
    spend seconds descending into 20k-file caches.
    """
    import os as _os
    out: list[str] = []
    for dirpath, dirnames, filenames in _os.walk(workdir):
        dirnames[:] = [d for d in dirnames if d not in _NOISE_DIR_NAMES]
        for fn in filenames:
            rel = _os.path.relpath(_os.path.join(dirpath, fn), workdir)
            out.append(rel)
            if len(out) >= max_files:
                return out
    return out


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

        # 1. Pick a workdir.
        #    If the agent's inputs specify output_dir, use it directly (the
        #    user wants persistent work, e.g. building a website at
        #    ~/portfolio-site). Otherwise: carve out a per-run persistent
        #    workdir under $PLNT_HOME/runs/<run>/work/<agent>/ so we never
        #    throw away what the agent wrote.
        from plnt.config import paths as _paths

        explicit_out = None
        tenant_id = None
        if isinstance(spec.inputs, dict):
            explicit_out = spec.inputs.get("output_dir") or spec.inputs.get("workdir")
            tenant_id = spec.inputs.get("tenant_id")

        if explicit_out:
            workdir = Path(str(explicit_out)).expanduser().resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            workdir_is_explicit = True
        else:
            # When a tenant context is present, partition the workdir tree by
            # tenant so cross-tenant filesystem reach is impossible by path
            # alone (defense-in-depth alongside `allowed_roots` enforcement
            # inside the runner's search/execute tools).
            if tenant_id:
                base = _paths().runs / "tenants" / str(tenant_id) / spec.run_id / "work" / spec.id
            else:
                base = _paths().runs / spec.run_id / "work" / spec.id
            workdir = base
            workdir.mkdir(parents=True, exist_ok=True)
            workdir_is_explicit = False

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
                    "workdir_persistent": True,
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

            # Capture the file manifest the agent created so the user can
            # see what work landed where. Prune noise subtrees (.git,
            # node_modules, .venv, etc.) so the file count means something
            # and the walk doesn't take 5 seconds in a real project.
            try:
                files_written = sorted(_scan_workdir_files(workdir))
                meaningful_files = [
                    p for p in files_written if not _is_log_only_path(p)
                ]
            except Exception:
                files_written = []
                meaningful_files = []

            # Truthful "done": if the agent finished cleanly but wrote nothing
            # the user can use, surface as silent so the synth treats it as
            # a failed agent rather than a successful one with no answer.
            silent = (
                not bool(self._kill_reason)
                and rc == 0
                and not meaningful_files
                and output is None
            )

            self.bb.emit(
                "finished",
                agent_id=spec.id,
                payload={
                    "exit_code": rc,
                    "wall_seconds": round(wall, 3),
                    "killed": bool(self._kill_reason),
                    "kill_reason": self._kill_reason,
                    "workdir": str(workdir),
                    "files_written": files_written[:50],
                    "file_count": len(files_written),
                    "meaningful_file_count": len(meaningful_files),
                    "silent": silent,
                    "status": (
                        "killed" if self._kill_reason
                        else "silent" if silent
                        else "ok"
                    ),
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
            # NEVER delete the workdir. The user wants to see what was built.
            # Cleanup is the user's job (or `rm -rf $PLNT_HOME/runs/...`).
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
