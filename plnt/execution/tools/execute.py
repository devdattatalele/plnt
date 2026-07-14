"""execute() — bounded shell.

Runs `argv` in the agent's workdir with hard wall/output ceilings. There is
no shell interpolation: callers pass argv as a list. If the agent wants a
pipeline, it writes a script to the workdir and executes the script — that
puts the dangerous string under the search() tool's view, which is exactly
what the audit story needs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExecuteResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    wall_seconds: float
    truncated: bool


class ExecuteError(Exception):
    pass


# Block-list — substring match on `argv[0]`. Belt-and-suspenders only; real
# isolation belongs to the sandbox rung above this tool.
_HARD_BLOCKED_BINS = {"sudo", "su", "doas"}


def execute(
    argv: list[str],
    *,
    workdir: Path,
    allowed_roots: list[Path],
    timeout_seconds: int = 30,
    max_output_bytes: int = 256 * 1024,
    env: dict[str, str] | None = None,
) -> ExecuteResult:
    if not argv:
        raise ExecuteError("argv must be non-empty")
    bin_name = Path(argv[0]).name
    if bin_name in _HARD_BLOCKED_BINS:
        raise ExecuteError(f"binary {bin_name!r} is hard-blocked")

    # Resolve workdir within an allowed root.
    wd = Path(workdir).resolve()
    if not any(str(wd).startswith(str(r.resolve())) for r in allowed_roots):
        raise ExecuteError(f"workdir {wd} is not inside any allowed root")

    real_env = _safe_env(wd)
    if env:
        real_env.update(env)

    import time

    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(wd),
            env=real_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        wall = time.monotonic() - started
        out, err = proc.stdout, proc.stderr
        truncated = False
        if len(out) > max_output_bytes:
            out = out[:max_output_bytes]
            truncated = True
        if len(err) > max_output_bytes:
            err = err[:max_output_bytes]
            truncated = True
        return ExecuteResult(
            argv=argv,
            exit_code=proc.returncode,
            stdout=out,
            stderr=err,
            wall_seconds=wall,
            truncated=truncated,
        )
    except subprocess.TimeoutExpired:
        return ExecuteResult(
            argv=argv,
            exit_code=124,
            stdout="",
            stderr=f"execute() timed out after {timeout_seconds}s",
            wall_seconds=time.monotonic() - started,
            truncated=False,
        )
    except FileNotFoundError as e:
        raise ExecuteError(f"binary not found: {argv[0]}") from e


def which(bin_name: str) -> str | None:
    return shutil.which(bin_name)


# Non-interactive env. Inspired by phoenix-os bash.py — npm/git/apt/etc.
# block waiting for stdin (license prompts, "use defaults? Y/n", auth) and
# never recover inside a subprocess. We set the well-known opt-out vars so
# tools either auto-yes or fail fast instead of hanging until the watchdog.
def _safe_env(workdir: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workdir),
        "LC_ALL": "C.UTF-8",
        "CI": "1",
        "NONINTERACTIVE": "1",
        "FORCE_COLOR": "0",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "npm_config_yes": "true",
        "npm_config_fund": "false",
        "npm_config_audit": "false",
        "npm_config_update_notifier": "false",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONUNBUFFERED": "1",
        "DEBIAN_FRONTEND": "noninteractive",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/true",
    }
