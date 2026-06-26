"""initial registry tables

Revision ID: 0001
Revises:
Create Date: 2026-05-13

Creates the three baseline registry tables: agent_templates,
tool_contracts, and the append-only audit_log.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_versioned_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column("pk", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("description", sa.String(1024), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column("spec_json", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("id", "version", name=f"uq_{name}_id_version"),
    )
    op.create_index(f"ix_{name}_id", name, ["id"])
    op.create_index(f"ix_{name}_version", name, ["version"])
    op.create_index(f"ix_{name}_tags_gin", name, ["tags"], postgresql_using="gin")


def _drop_versioned_table(name: str) -> None:
    op.drop_index(f"ix_{name}_tags_gin", table_name=name)
    op.drop_index(f"ix_{name}_version", table_name=name)
    op.drop_index(f"ix_{name}_id", table_name=name)
    op.drop_table(name)


def upgrade() -> None:
    _create_versioned_table("agent_templates")
    _create_versioned_table("tool_contracts")

    op.create_table(
        "audit_log",
        sa.Column("pk", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("before_json", postgresql.JSONB, nullable=True),
        sa.Column("after_json", postgresql.JSONB, nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])
    op.create_index("ix_audit_log_actor", "audit_log", ["actor"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_resource_id", "audit_log", ["resource_id"])
    op.create_index("ix_audit_log_trace_id", "audit_log", ["trace_id"])


def downgrade() -> None:
    for ix in (
        "ix_audit_log_trace_id",
        "ix_audit_log_resource_id",
        "ix_audit_log_action",
        "ix_audit_log_actor",
        "ix_audit_log_timestamp",
    ):
        op.drop_index(ix, table_name="audit_log")
    op.drop_table("audit_log")

    _drop_versioned_table("tool_contracts")
    _drop_versioned_table("agent_templates")
