from __future__ import annotations

from typing import Annotated, get_args, get_type_hints

import pytest

from orchestrator.core.state import (
    OrchestratorState,
    append_list,
    merge_dict,
    set_once,
    write_once_glossary,
)


def test_merge_dict_later_wins_on_conflict() -> None:
    assert merge_dict({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}


def test_merge_dict_handles_none() -> None:
    assert merge_dict(None, {"a": 1}) == {"a": 1}
    assert merge_dict({"a": 1}, None) == {"a": 1}
    assert merge_dict(None, None) == {}


def test_append_list_preserves_order() -> None:
    assert append_list([1, 2], [3, 4]) == [1, 2, 3, 4]


def test_append_list_handles_none() -> None:
    assert append_list(None, [1]) == [1]
    assert append_list([1], None) == [1]
    assert append_list(None, None) == []


def test_set_once_initial_set() -> None:
    assert set_once({}, {"task_id": "t_1"}) == {"task_id": "t_1"}


def test_set_once_idempotent_same_value() -> None:
    prior = {"task_id": "t_1"}
    assert set_once(prior, prior) == prior


def test_set_once_rejects_modification() -> None:
    with pytest.raises(ValueError, match="set_once"):
        set_once({"task_id": "t_1"}, {"task_id": "t_2"})


def test_write_once_glossary_adds_terms() -> None:
    result = write_once_glossary({"churn": "logo"}, {"arr": "annual recurring revenue"})
    assert result == {"churn": "logo", "arr": "annual recurring revenue"}


def test_write_once_glossary_rejects_redefinition() -> None:
    with pytest.raises(ValueError, match="churn"):
        write_once_glossary({"churn": "logo"}, {"churn": "revenue"})


def test_write_once_glossary_allows_same_value_again() -> None:
    result = write_once_glossary({"churn": "logo"}, {"churn": "logo", "arr": "x"})
    assert result == {"churn": "logo", "arr": "x"}


def test_reducer_idempotency_under_repeated_parallel_writes() -> None:
    """Applying the same parallel update twice must yield the same state.

    Idempotency is required for at-least-once delivery semantics in
    LangGraph's checkpoint/resume flow.
    """
    state = {"a": 1}
    update = {"b": 2}
    once = merge_dict(state, update)
    twice = merge_dict(once, update)
    assert once == twice == {"a": 1, "b": 2}


def _reducer_for(field: str) -> object | None:
    hints = get_type_hints(OrchestratorState, include_extras=True)
    annotation = hints[field]
    args = get_args(annotation)
    return args[1] if len(args) == 2 else None


def test_typed_dict_channels_carry_expected_reducers() -> None:
    assert _reducer_for("task_metadata") is set_once
    assert _reducer_for("task_glossary") is write_once_glossary
    assert _reducer_for("node_outputs") is merge_dict
    assert _reducer_for("artifacts") is merge_dict
    assert _reducer_for("claims") is append_list
    assert _reducer_for("confidence_history") is append_list
    assert _reducer_for("tool_call_log") is append_list
    assert _reducer_for("approval_requests") is append_list
    assert _reducer_for("budget_consumed") is merge_dict


def test_unreduced_channels_are_last_write_wins() -> None:
    """Channels without a reducer fall back to LangGraph's default."""
    for field in ("replan_count", "current_node_id"):
        hints = get_type_hints(OrchestratorState, include_extras=True)
        annotation = hints[field]
        assert (
            get_args(annotation) == ()
            or not callable(get_args(annotation)[-1])
            or annotation.__class__ is not Annotated
        )
