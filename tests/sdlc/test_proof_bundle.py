"""The facts a PR's proof bundle may state — what the run recorded, and what it did not."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult
from orchestrator.core.llm.recording import TokenLedger
from orchestrator.sdlc.baseline import BaselineCheck
from orchestrator.sdlc.proof_bundle import (
    BaselineFacts,
    CostFacts,
    JudgeFacts,
    RiskFacts,
    assess_risk,
    enabled,
)
from orchestrator.sdlc.review import CriterionVerdict, ReviewResult

# ---- the switch ------------------------------------------------------------------------------


def test_it_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_PROOF_BUNDLE", raising=False)
    assert enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "off", "no", " 0 "])
def test_the_off_values_turn_it_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SDLC_PROOF_BUNDLE", value)
    assert enabled() is False


def test_any_other_value_leaves_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_PROOF_BUNDLE", "1")
    assert enabled() is True


# ---- the baseline ----------------------------------------------------------------------------


def test_a_baseline_never_taken_says_so() -> None:
    assert BaselineFacts.not_taken() == BaselineFacts(state="not_taken", failures=())


def test_a_clean_baseline_is_not_a_baseline_never_taken() -> None:
    facts = BaselineFacts.from_check(BaselineCheck(frozenset()))
    assert facts.state == "clean" and facts.failures == ()


def test_pre_existing_failures_are_kept_sorted() -> None:
    facts = BaselineFacts.from_check(BaselineCheck(frozenset({"tests/b.py", "tests/a.py"})))
    assert facts.state == "preexisting"
    assert facts.failures == ("tests/a.py", "tests/b.py")


def test_an_unreadable_baseline_is_not_read_as_no_failures() -> None:
    """`problems=None` is "the pre-change run could not be read" — rendering it as "nothing
    failed before" would credit the change with a clean suite nobody saw."""
    facts = BaselineFacts.from_check(BaselineCheck(None))
    assert facts.state == "unreadable" and facts.failures == ()


# ---- the judge -------------------------------------------------------------------------------


def test_judge_facts_carry_the_per_criterion_rows() -> None:
    verdict = ReviewResult(
        verdict="approve",
        summary="all met",
        criteria=[CriterionVerdict("a", "met", "ok"), CriterionVerdict("b", "met", "")],
    )

    facts = JudgeFacts.from_verdict(verdict, revisions=1)

    assert facts.verdict == "approve" and facts.revisions == 1
    assert facts.criteria == (CriterionVerdict("a", "met", "ok"), CriterionVerdict("b", "met", ""))


def test_judge_facts_tolerate_a_reviewer_that_records_no_criteria() -> None:
    """A stub or third-party reviewer returns the older, narrower shape."""
    old = SimpleNamespace(verdict="comment", blockers=["x"], summary="s", uncertain=["u"])

    facts = JudgeFacts.from_verdict(old, revisions=0)

    assert facts.criteria == () and facts.blockers == ("x",) and facts.uncertain == ("u",)


# ---- the cost --------------------------------------------------------------------------------


def test_cost_facts_snapshot_the_ledger() -> None:
    ledger = TokenLedger()
    ledger.record("implement", CompletionResult("", "model-a", 100, 40, 0.25, 1.0))
    ledger.record("semantic_review", CompletionResult("", "model-b", 10, 5, 0.05, 1.0, cost_known=False))

    facts = CostFacts.from_ledger(ledger)

    assert (facts.calls, facts.prompt_tokens, facts.completion_tokens) == (2, 110, 45)
    assert facts.cost_usd == pytest.approx(0.30)
    assert facts.models == ("model-a", "model-b")
    assert facts.unpriced_calls == 1 and facts.unreported_calls == 0


def test_cost_facts_of_an_empty_ledger_are_zero_not_missing() -> None:
    facts = CostFacts.from_ledger(TokenLedger())
    assert facts.calls == 0 and facts.cost_usd == 0.0 and facts.models == ()


# ---- the risk --------------------------------------------------------------------------------


async def test_a_python_change_measures_its_blast_radius(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("orchestrator.sdlc.escalation.blast_radius", lambda root: (12, "py:pkg.core.run"))

    risk = await assess_risk("/w", ["src/pkg/core.py"], uncertain=[], iterations=1)

    assert risk.state == "computed"
    assert risk.blast_radius == 12 and risk.radius_symbol == "py:pkg.core.run"
    assert risk.escalate is True
    assert any("blast radius" in r for r in risk.reasons)


async def test_a_change_with_no_python_file_is_not_measured_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`blast_radius()` returns (0, "") for a change with no `.py` file. Reported as 0 it reads
    as "nothing depends on this" about a Java or Markdown change it never looked at."""

    def _boom(root: Any) -> Any:
        raise AssertionError("must not be asked about a change with no Python file")

    monkeypatch.setattr("orchestrator.sdlc.escalation.blast_radius", _boom)

    risk = await assess_risk("/w", ["src/Main.java", "README.md"], uncertain=[], iterations=1)

    assert risk.state == "computed" and risk.blast_radius is None
    assert risk.escalate is False


async def test_the_other_signals_still_count_when_the_radius_is_not_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orchestrator.sdlc.escalation.blast_radius", lambda root: (0, ""))

    risk = await assess_risk("/w", ["README.md"], uncertain=["handles unicode"], iterations=5)

    assert risk.blast_radius is None and risk.escalate is True
    assert len(risk.reasons) == 2  # the judge's doubt, and the refine cycles


async def test_a_failing_measurement_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(root: Any) -> Any:
        raise RuntimeError("extractor fell over")

    monkeypatch.setattr("orchestrator.sdlc.escalation.blast_radius", _boom)

    risk = await assess_risk("/w", ["a.py"], uncertain=[], iterations=1)

    assert risk.state == "not_computed" and "RuntimeError" in risk.detail
    assert risk.blast_radius is None and risk.escalate is False


def test_a_run_that_did_not_assess_risk_says_why() -> None:
    risk = RiskFacts.not_computed("safe mode — no PR is opened")
    assert risk.state == "not_computed" and risk.detail == "safe mode — no PR is opened"
