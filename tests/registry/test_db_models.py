"""Smoke tests for the registry DB models.

End-to-end migration tests (up, down, idempotency) live alongside the
registry service once it lands — they require a running Postgres.
"""

from __future__ import annotations

from sqlalchemy import Table

from orchestrator.registry.db.models import (
    AgentTemplateRow,
    AuditLogRow,
    Base,
    ToolContractRow,
)


def _table_for(row_cls: type) -> Table:
    table = row_cls.__table__  # type: ignore[attr-defined]
    assert isinstance(table, Table)
    return table


def test_metadata_contains_expected_tables() -> None:
    assert set(Base.metadata.tables) == {
        "agent_templates",
        "tool_contracts",
        "glossary_entries",
        "calibration_history",
        "approval_requests",
        "audit_log",
        "agent_memory",
    }


def test_versioned_tables_have_composite_unique() -> None:
    from sqlalchemy import UniqueConstraint

    for row_cls in (AgentTemplateRow, ToolContractRow):
        table = _table_for(row_cls)
        uniques = [
            tuple(c.name for c in uc.columns) for uc in table.constraints if isinstance(uc, UniqueConstraint)
        ]
        assert ("id", "version") in uniques, f"{table.name} missing (id, version) unique"


def test_versioned_tables_index_id_and_version() -> None:
    for row_cls in (AgentTemplateRow, ToolContractRow):
        table = _table_for(row_cls)
        indexed_cols = {col.name for col in table.columns if col.index}
        assert {"id", "version"}.issubset(indexed_cols), f"{table.name}: {indexed_cols}"


def test_audit_log_columns_present() -> None:
    expected = {
        "pk",
        "timestamp",
        "tenant_id",
        "actor",
        "action",
        "resource_type",
        "resource_id",
        "before_json",
        "after_json",
        "trace_id",
    }
    assert {c.name for c in _table_for(AuditLogRow).columns} == expected
