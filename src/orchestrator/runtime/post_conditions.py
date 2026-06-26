"""Post-conditions and min-confidence enforcement on agent node outputs.

Sprint 8 ships an explicit predicate evaluator rather than CEL — every
post-condition declares a ``field`` (dotted path), an ``op``, and a
``value``. This trades expressiveness for auditability: every rule is a
data row the IR validator can render in the validation report, and there
is no eval() of user-controlled strings.

Failure handlers (``on_failure``, ``on_low_confidence``) decide what the
runtime does next. Sprint 8 implements the two terminal options
(``continue_with_warning``, ``terminate``); ``replan`` and
``escalate_to_human`` land in Sprint 12/14 alongside their dispatchers
and surface today as ``continue_with_warning`` with the original outcome
recorded so the audit row still captures intent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PostConditionOp(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    NOT_EMPTY = "not_empty"
    LEN_GT = "len_gt"
    LEN_GE = "len_ge"
    IN = "in"
    IN_RANGE = "in_range"  # value=[lo, hi], inclusive


class FailureAction(str, Enum):
    CONTINUE_WITH_WARNING = "continue_with_warning"
    TERMINATE = "terminate"
    REPLAN = "replan"  # Sprint 12; falls back to warn today
    ESCALATE_TO_HUMAN = "escalate_to_human"  # Sprint 14; falls back to warn today
    INSERT_VERIFIER = "insert_verifier"  # Sprint 10; warn-only today


@dataclass(frozen=True)
class PostCondition:
    """A single post-condition predicate."""

    field: str
    op: PostConditionOp
    value: Any | None = None
    description: str = ""
    on_failure: FailureAction = FailureAction.TERMINATE


@dataclass(frozen=True)
class MinConfidenceRule:
    """Floor on the agent's reported confidence."""

    threshold: float
    on_low_confidence: FailureAction = FailureAction.CONTINUE_WITH_WARNING


@dataclass(frozen=True)
class PostConditionFailure:
    field: str
    op: str
    expected: Any
    actual: Any
    description: str
    on_failure: FailureAction


@dataclass(frozen=True)
class PostConditionReport:
    failures: tuple[PostConditionFailure, ...] = field(default_factory=tuple)
    confidence_warning: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.failures and self.confidence_warning is None

    @property
    def should_terminate(self) -> bool:
        return any(f.on_failure is FailureAction.TERMINATE for f in self.failures)

    def to_state_value(self) -> dict[str, Any]:
        return {
            "outcome": "pass" if self.ok else ("fail" if self.should_terminate else "warn"),
            "failures": [
                {
                    "field": f.field,
                    "op": f.op,
                    "expected": f.expected,
                    "actual": f.actual,
                    "description": f.description,
                    "on_failure": f.on_failure.value,
                }
                for f in self.failures
            ],
            "confidence_warning": self.confidence_warning,
        }


def _read_path(state: Any, path: str) -> Any:
    parts = [p for p in path.split(".") if p]
    cursor: Any = state
    for part in parts:
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return None
    return cursor


def _evaluate(op: PostConditionOp, actual: Any, expected: Any) -> bool:
    if op is PostConditionOp.NOT_EMPTY:
        if actual is None:
            return False
        if isinstance(actual, (str, list, tuple, dict, set)):
            return len(actual) > 0
        return True
    if actual is None:
        return False
    if op is PostConditionOp.EQ:
        return bool(actual == expected)
    if op is PostConditionOp.NE:
        return bool(actual != expected)
    if op is PostConditionOp.GT:
        return bool(actual > expected)
    if op is PostConditionOp.GE:
        return bool(actual >= expected)
    if op is PostConditionOp.LT:
        return bool(actual < expected)
    if op is PostConditionOp.LE:
        return bool(actual <= expected)
    if op is PostConditionOp.LEN_GT:
        return hasattr(actual, "__len__") and len(actual) > int(expected)
    if op is PostConditionOp.LEN_GE:
        return hasattr(actual, "__len__") and len(actual) >= int(expected)
    if op is PostConditionOp.IN:
        return actual in expected if hasattr(expected, "__contains__") else False
    if op is PostConditionOp.IN_RANGE:
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            return False
        lo, hi = expected
        return bool(lo <= actual <= hi)
    return False


def evaluate_post_conditions(
    output: dict[str, Any],
    rules: list[PostCondition],
    *,
    min_confidence: MinConfidenceRule | None = None,
) -> PostConditionReport:
    """Evaluate every post-condition (and optional min-confidence) against an agent output.

    Returns a typed report. Callers route on ``ok`` / ``should_terminate``;
    the runtime serialises the report into ``node_outputs`` for the audit log.
    """
    failures: list[PostConditionFailure] = []
    for rule in rules:
        actual = _read_path(output, rule.field)
        if _evaluate(rule.op, actual, rule.value):
            continue
        failures.append(
            PostConditionFailure(
                field=rule.field,
                op=rule.op.value,
                expected=rule.value,
                actual=actual,
                description=rule.description,
                on_failure=rule.on_failure,
            )
        )

    confidence_warning: dict[str, Any] | None = None
    if min_confidence is not None:
        actual = output.get("confidence")
        if isinstance(actual, (int, float)) and float(actual) < min_confidence.threshold:
            confidence_warning = {
                "threshold": min_confidence.threshold,
                "actual": float(actual),
                "on_low_confidence": min_confidence.on_low_confidence.value,
            }
            if min_confidence.on_low_confidence is FailureAction.TERMINATE:
                failures.append(
                    PostConditionFailure(
                        field="confidence",
                        op=PostConditionOp.GE.value,
                        expected=min_confidence.threshold,
                        actual=float(actual),
                        description="min_confidence threshold",
                        on_failure=FailureAction.TERMINATE,
                    )
                )

    return PostConditionReport(failures=tuple(failures), confidence_warning=confidence_warning)


def parse_post_condition(raw: dict[str, Any]) -> PostCondition:
    """Build a PostCondition from a node config dict (the format agent templates use)."""
    return PostCondition(
        field=str(raw["field"]),
        op=PostConditionOp(raw["op"]),
        value=raw.get("value"),
        description=str(raw.get("description", "")),
        on_failure=FailureAction(raw.get("on_failure", FailureAction.TERMINATE.value)),
    )


def parse_min_confidence(raw: dict[str, Any] | None) -> MinConfidenceRule | None:
    if not raw:
        return None
    return MinConfidenceRule(
        threshold=float(raw["threshold"]),
        on_low_confidence=FailureAction(
            raw.get("on_low_confidence", FailureAction.CONTINUE_WITH_WARNING.value)
        ),
    )
