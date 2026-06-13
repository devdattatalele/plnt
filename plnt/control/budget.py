"""Budget governor — meters spend across a run and per-spawn.

The governor reads the run's event log and rolls up tokens, wall time, and
energy estimates. It exposes a `check(spec)` for the orchestrator to call
before each spawn, and `tick(spec)` for the orchestrator to call as spawns
emit `model_result` events. When a hard ceiling is hit, the orchestrator
kills the offender and refuses further spawns under that run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from plnt.execution.blackboard import Blackboard
from plnt.execution.spec import AgentSpec


@dataclass
class RunBudget:
    """Whole-run ceilings. Defaults match a generous personal-machine budget."""

    tokens: int = 500_000
    wall_seconds: int = 3600
    joules: int = 0  # 0 disables


@dataclass
class Spend:
    tokens: int = 0
    wall_seconds: float = 0.0
    joules: float = 0.0
    started_at: float = field(default_factory=time.monotonic)


class BudgetExceeded(Exception):
    def __init__(self, dim: str, limit: float, spent: float):
        super().__init__(f"budget exceeded: {dim} spent {spent} > limit {limit}")
        self.dim = dim
        self.limit = limit
        self.spent = spent


class BudgetGovernor:
    """One governor per run."""

    def __init__(self, run_id: str, budget: RunBudget | None = None, blackboard: Blackboard | None = None):
        self.run_id = run_id
        self.budget = budget or RunBudget()
        self.spend = Spend()
        self.bb = blackboard

    # ----- pre-flight ----------------------------------------------------

    def check_spawn(self, spec: AgentSpec) -> None:
        """Raise BudgetExceeded if the proposed spawn cannot fit."""
        # Token-side: would this spawn's hard cap push us over?
        if self.spend.tokens + spec.budget.tokens > self.budget.tokens:
            raise BudgetExceeded(
                "tokens",
                self.budget.tokens,
                self.spend.tokens + spec.budget.tokens,
            )
        # Wall-side: would this spawn fit before the run's wall ceiling?
        elapsed = time.monotonic() - self.spend.started_at
        if elapsed + spec.budget.wall_seconds > self.budget.wall_seconds:
            raise BudgetExceeded("wall_seconds", self.budget.wall_seconds, elapsed)

    # ----- in-flight -----------------------------------------------------

    def tick_tokens(self, n: int) -> None:
        self.spend.tokens += n
        if self.bb:
            self.bb.emit("budget_tick", payload={"dim": "tokens", "delta": n, "total": self.spend.tokens})
        if self.spend.tokens > self.budget.tokens:
            raise BudgetExceeded("tokens", self.budget.tokens, self.spend.tokens)

    def tick_joules(self, j: float) -> None:
        self.spend.joules += j
        if self.budget.joules and self.spend.joules > self.budget.joules:
            raise BudgetExceeded("joules", self.budget.joules, self.spend.joules)

    # ----- snapshot ------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.spend.started_at

    def snapshot(self) -> dict[str, float]:
        return {
            "tokens": float(self.spend.tokens),
            "tokens_limit": float(self.budget.tokens),
            "wall_seconds": round(self.elapsed, 3),
            "wall_limit": float(self.budget.wall_seconds),
            "joules": self.spend.joules,
            "joules_limit": float(self.budget.joules),
        }
