"""Sprint 14.1: Pydantic models for approval requests, approvers, timeouts.

Schema follows the spec verbatim with two scope cuts called out below:

  - ``approvers.role`` is a free-form string (not a typed enum), but it is now
    **enforced** at decide time (Bet 2c-ii): a caller must hold a required role
    to decide. ``min_required`` (N-of-M quorum) remains modelled but unenforced —
    the workflow still proceeds on the first valid decision; quorum is a follow-up.

  - ``notification_channels`` records *which* channels would have been
    notified (audit-only). Real delivery (email / Slack / webpush) lands
    in Sprint 19 once a console UI + per-tenant routing exist.

The on-the-wire shape is stable from this sprint forward; the API surface
and the workflow integration both serialise via these models.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApprovalState(str, Enum):
    """Lifecycle states. Transitions: pending → {approved, rejected, timed_out}."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class RiskClassification(str, Enum):
    """Spec uses a free-form classification, but pinning three rungs makes
    it ergonomic to filter the queue UI ("show me only HIGH risk pending
    approvals") and to drive auto-action rules at timeout."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Approver(BaseModel):
    """Who can decide on this approval. ``min_required`` lets a request
    demand more than one approver from the same role (e.g. 2-of-3 for
    high-risk operations)."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=64)
    min_required: int = Field(default=1, ge=1, le=10)


class ApprovalTimeout(BaseModel):
    """What happens if no decision arrives in time.

    ``auto_action`` ∈ {"escalate", "reject", "grant"}. ``grant`` is only
    permitted for ``RiskClassification.LOW`` requests; the timeout worker
    rejects any high-risk grant-on-timeout policy at audit time.
    """

    model_config = ConfigDict(extra="forbid")

    after_seconds: int = Field(ge=1, le=14 * 24 * 3600)  # max 14 days
    auto_action: str = Field(pattern=r"^(escalate|reject|grant)$")


class ApprovalRequest(BaseModel):
    """Spec-shaped approval request. Carries the description fields the
    UI needs plus the lifecycle bookkeeping the workflow + REST surface
    rely on."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=8, max_length=64)
    task_id: str = Field(min_length=8, max_length=64)
    before_node_id: str = Field(min_length=1, max_length=128)

    # Bet 2c-ii: the tenant that owns this approval. Defaults to ``"default"``
    # so single-tenant installs (no principals configured) behave as before;
    # decisions and the pending queue are scoped to the caller's tenant.
    tenant_id: str = Field(default="default", min_length=1, max_length=64)

    # Human-facing description block. Kept loose-but-typed.
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4096)
    action_summary: str = Field(min_length=1, max_length=512)
    risk_classification: RiskClassification = RiskClassification.MEDIUM
    affected_resources: list[str] = Field(default_factory=list)

    # Decision rules.
    approvers: list[Approver] = Field(min_length=1, max_length=10)
    timeout: ApprovalTimeout | None = None
    notification_channels: list[str] = Field(default_factory=list)

    # Lifecycle bookkeeping.
    state: ApprovalState = ApprovalState.PENDING
    decided_by: str | None = None
    decision_rationale: str | None = Field(default=None, max_length=2048)
    modified_input: dict[str, Any] | None = None  # If approver patches workflow input
    decided_at: datetime | None = None

    created_at: datetime | None = None
    trace_id: str | None = None

    # Sprint 14.9: before_hash links this approval to the previous approval
    # row for the same task. Detects re-ordering and gap-style tampering on
    # the approval audit chain without a Merkle tree across the whole
    # audit_log (that bigger move is Sprint 18 / compliance bundle work).
    before_hash: str | None = None


class ApprovalDecision(BaseModel):
    """POST payload for /v1/approvals/{id}/{approve|reject|modify_input}."""

    model_config = ConfigDict(extra="forbid")

    rationale: str | None = Field(default=None, max_length=2048)
    modified_input: dict[str, Any] | None = None
