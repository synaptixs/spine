"""calibration history table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-19

One row per terminal-verifier outcome on a template@version run. Backs the
planner's confidence-calibration ranking (Sprint 11.6).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calibration_history",
        sa.Column("pk", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("template_id", sa.String(128), nullable=False),
        sa.Column("template_version", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("claimed_confidence", sa.Float, nullable=False),
        sa.Column("verifier_outcome", sa.String(16), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_calibration_history_template_id", "calibration_history", ["template_id"])
    op.create_index("ix_calibration_history_template_version", "calibration_history", ["template_version"])
    op.create_index("ix_calibration_history_task_id", "calibration_history", ["task_id"])
    op.create_index("ix_calibration_history_recorded_at", "calibration_history", ["recorded_at"])


def downgrade() -> None:
    for ix in (
        "ix_calibration_history_recorded_at",
        "ix_calibration_history_task_id",
        "ix_calibration_history_template_version",
        "ix_calibration_history_template_id",
    ):
        op.drop_index(ix, table_name="calibration_history")
    op.drop_table("calibration_history")
