"""SQLAlchemy ORM models backing the registry service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for registry tables."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentTemplateRow(Base):
    __tablename__ = "agent_templates"
    __table_args__ = (UniqueConstraint("id", "version", name="uq_agent_templates_id_version"),)

    pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB().with_variant(JSON, "sqlite"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )


class ToolContractRow(Base):
    __tablename__ = "tool_contracts"
    __table_args__ = (UniqueConstraint("id", "version", name="uq_tool_contracts_id_version"),)

    pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB().with_variant(JSON, "sqlite"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )


class GlossaryEntryRow(Base):
    __tablename__ = "glossary_entries"
    __table_args__ = (UniqueConstraint("id", "version", name="uq_glossary_entries_id_version"),)

    pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB().with_variant(JSON, "sqlite"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )


class CalibrationHistoryRow(Base):
    """One row per terminal verifier outcome on a template@version run.

    Sprint 11.6 backs the planner's confidence-calibration ranking. Each
    row records the agent's claimed confidence and what the terminal
    verifier said about it (pass / warn / fail); aggregations live in
    ``CalibrationHistoryRepo``.
    """

    __tablename__ = "calibration_history"

    pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    claimed_confidence: Mapped[float] = mapped_column(nullable=False)
    verifier_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        index=True,
    )


class ApprovalRequestRow(Base):
    """Sprint 14.2: Approval-gate persistence.

    One row per approval request raised by the runtime. Indexed for two
    primary lookups: (a) the pending queue UI ("show me everything in
    state=pending"), (b) the workflow signal handler resolving an id to a
    task_id when a decision arrives via REST.

    ``before_hash`` (Sprint 14.9) links this row to the previous approval
    row for the same task — gap detection on the approval-decision chain
    without extending Merkle-style chaining to all of audit_log (the
    bigger compliance move lands in Sprint 18).
    """

    __tablename__ = "approval_requests"

    pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Bet 2c-ii: the owning tenant. ``server_default='default'`` backfills
    # existing rows and keeps single-tenant installs working.
    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", server_default="default", index=True
    )
    before_node_id: Mapped[str] = mapped_column(String(128), nullable=False)

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(String(4096), nullable=False)
    action_summary: Mapped[str] = mapped_column(String(512), nullable=False)
    risk_classification: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    affected_resources: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    approvers_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=False, default=list
    )
    timeout_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=True
    )
    notification_channels: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_rationale: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    modified_input_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        index=True,
    )


class AuditLogRow(Base):
    """Append-only audit log. Writes never update or delete existing rows."""

    __tablename__ = "audit_log"

    pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        index=True,
    )
    # Bet 2c-ii: the owning tenant (see ApprovalRequestRow.tenant_id).
    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", server_default="default", index=True
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=True
    )
    after_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class MemoryRow(Base):
    """Cross-run semantic memory — the experience-true layer.

    A consolidated fact learned *from* past runs (see
    ``docs/specs/cross-run-semantic-memory.md``), read back by the agentic loop
    via the ``recall_memory`` tool. Distinct from the code-true ``memory-bank/``
    files: this is derived from what the agent *did*, and every row cites its
    source run(s) in ``evidence`` so it stays auditable and prunable. The
    ``embedding`` column (pgvector ANN) is deferred to Phase 3; Phase 1 ranks by
    keyword overlap, so the schema is portable (no pgvector dependency yet).
    """

    __tablename__ = "agent_memory"

    pk: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", server_default="default", index=True
    )
    # Which project this memory is about (repo identity, e.g. owner/name or path).
    repo_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    # convention | pitfall | decision | fix-pattern
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # repo (project-specific) | global (applies across projects)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="repo", server_default="repo")
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    # {run_ids: [...], trace_steps: [...], files: [...]} — provenance for the fact.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=True
    )
    # Starts 0.5; reinforced on dedup-hit (write path), decayed on disuse (Phase 3).
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    # Times retrieved-and-used — the feedback loop signal.
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
