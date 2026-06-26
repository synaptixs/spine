"""Sprint 14.1: unit tests for the ApprovalRequest model.

These pin the on-the-wire shape that the REST API + workflow integration
both serialise against. Stricter validations (timeout bounds, approver
roles non-empty, risk classification enum) catch malformed requests
before they hit the DB.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    ApprovalTimeout,
    Approver,
    RiskClassification,
)


def _minimal_request(**overrides: object) -> ApprovalRequest:
    defaults: dict[str, object] = {
        "id": "approval-12345678",
        "task_id": "task-abcd1234",
        "before_node_id": "n_destructive",
        "title": "Approve destructive DB migration",
        "description": "Migration will DROP a non-empty column. Confirm.",
        "action_summary": "Run migrations.sql against prod.",
        "approvers": [Approver(role="dba", min_required=1)],
    }
    defaults.update(overrides)
    return ApprovalRequest(**defaults)  # type: ignore[arg-type]


def test_minimal_request_defaults_to_pending_medium_risk() -> None:
    req = _minimal_request()
    assert req.state is ApprovalState.PENDING
    assert req.risk_classification is RiskClassification.MEDIUM
    assert req.affected_resources == []
    assert req.notification_channels == []
    assert req.before_hash is None


def test_timeout_rejects_negative_and_too_large() -> None:
    with pytest.raises(ValidationError):
        ApprovalTimeout(after_seconds=-1, auto_action="reject")
    with pytest.raises(ValidationError):
        ApprovalTimeout(after_seconds=999_999_999, auto_action="reject")


def test_timeout_rejects_unknown_auto_action() -> None:
    with pytest.raises(ValidationError):
        ApprovalTimeout(after_seconds=60, auto_action="explode")


def test_approver_role_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        Approver(role="", min_required=1)


def test_approver_min_required_capped_at_ten() -> None:
    with pytest.raises(ValidationError):
        Approver(role="dba", min_required=11)


def test_request_requires_at_least_one_approver() -> None:
    with pytest.raises(ValidationError):
        _minimal_request(approvers=[])


def test_decision_accepts_modified_input_dict() -> None:
    d = ApprovalDecision(rationale="ok", modified_input={"hint": "use staging schema"})
    assert d.modified_input == {"hint": "use staging schema"}
    assert d.rationale == "ok"


def test_request_round_trips_through_model_dump() -> None:
    req = _minimal_request(
        timeout=ApprovalTimeout(after_seconds=300, auto_action="escalate"),
        risk_classification=RiskClassification.HIGH,
        affected_resources=["prod-db", "audit-bucket"],
        notification_channels=["#ops", "ceo@example.com"],
    )
    payload = req.model_dump(mode="json")
    revived = ApprovalRequest.model_validate(payload)
    assert revived == req
