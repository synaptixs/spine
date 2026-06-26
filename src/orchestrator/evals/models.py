"""Persona-agnostic eval data model (Bet 1, phase 1a).

A task is whatever a persona can be asked to do (`payload` is arm-specific). An
*arm* runs it and returns an ``ArmOutcome`` — accepted-or-not plus the honest
metrics (cost, wall-clock, convergence, whether a human gate would have had to
step in, and the failure mode when rejected). Repeats per task make
nondeterminism visible rather than hidden.

The same model scores the SWE codegen loop *and* a future auditor persona — the
harness only knows tasks, arms, and outcomes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvalTask:
    """One unit of work to evaluate. ``payload`` is interpreted by the arm."""

    id: str
    category: str  # arm-defined label for breakdown, e.g. "create"/"edit"/"audit"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArmOutcome:
    """The result of running one task through one arm, once."""

    accepted: bool
    cost_usd: float = 0.0
    wall_clock_s: float = 0.0
    iterations: int = 0  # loop / refine steps taken
    intervened: bool = False  # would a human gate have had to edit/redo?
    failure_mode: str | None = None  # e.g. "parse" | "anchor" | "test" | "fit"
    detail: str = ""


@dataclass
class TaskResult:
    """A task's outcomes across K repeats (variance lives here)."""

    task: EvalTask
    outcomes: list[ArmOutcome]

    @property
    def repeats(self) -> int:
        return len(self.outcomes)

    @property
    def accepted_count(self) -> int:
        return sum(1 for o in self.outcomes if o.accepted)

    @property
    def acceptance_rate(self) -> float:
        return self.accepted_count / self.repeats if self.outcomes else 0.0

    @property
    def flaky(self) -> bool:
        """Accepted on some repeats but not all — nondeterminism worth surfacing."""
        return 0 < self.accepted_count < self.repeats

    @property
    def mean_cost(self) -> float:
        return statistics.fmean(o.cost_usd for o in self.outcomes) if self.outcomes else 0.0

    @property
    def mean_iterations(self) -> float:
        return statistics.fmean(o.iterations for o in self.outcomes) if self.outcomes else 0.0


@dataclass
class Scorecard:
    """Aggregate of an eval run — the honest numbers."""

    arm: str
    model: str
    results: list[TaskResult]

    def metrics(self) -> dict[str, Any]:
        outcomes = [o for r in self.results for o in r.outcomes]
        n = len(outcomes)
        accepted = sum(1 for o in outcomes if o.accepted)
        by_category: dict[str, dict[str, int]] = {}
        for r in self.results:
            cat = by_category.setdefault(r.task.category, {"accepted": 0, "total": 0})
            cat["accepted"] += r.accepted_count
            cat["total"] += r.repeats
        return {
            "arm": self.arm,
            "model": self.model,
            "tasks": len(self.results),
            "repeats": self.results[0].repeats if self.results else 0,
            "runs": n,
            "acceptance_rate": round(accepted / n, 3) if n else 0.0,
            "accepted": accepted,
            "flaky_tasks": sum(1 for r in self.results if r.flaky),
            "mean_cost_usd": round(statistics.fmean(o.cost_usd for o in outcomes), 4) if n else 0.0,
            "total_cost_usd": round(sum(o.cost_usd for o in outcomes), 4),
            "mean_iterations": round(statistics.fmean(o.iterations for o in outcomes), 2) if n else 0.0,
            "intervention_rate": round(sum(1 for o in outcomes if o.intervened) / n, 3) if n else 0.0,
            "by_category": {k: f"{v['accepted']}/{v['total']}" for k, v in by_category.items()},
            "failure_modes": _tally(o.failure_mode for o in outcomes if not o.accepted),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics(),
            "tasks": [
                {
                    "id": r.task.id,
                    "category": r.task.category,
                    "acceptance": f"{r.accepted_count}/{r.repeats}",
                    "flaky": r.flaky,
                    "mean_cost_usd": round(r.mean_cost, 4),
                    "mean_iterations": round(r.mean_iterations, 2),
                    "outcomes": [
                        {
                            "accepted": o.accepted,
                            "cost_usd": round(o.cost_usd, 4),
                            "iterations": o.iterations,
                            "intervened": o.intervened,
                            "failure_mode": o.failure_mode,
                            "detail": o.detail,
                        }
                        for o in r.outcomes
                    ],
                }
                for r in self.results
            ],
        }


def _tally(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        key = v or "unknown"
        out[key] = out.get(key, 0) + 1
    return out


__all__ = ["ArmOutcome", "EvalTask", "Scorecard", "TaskResult"]
