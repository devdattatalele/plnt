"""Process sandbox tests — spawn the real runner subprocess."""

from __future__ import annotations

import json
import sys
import time

from plnt.execution.blackboard import Blackboard
from plnt.execution.sandbox.process import ProcessSandbox
from plnt.execution.spec import AgentSpec, Budget


def test_sandbox_round_trip(isolated_home, tmp_path):
    # Seed a file the runner can search over.
    (tmp_path / "src.txt").write_text("plnt is the personal local native twin\n")
    bb = Blackboard("r-st")
    sandbox = ProcessSandbox(bb)
    spec = AgentSpec(
        role="general-helper",
        run_id="r-st",
        inputs={"intent": "find plnt", "search_roots": [str(tmp_path)], "max_steps": 2},
        budget=Budget(wall_seconds=30),
    )
    result = sandbox.run(spec)
    assert result.exit_code == 0
    # The runner must produce a result event.
    kinds = [e.get("kind") for e in result.events]
    assert "started" in kinds and "finished" in kinds


def test_sandbox_watchdog_kills_runaway(isolated_home, tmp_path):
    bb = Blackboard("r-wd")
    # Override the runner with a script that just sleeps.
    runner_cmd = [sys.executable, "-c", "import time, sys; sys.stdin.readline(); time.sleep(30)"]
    sandbox = ProcessSandbox(bb, runner_cmd=runner_cmd)
    spec = AgentSpec(
        role="x",
        run_id="r-wd",
        inputs={"max_steps": 1},
        budget=Budget(wall_seconds=2),
    )
    started = time.monotonic()
    result = sandbox.run(spec)
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"watchdog should have killed within ~2s, took {elapsed:.1f}s"
    assert result.killed
