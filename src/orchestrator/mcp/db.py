"""Introspect a database schema through an onboarded DB MCP server.

Most DB MCP servers (Postgres/MySQL) expose a ``query`` tool; one
``information_schema`` query fetches every table + column, which
``schema_to_facts`` then projects into the PKG data layer. The query-tool name
and its SQL argument name are configurable because servers differ.
"""

from __future__ import annotations

import json
from typing import Any

from orchestrator.mcp.registry import MCPRegistry
from orchestrator.pkg.schema import DBColumn, DBSchema, DBTable, ForeignKey

_COLUMNS_SQL = (
    "SELECT table_name, column_name, data_type, is_nullable "
    "FROM information_schema.columns "
    "WHERE table_schema = '{schema}' "
    "ORDER BY table_name, ordinal_position"
)

# Foreign keys (standard information_schema join; Postgres/MySQL-compatible).
_FK_SQL = (
    "SELECT tc.table_name AS table_name, kcu.column_name AS column_name, "
    "ccu.table_name AS ref_table, ccu.column_name AS ref_column "
    "FROM information_schema.table_constraints tc "
    "JOIN information_schema.key_column_usage kcu "
    "ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
    "JOIN information_schema.constraint_column_usage ccu "
    "ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema "
    "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = '{schema}'"
)


def _parse_rows(text: str) -> list[dict[str, Any]]:
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"DB MCP query did not return JSON rows: {text[:200]}") from exc
    if isinstance(data, dict):  # some servers wrap rows as {"rows": [...]} / {"result": [...]}
        data = data.get("rows") or data.get("result") or data.get("data") or []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def _rows_to_fks(rows: list[dict[str, Any]]) -> dict[str, list[ForeignKey]]:
    by_table: dict[str, list[ForeignKey]] = {}
    for row in rows:
        table = str(row.get("table_name") or "")
        column = str(row.get("column_name") or "")
        ref_table = str(row.get("ref_table") or "")
        if not (table and column and ref_table):
            continue
        by_table.setdefault(table, []).append(
            ForeignKey(column=column, ref_table=ref_table, ref_column=str(row.get("ref_column") or ""))
        )
    return by_table


def _rows_to_schema(
    rows: list[dict[str, Any]], database: str, fk_rows: list[dict[str, Any]] | None = None
) -> DBSchema:
    by_table: dict[str, list[DBColumn]] = {}
    for row in rows:
        table = str(row.get("table_name") or "")
        column = str(row.get("column_name") or "")
        if not table or not column:
            continue
        nullable = str(row.get("is_nullable", "YES")).upper() != "NO"
        by_table.setdefault(table, []).append(
            DBColumn(name=column, type=str(row.get("data_type") or ""), nullable=nullable)
        )
    fks = _rows_to_fks(fk_rows or [])
    tables = tuple(
        DBTable(name=t, columns=tuple(cols), foreign_keys=tuple(fks.get(t, ())))
        for t, cols in by_table.items()
    )
    return DBSchema(database=database, tables=tables)


async def introspect_via_mcp(
    mcp_registry: MCPRegistry,
    *,
    server: str,
    query_tool: str = "query",
    sql_arg: str = "sql",
    db_schema: str = "public",
    database: str | None = None,
) -> DBSchema:
    """Read the schema of ``server``'s database via its ``query`` MCP tool."""
    result = await mcp_registry.call(
        f"{server}:{query_tool}", {sql_arg: _COLUMNS_SQL.format(schema=db_schema)}
    )
    # Foreign keys are best-effort: a server whose information_schema differs
    # just yields no REFERENCES edges rather than failing schema introspection.
    fk_rows: list[dict[str, Any]] = []
    try:
        fk_result = await mcp_registry.call(
            f"{server}:{query_tool}", {sql_arg: _FK_SQL.format(schema=db_schema)}
        )
        fk_rows = _parse_rows(fk_result.text)
    except Exception:  # noqa: BLE001 — FK discovery is additive, never required
        fk_rows = []
    return _rows_to_schema(_parse_rows(result.text), database or server, fk_rows)


__all__ = ["introspect_via_mcp"]
