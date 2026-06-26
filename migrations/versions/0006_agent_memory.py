"""add agent_memory table (cross-run semantic memory)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-24

Cross-run semantic memory (docs/specs/cross-run-semantic-memory.md) Phase 1:
the experience-true layer the agentic loop reads via ``recall_memory``. The
``embedding`` (pgvector) column is deferred to Phase 3 — Phase 1 ranks by
keyword overlap, so no pgvector extension is required here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_memory",
        sa.Column("pk", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("repo_key", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="repo"),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_agent_memory_tenant_id", "agent_memory", ["tenant_id"])
    op.create_index("ix_agent_memory_repo_key", "agent_memory", ["repo_key"])
    op.create_index("ix_agent_memory_kind", "agent_memory", ["kind"])
    op.create_index("ix_agent_memory_trace_id", "agent_memory", ["trace_id"])


def downgrade() -> None:
    for ix in ("trace_id", "kind", "repo_key", "tenant_id"):
        op.drop_index(f"ix_agent_memory_{ix}", table_name="agent_memory")
    op.drop_table("agent_memory")
