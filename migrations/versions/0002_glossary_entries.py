"""glossary entries table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-15

Adds the glossary_entries table backing the GlossaryEntry registry entity.
Same shape as agent_templates / tool_contracts so the existing
VersionedRepo serves it too.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "glossary_entries",
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
        sa.UniqueConstraint("id", "version", name="uq_glossary_entries_id_version"),
    )
    op.create_index("ix_glossary_entries_id", "glossary_entries", ["id"])
    op.create_index("ix_glossary_entries_version", "glossary_entries", ["version"])
    op.create_index("ix_glossary_entries_tags_gin", "glossary_entries", ["tags"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_glossary_entries_tags_gin", table_name="glossary_entries")
    op.drop_index("ix_glossary_entries_version", table_name="glossary_entries")
    op.drop_index("ix_glossary_entries_id", table_name="glossary_entries")
    op.drop_table("glossary_entries")
