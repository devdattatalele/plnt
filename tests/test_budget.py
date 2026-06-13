from __future__ import annotations

import pytest

from plnt.control.budget import BudgetExceeded, BudgetGovernor, RunBudget
from plnt.execution.spec import AgentSpec, Budget


def test_pre_flight_tokens():
    g = BudgetGovernor("r1", RunBudget(tokens=5000, wall_seconds=600))
    spec = AgentSpec(role="x", run_id="r1", budget=Budget(tokens=3000, wall_seconds=10))
    g.check_spawn(spec)  # ok
    g.tick_tokens(2500)
    with pytest.raises(BudgetExceeded) as ex:
        g.check_spawn(spec)
    assert ex.value.dim == "tokens"


def test_tick_kills_run():
    g = BudgetGovernor("r1", RunBudget(tokens=100, wall_seconds=600))
    g.tick_tokens(50)
    with pytest.raises(BudgetExceeded):
        g.tick_tokens(200)


def test_joules_disabled_by_default():
    g = BudgetGovernor("r1", RunBudget(joules=0))
    g.tick_joules(1e9)  # silent — joules=0 means unmetered
