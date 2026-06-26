"""add tenant_id to approval_requests and audit_log

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-18

Bet 2c-ii (RBAC + multi-tenancy): scope approvals and audit rows to an owning
tenant. ``server_default='default'`` backfills existing rows and keeps
single-tenant installs (no principals configured) working unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("approval_requests", "audit_log"):
        op.add_column(
            table,
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def downgrade() -> None:
    for table in ("approval_requests", "audit_log"):
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")
