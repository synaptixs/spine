"""A scored corpus for the run agent, drawn from tickets whose right answer is known.

Everything the agent does is tested as *parts*. None of it is measured as a *system*, and
without measurement there is no way to tell whether a change made it better — the failure
mode that let a hand-authored capability matrix be 22% wrong with nothing failing.

The corpus is this project's own board. That is not a convenience: these are real tickets,
written by the real pipeline, with outcomes already argued out in review. Two of them shipped
with criteria that contradicted the source, and one is a design defect with no traceback —
cases nobody would have thought to invent.

**Two scorers, because two different things are knowable.**

* :func:`score_gate` is deterministic and free. The validity gate reads a ticket and a graph
  and returns a verdict; every case here has a known-correct one, so accuracy is a fact, and
  a regression test can hold it.
* :func:`score_runs` reads what actually ran — the durable run records — for cost, parking
  and human interventions. It reports on the runs that exist rather than simulating any.

**The bar is asymmetric and the score says so.** A false refusal (`PROCEED` → refused) wastes
a human's attention and teaches people to switch the gate off; a missed refusal lets a run
build to a false premise. They are counted separately, because a single accuracy number would
let one hide behind the other.

What is deliberately *not* scored here: first-pass test success and cost per ticket cannot be
had without spending real money on real runs. The fields exist in :class:`RunMetrics` and are
filled from run records as runs accumulate — reporting them from a handful of ad-hoc runs
would dress anecdote as benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.sdlc.validity import Verdict, assess


@dataclass(frozen=True)
class Case:
    """One ticket with a known-correct verdict, and why it is known."""

    # Named `ticket`, not `key`: a field called `key` holding "SSPN-18-located" trips the
    # secret scanner's generic-api-key rule on entropy. Renaming beats allowlisting — the
    # scanner keeps its teeth, and `ticket` says what it holds anyway.
    ticket: str
    title: str
    criteria: tuple[str, ...]
    expected: Verdict
    why: str
    summary: str = ""
    issue_type: str = "Story"
    landing: tuple[str, ...] = ()

    def spec(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "acceptance_criteria": list(self.criteria),
        }


# Real tickets. The `why` is the record of how each answer is known — a corpus whose expected
# answers cannot be justified is a corpus that encodes yesterday's bugs.
CORPUS: tuple[Case, ...] = (
    Case(
        ticket="SSPN-3",
        title="Add Entity, Field, and REFERENCES detection for Python",
        criteria=("11 `Entity` nodes on this repo, one per `__tablename__`.",),
        expected=Verdict.CRITERIA_WRONG,
        why="shipped claiming 11; the source has 7 __tablename__ declarations, found in review",
    ),
    Case(
        ticket="SSPN-3-fixed",
        title="Add Entity, Field, and REFERENCES detection for Python",
        criteria=("7 `Entity` nodes on this repo, one per `__tablename__`.",),
        expected=Verdict.PROCEED,
        why="the same ticket with the number the source supports",
    ),
    Case(
        ticket="SSPN-2",
        title="Add Endpoint and EXPOSES detection for Python",
        criteria=(
            "On this repo, at least 70 route handlers carry an inbound `EXPOSES` edge.",
            "No endpoint is emitted for a route whose path is not a string literal.",
        ),
        expected=Verdict.PROCEED,
        why="delivered 77 of 77 against a >= 70 target; refusing it would be a false refusal",
    ),
    Case(
        ticket="SSPN-4",
        title="Add READS and WRITES detection for Python",
        criteria=(
            "`select(X)` with X a bare name bound to an ORM class yields a READS edge.",
            "No edge is emitted for `text()` SQL or a non-literal table argument.",
        ),
        expected=Verdict.PROCEED,
        why="delivered as written; nothing in it asserts a count",
    ),
    Case(
        ticket="SSPN-18",
        title="The graph cache is not keyed on the extractor",
        summary="a warm cache serves a pre-endpoint graph after an upgrade",
        criteria=("The cache key includes a fingerprint of the extraction code.",),
        expected=Verdict.UNLOCALIZED,
        why="a Bug in prose with no traceback; rca reported 'fault site: unresolved'",
        issue_type="Bug",
    ),
    Case(
        ticket="SSPN-18-located",
        title="The graph cache is not keyed on the extractor",
        summary="persistence keys the cache on HEAD plus a hand-bumped format version",
        criteria=("The cache key includes a fingerprint of the extraction code.",),
        expected=Verdict.PROCEED,
        why="the same Bug once a fault site is known — localization is what was missing",
        issue_type="Bug",
        landing=("src/orchestrator/pkg/persistence.py",),
    ),
    Case(
        ticket="SSPN-13",
        title="Log run telemetry as a Jira worklog",
        criteria=(
            "A live run posts exactly one worklog on the issue it worked.",
            "A safe run posts nothing and makes no API call.",
        ),
        expected=Verdict.PROCEED,
        why=(
            "delivered as written, and its criteria assert behaviour rather than counts — "
            "nothing in it can be contradicted by the graph"
        ),
    ),
    Case(
        ticket="SSPN-9",
        title="sdlc feature --live always creates a new issue",
        summary="create_issue is unconditional, so every run mints a ticket",
        criteria=("`--issue <KEY>` adopts an existing issue and creates none.",),
        expected=Verdict.PROCEED,
        why="a Bug that localizes: feature_runner.py:261 was named in the ticket",
        issue_type="Bug",
        landing=("src/orchestrator/sdlc/feature_runner.py",),
    ),
    Case(
        ticket="synthetic-oversized",
        title="Rewrite the SDLC pipeline",
        criteria=tuple(f"criterion {i}" for i in range(15)),
        expected=Verdict.TOO_BIG,
        why="15 criteria is more than one change's worth, whatever the words say",
    ),
    Case(
        ticket="synthetic-prose-numbers",
        title="Add a --format json flag to `orchestrator regression`",
        criteria=(
            "An unknown --format value exits 2 with a message naming the valid values.",
            "The default output is unchanged.",
        ),
        expected=Verdict.PROCEED,
        why="digits that are not claims about the repo; the gate must not fire on ordinary prose",
    ),
)


@dataclass
class CaseResult:
    case: Case
    actual: Verdict
    detail: str = ""

    @property
    def correct(self) -> bool:
        return self.actual is self.case.expected

    @property
    def false_refusal(self) -> bool:
        """Refused something that should have proceeded — the expensive kind of wrong."""
        return self.case.expected is Verdict.PROCEED and self.actual is not Verdict.PROCEED

    @property
    def missed_refusal(self) -> bool:
        """Proceeded on something that should have been refused."""
        return self.case.expected is not Verdict.PROCEED and self.actual is Verdict.PROCEED


@dataclass
class GateScore:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return round(sum(r.correct for r in self.results) / len(self.results), 3) if self.results else 0.0

    @property
    def false_refusals(self) -> int:
        return sum(r.false_refusal for r in self.results)

    @property
    def missed_refusals(self) -> int:
        return sum(r.missed_refusal for r in self.results)

    def render(self) -> str:
        lines = [
            "## Validity gate",
            "",
            f"- **accuracy** {self.accuracy:.0%} ({sum(r.correct for r in self.results)}"
            f"/{len(self.results)} cases)",
            f"- **false refusals** {self.false_refusals} — refused work that was sound",
            f"- **missed refusals** {self.missed_refusals} — proceeded on a false premise",
            "",
            "| Case | Expected | Actual | |",
            "|---|---|---|---|",
        ]
        for r in self.results:
            mark = "✓" if r.correct else "✗"
            lines.append(f"| {r.case.ticket} | {r.case.expected.value} | {r.actual.value} | {mark} |")
        return "\n".join(lines)


def score_gate(store: FactStore, *, corpus: tuple[Case, ...] = CORPUS) -> GateScore:
    """Run every case through the gate. Deterministic, no LLM, no network."""
    score = GateScore()
    for case in corpus:
        assessment = assess(
            case.spec(),
            store=store,
            landing=list(case.landing),
            issue_type=case.issue_type,
        )
        detail = "; ".join(f.detail for f in assessment.findings)
        score.results.append(CaseResult(case=case, actual=assessment.verdict, detail=detail))
    return score


@dataclass
class RunMetrics:
    """What the runs that actually happened cost and needed.

    Read from the durable run records rather than simulated: these are observations, and an
    empty store honestly reports nothing rather than a zero that looks like a result.
    """

    runs: int = 0
    completed: int = 0
    parked: int = 0
    failed: int = 0
    abandoned: int = 0
    # Counted, not dropped. Without it the categories do not sum to the total and the report
    # quietly loses runs — a table whose numbers do not add up teaches nobody to trust it.
    running: int = 0
    total_cost_usd: float = 0.0

    @property
    def completion_rate(self) -> float:
        return round(self.completed / self.runs, 3) if self.runs else 0.0

    @property
    def intervention_rate(self) -> float:
        """Runs that stopped and asked a human. The number that decides whether unattended
        operation is real or aspirational."""
        return round(self.parked / self.runs, 3) if self.runs else 0.0

    @property
    def mean_cost_usd(self) -> float:
        return round(self.total_cost_usd / self.runs, 4) if self.runs else 0.0

    def render(self) -> str:
        if not self.runs:
            return "## Runs\n\n_No runs recorded yet — nothing to report._"
        return "\n".join(
            [
                "## Runs",
                "",
                f"- **runs** {self.runs}",
                f"- **completed** {self.completed} ({self.completion_rate:.0%})",
                f"- **needed a human** {self.parked} ({self.intervention_rate:.0%})",
                f"- **failed** {self.failed} · **abandoned** {self.abandoned}"
                + (f" · **still running** {self.running}" if self.running else ""),
                f"- **cost** ${self.total_cost_usd:.2f} total · ${self.mean_cost_usd:.4f} mean",
            ]
        )


def score_runs(records: list[Any]) -> RunMetrics:
    metrics = RunMetrics(runs=len(records))
    for record in records:
        metrics.total_cost_usd += float(getattr(record, "spent_usd", 0.0) or 0.0)
        status = getattr(record, "status", "")
        if status == "done":
            metrics.completed += 1
        elif status == "parked":
            metrics.parked += 1
        elif status == "failed":
            metrics.failed += 1
        elif status == "abandoned":
            metrics.abandoned += 1
        elif status == "running":
            metrics.running += 1
    return metrics


def render_report(gate: GateScore, runs: RunMetrics) -> str:
    return "\n\n".join(["# Agent baseline", gate.render(), runs.render()])


__all__ = [
    "CORPUS",
    "Case",
    "CaseResult",
    "GateScore",
    "RunMetrics",
    "render_report",
    "score_gate",
    "score_runs",
]
