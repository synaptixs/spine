"""The baseline the agent is held to.

This is the test that turns "the gate seems to work" into a number a change can regress
against. It runs the real gate over real tickets and a real graph — no stubs, because a
corpus scored against a fake is a measurement of the fake.

The bar is asymmetric on purpose and asserted that way: **zero false refusals** is
non-negotiable, because refusing sound work teaches people to switch the gate off, and a gate
that is switched off scores nothing at all.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.evals.agent_corpus import (
    CORPUS,
    render_report,
    score_gate,
    score_runs,
)
from orchestrator.pkg import FactStore, RepoCodeExtractor, load_or_extract
from orchestrator.sdlc.validity import Verdict

# The published baseline. Raise it when the gate genuinely improves; never lower it to make a
# change pass — that is the one edit this file exists to make uncomfortable.
BASELINE_ACCURACY = 1.0
BASELINE_FALSE_REFUSALS = 0
BASELINE_MISSED_REFUSALS = 0


@pytest.fixture(scope="module")
def repo_store() -> FactStore:
    """This repo's real graph — the corpus asserts counts against it (7 entities, 71 routes)."""
    root = Path(__file__).resolve().parents[2]
    return FactStore(load_or_extract(root, extractor=RepoCodeExtractor()))


def test_the_gate_meets_its_baseline(repo_store: FactStore) -> None:
    score = score_gate(repo_store)

    assert score.accuracy >= BASELINE_ACCURACY, score.render()
    assert score.false_refusals <= BASELINE_FALSE_REFUSALS, score.render()
    assert score.missed_refusals <= BASELINE_MISSED_REFUSALS, score.render()


def test_every_case_carries_its_justification() -> None:
    """A corpus whose expected answers cannot be justified encodes yesterday's bugs as truth."""
    for case in CORPUS:
        assert case.why.strip(), f"{case.ticket} has no recorded reason for its expected verdict"
        assert len(case.why) > 20, f"{case.ticket}'s justification is too thin to audit"


def test_the_corpus_covers_more_than_the_happy_path() -> None:
    """A corpus of tickets that should all proceed measures nothing about refusing."""
    expected = {case.expected for case in CORPUS}

    assert Verdict.PROCEED in expected
    assert Verdict.CRITERIA_WRONG in expected
    assert Verdict.UNLOCALIZED in expected
    assert Verdict.TOO_BIG in expected
    # And the two halves of the asymmetric bar are both represented.
    assert sum(c.expected is Verdict.PROCEED for c in CORPUS) >= 4
    assert sum(c.expected is not Verdict.PROCEED for c in CORPUS) >= 3


def test_the_paired_cases_differ_only_in_what_was_wrong(repo_store: FactStore) -> None:
    """SSPN-3 and SSPN-3-fixed are the same ticket with one number changed; SSPN-18 and
    SSPN-18-located the same bug with a fault site. If the gate scored the pairs the same,
    it would be reading something other than what we think."""
    results = {r.case.ticket: r.actual for r in score_gate(repo_store).results}

    assert results["SSPN-3"] is Verdict.CRITERIA_WRONG
    assert results["SSPN-3-fixed"] is Verdict.PROCEED
    assert results["SSPN-18"] is Verdict.UNLOCALIZED
    assert results["SSPN-18-located"] is Verdict.PROCEED


# ---- run metrics: observations, not simulations ----------------------------


def _record(status: str, cost: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(status=status, spent_usd=cost)


def test_run_metrics_count_every_run() -> None:
    """The categories must sum to the total. A report that quietly loses runs teaches nobody
    to trust it — an earlier version dropped `running` and showed 3 runs adding up to 1."""
    metrics = score_runs(
        [
            _record("done", 1.0),
            _record("parked", 0.5),
            _record("failed"),
            _record("abandoned"),
            _record("running"),
        ]
    )

    assert metrics.runs == 5
    assert (
        metrics.completed + metrics.parked + metrics.failed + metrics.abandoned + metrics.running
    ) == metrics.runs
    assert metrics.total_cost_usd == 1.5
    assert metrics.intervention_rate == 0.2


def test_no_runs_reports_nothing_rather_than_zero() -> None:
    """A 0% completion rate over no runs looks like a result. It is an absence."""
    metrics = score_runs([])

    assert metrics.runs == 0
    assert "No runs recorded yet" in metrics.render()


def test_the_report_names_both_halves(repo_store: FactStore) -> None:
    report = render_report(score_gate(repo_store), score_runs([_record("done", 2.0)]))

    assert "## Validity gate" in report and "## Runs" in report
    assert "false refusals" in report and "missed refusals" in report
