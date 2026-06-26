"""Sprint 14: Approval gates.

Approvals pause a Temporal workflow at a planned point in the IR (a node id
named in ``ir.spec.approval_points``), persist an ``ApprovalRequest`` row,
and wait for an approve/deny signal from the REST API.

This package owns:

  - ``models``: Pydantic models for the request, decision, and timeout config.
  - ``repository``: ``ApprovalRequestRepo`` over Postgres, with the
    pending-queue / detail-fetch / decision helpers used by both the REST
    API and the workflow signal handlers.

The Temporal workflow integration sits in ``orchestrator.temporal`` (Sprint
14.4 / 14.5 bundles); the REST surface lives in ``orchestrator.registry.api.approvals``.
"""

from orchestrator.approval.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    ApprovalTimeout,
    Approver,
    RiskClassification,
)
from orchestrator.approval.repository import ApprovalRequestRepo

__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalRequestRepo",
    "ApprovalState",
    "ApprovalTimeout",
    "Approver",
    "RiskClassification",
]
