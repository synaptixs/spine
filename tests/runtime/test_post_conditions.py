from __future__ import annotations

import pytest

from orchestrator.runtime.post_conditions import (
    FailureAction,
    MinConfidenceRule,
    PostCondition,
    PostConditionOp,
    evaluate_post_conditions,
    parse_min_confidence,
    parse_post_condition,
)


def test_eq_passes_when_actual_matches() -> None:
    rules = [PostCondition(field="status", op=PostConditionOp.EQ, value="ok")]
    report = evaluate_post_conditions({"status": "ok"}, rules)
    assert report.ok


def test_eq_fails_when_actual_differs() -> None:
    rules = [PostCondition(field="status", op=PostConditionOp.EQ, value="ok")]
    report = evaluate_post_conditions({"status": "broken"}, rules)
    assert not report.ok
    assert report.should_terminate  # default on_failure is TERMINATE
    assert report.failures[0].actual == "broken"


def test_not_empty_on_list() -> None:
    rules = [PostCondition(field="claims", op=PostConditionOp.NOT_EMPTY)]
    assert evaluate_post_conditions({"claims": [1]}, rules).ok
    assert not evaluate_post_conditions({"claims": []}, rules).ok
    assert not evaluate_post_conditions({}, rules).ok


def test_len_ge() -> None:
    rules = [PostCondition(field="claims", op=PostConditionOp.LEN_GE, value=3)]
    assert evaluate_post_conditions({"claims": [1, 2, 3]}, rules).ok
    assert not evaluate_post_conditions({"claims": [1, 2]}, rules).ok


def test_in_range() -> None:
    rules = [PostCondition(field="confidence", op=PostConditionOp.IN_RANGE, value=[0.7, 1.0])]
    assert evaluate_post_conditions({"confidence": 0.9}, rules).ok
    assert not evaluate_post_conditions({"confidence": 0.5}, rules).ok


def test_dotted_field_path() -> None:
    rules = [PostCondition(field="execution.exit_code", op=PostConditionOp.EQ, value=0)]
    assert evaluate_post_conditions({"execution": {"exit_code": 0}}, rules).ok
    assert not evaluate_post_conditions({"execution": {"exit_code": 1}}, rules).ok


def test_warn_only_on_continue_with_warning() -> None:
    rules = [
        PostCondition(
            field="status",
            op=PostConditionOp.EQ,
            value="ok",
            on_failure=FailureAction.CONTINUE_WITH_WARNING,
        )
    ]
    report = evaluate_post_conditions({"status": "broken"}, rules)
    assert not report.ok
    assert not report.should_terminate


def test_min_confidence_below_threshold_warns_by_default() -> None:
    report = evaluate_post_conditions(
        {"confidence": 0.4},
        rules=[],
        min_confidence=MinConfidenceRule(threshold=0.7),
    )
    assert report.confidence_warning == {
        "threshold": 0.7,
        "actual": 0.4,
        "on_low_confidence": FailureAction.CONTINUE_WITH_WARNING.value,
    }
    assert not report.should_terminate
    assert not report.ok


def test_min_confidence_terminate_propagates() -> None:
    report = evaluate_post_conditions(
        {"confidence": 0.4},
        rules=[],
        min_confidence=MinConfidenceRule(threshold=0.7, on_low_confidence=FailureAction.TERMINATE),
    )
    assert report.should_terminate


def test_parse_post_condition_from_dict() -> None:
    pc = parse_post_condition({"field": "claims", "op": "len_ge", "value": 1, "description": "must cite"})
    assert pc.op is PostConditionOp.LEN_GE
    assert pc.description == "must cite"


def test_parse_min_confidence_returns_none_for_empty() -> None:
    assert parse_min_confidence(None) is None
    assert parse_min_confidence({}) is None


def test_unknown_op_in_parse_raises() -> None:
    with pytest.raises(ValueError):
        parse_post_condition({"field": "x", "op": "definitely_not_real"})
