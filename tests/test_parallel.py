"""Parallel orchestrator tests — fan out N agents under one BB/budget/ACC."""

from __future__ import annotations

from plnt.control.acc import ACCMonitor
from plnt.control.budget import BudgetGovernor, RunBudget
from plnt.control.parallel import ParallelOrchestrator, default_cap
from plnt.execution.blackboard import Blackboard
from plnt.execution.spec import AgentSpec, Budget


def test_default_cap_sensible():
    n = default_cap()
    assert 1 <= n <= 8


def test_fan_out_three(isolated_home, tmp_path, monkeypatch):
    monkeypatch.setenv("PLNT_REQUIRED_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("PLNT_CLOUD_URL", raising=False)
    monkeypatch.delenv("PLNT_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("PLNT_LOCAL_URL", "http://127.0.0.1:1")
    (tmp_path / "src.txt").write_text("plnt twin alpha\n")
    bb = Blackboard("r-fan")
    budget = BudgetGovernor("r-fan", RunBudget(tokens=100_000, wall_seconds=120))
    acc = ACCMonitor()
    po = ParallelOrchestrator(bb, budget, acc, max_concurrency=3)

    specs = [
        AgentSpec(
            role="general-helper",
            run_id="r-fan",
            inputs={"intent": f"find thing {i}", "search_roots": [str(tmp_path)], "max_steps": 2},
            budget=Budget(wall_seconds=30),
        )
        for i in range(3)
    ]
    result = po.fan_out(specs)

    assert result.spawned == 3
    assert result.completed == 3
    assert result.killed == 0
    # All three should have produced a `started` and a `finished` event
    events = bb.read_all()
    started = [e for e in events if e["kind"] == "started"]
    finished = [e for e in events if e["kind"] == "finished" and e.get("payload", {}).get("exit_code") is not None]
    assert len(started) == 3
    assert len(finished) == 3
