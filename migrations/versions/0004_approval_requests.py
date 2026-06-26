"""approval requests table

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-19

Sprint 14.2: persistence for approval-gate requests. The Temporal workflow
inserts a row when it pauses at an approval gate; the REST API updates the
state when an approver decides. Indexed for the pending-queue UI lookup
and the workflow signal handler resolving id → task_id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("pk", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("before_node_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.String(4096), nullable=False),
        sa.Column("action_summary", sa.String(512), nullable=False),
        sa.Column("risk_classification", sa.String(16), nullable=False, server_default="medium"),
        sa.Column(
            "affected_resources",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("approvers_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("timeout_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "notification_channels",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decision_rationale", sa.String(2048), nullable=True),
        sa.Column("modified_input_json", postgresql.JSONB(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("before_hash", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("id", name="uq_approval_requests_id"),
    )
    op.create_index("ix_approval_requests_id", "approval_requests", ["id"])
    op.create_index("ix_approval_requests_task_id", "approval_requests", ["task_id"])
    op.create_index("ix_approval_requests_state", "approval_requests", ["state"])
    op.create_index("ix_approval_requests_trace_id", "approval_requests", ["trace_id"])
    op.create_index("ix_approval_requests_created_at", "approval_requests", ["created_at"])


def downgrade() -> None:
    for ix in (
        "ix_approval_requests_created_at",
        "ix_approval_requests_trace_id",
        "ix_approval_requests_state",
        "ix_approval_requests_task_id",
        "ix_approval_requests_id",
    ):
        op.drop_index(ix, table_name="approval_requests")
    op.drop_table("approval_requests")
