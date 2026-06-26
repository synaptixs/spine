from __future__ import annotations

from orchestrator.runtime.failure_dispatch import (
    DispatchDecision,
    FailurePolicy,
    NextStep,
    dispatch,
)
from orchestrator.runtime.post_conditions import FailureAction
from orchestrator.runtime.verifiers import VerifierOutcome, VerifierResult
from orchestrator.runtime.verifiers.chain import ChainResult


def _chain(outcome: VerifierOutcome) -> ChainResult:
    return ChainResult(
        chain_id="c",
        aggregate=VerifierResult(verifier_id="c", outcome=outcome, elapsed_ms=1.0),
    )


def test_pass_continues_with_no_action() -> None:
    d = dispatch(_chain(VerifierOutcome.PASS), policy=FailurePolicy())
    assert d.next_step is NextStep.CONTINUE
    assert d.action is FailureAction.CONTINUE_WITH_WARNING
    assert d.intent is None


def test_fail_with_terminate_policy_terminates() -> None:
    d = dispatch(_chain(VerifierOutcome.FAIL), policy=FailurePolicy(on_fail=FailureAction.TERMINATE))
    assert d.next_step is NextStep.TERMINATE


def test_fail_with_continue_with_warning_does_not_terminate() -> None:
    d = dispatch(
        _chain(VerifierOutcome.FAIL),
        policy=FailurePolicy(on_fail=FailureAction.CONTINUE_WITH_WARNING),
    )
    assert d.next_step is NextStep.CONTINUE
    assert d.action is FailureAction.CONTINUE_WITH_WARNING


def test_fail_with_replan_returns_replan_next_step() -> None:
    """Sprint 12: REPLAN now drives a real next_step instead of warn-only.

    Orchestration reads this off the chain's node_outputs slot and asks the
    planner for a revised IR before retrying.
    """
    d = dispatch(_chain(VerifierOutcome.FAIL), policy=FailurePolicy(on_fail=FailureAction.REPLAN))
    assert d.next_step is NextStep.REPLAN
    assert d.action is FailureAction.REPLAN
    assert d.intent is None  # No longer "warn-only" — the dispatcher really replans.


def test_fail_with_escalate_records_intent_but_continues_today() -> None:
    d = dispatch(
        _chain(VerifierOutcome.FAIL),
        policy=FailurePolicy(on_fail=FailureAction.ESCALATE_TO_HUMAN),
    )
    assert d.next_step is NextStep.CONTINUE
    assert d.action is FailureAction.ESCALATE_TO_HUMAN
    assert d.intent is not None
    assert "Sprint 14" in d.intent


def test_fail_with_insert_verifier_continues_without_action() -> None:
    d = dispatch(
        _chain(VerifierOutcome.FAIL),
        policy=FailurePolicy(on_fail=FailureAction.INSERT_VERIFIER),
    )
    assert d.next_step is NextStep.CONTINUE
    assert d.action is FailureAction.INSERT_VERIFIER


def test_every_failure_action_value_has_a_branch_or_documented_intent() -> None:
    """Defensive regression: a new FailureAction enum value must either get a
    real dispatch branch or be registered in _NOT_YET_IMPLEMENTED with a
    documented reason. Silent fall-through to CONTINUE is gone."""
    for action in FailureAction:
        d = dispatch(_chain(VerifierOutcome.FAIL), policy=FailurePolicy(on_fail=action))
        # If we reach here the action was handled; assert action round-trips.
        assert d.action is action


def test_warn_uses_on_warn_action() -> None:
    d = dispatch(
        _chain(VerifierOutcome.WARN),
        policy=FailurePolicy(on_warn=FailureAction.TERMINATE),
    )
    # on_warn=terminate degrades the warn to a terminate at this edge.
    assert d.next_step is NextStep.TERMINATE


def test_decision_to_state_value_round_trip() -> None:
    d = DispatchDecision(
        next_step=NextStep.TERMINATE,
        action=FailureAction.TERMINATE,
        outcome=VerifierOutcome.FAIL,
        rationale="x",
    )
    payload = d.to_state_value()
    assert payload["next_step"] == "terminate"
    assert payload["action"] == "terminate"
    assert payload["outcome"] == "fail"
