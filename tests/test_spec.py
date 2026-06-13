from __future__ import annotations

import pytest

from plnt.execution.spec import AgentSpec, Budget


def test_minimum_valid_spec():
    s = AgentSpec(role="literature-scout", run_id="r-abc123")
    assert s.tools == ["search", "execute"]
    assert s.isolation == "process"
    assert s.lifetime == "ephemeral"
    assert s.depth == 0


def test_unknown_tool_rejected():
    with pytest.raises(ValueError, match="search\\+execute"):
        AgentSpec(role="x", run_id="r-1", tools=["search", "rm_rf"])


def test_empty_tools_rejected():
    with pytest.raises(ValueError, match="at least one tool"):
        AgentSpec(role="x", run_id="r-1", tools=[])


def test_bad_id_rejected():
    with pytest.raises(ValueError):
        AgentSpec(id="UPPER", role="x", run_id="r-1")


def test_resident_min_wall():
    with pytest.raises(ValueError, match="wall_seconds >= 60"):
        AgentSpec(role="x", run_id="r-1", lifetime="resident", budget=Budget(wall_seconds=10))


def test_depth_capped():
    with pytest.raises(ValueError):
        AgentSpec(role="x", run_id="r-1", depth=99)


def test_budget_hard_ceiling():
    with pytest.raises(ValueError):
        AgentSpec(role="x", run_id="r-1", budget=Budget(tokens=10_000_000))
