"""Introspect a DB schema via an onboarded MCP server's query tool."""

from __future__ import annotations

import json
from typing import Any

from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.db import introspect_via_mcp
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry


class _FakeQueryClient:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.sqls: list[str] = []

    async def list_tools(self) -> list[MCPTool]:
        return [MCPTool(server="pg", name="query", read_only=True)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self.sqls.append(str(arguments.get("sql")))
        return MCPToolResult(text=json.dumps(self._payload))


def _registry(client: _FakeQueryClient) -> MCPRegistry:
    cfg = MCPServerConfig(name="pg", url="http://x", allow=("query",))
    return MCPRegistry([cfg], client_factory=lambda _c: client)


_ROWS = [
    {"table_name": "orders", "column_name": "id", "data_type": "integer", "is_nullable": "NO"},
    {"table_name": "orders", "column_name": "total", "data_type": "numeric", "is_nullable": "YES"},
    {"table_name": "customers", "column_name": "email", "data_type": "text", "is_nullable": "NO"},
]


async def test_introspect_builds_schema_from_information_schema_rows() -> None:
    client = _FakeQueryClient(_ROWS)
    schema = await introspect_via_mcp(_registry(client), server="pg", database="app")
    assert schema.database == "app"
    tables = {t.name: [c.name for c in t.columns] for t in schema.tables}
    assert tables == {"orders": ["id", "total"], "customers": ["email"]}
    orders = next(t for t in schema.tables if t.name == "orders")
    assert orders.columns[0].nullable is False  # is_nullable=NO parsed
    assert any("information_schema.columns" in s for s in client.sqls)
    assert any("FOREIGN KEY" in s for s in client.sqls)  # FK discovery query also issued


async def test_introspect_handles_rows_wrapped_in_an_object() -> None:
    client = _FakeQueryClient({"rows": [_ROWS[2]]})  # some servers wrap as {"rows": [...]}
    schema = await introspect_via_mcp(_registry(client), server="pg")
    assert [t.name for t in schema.tables] == ["customers"]


def test_rows_to_schema_attaches_foreign_keys() -> None:
    from orchestrator.mcp.db import _rows_to_schema

    cols = [
        {"table_name": "orders", "column_name": "customer_id", "data_type": "integer", "is_nullable": "NO"},
        {"table_name": "customers", "column_name": "id", "data_type": "integer", "is_nullable": "NO"},
    ]
    fks = [
        {"table_name": "orders", "column_name": "customer_id", "ref_table": "customers", "ref_column": "id"}
    ]
    schema = _rows_to_schema(cols, "app", fks)
    orders = next(t for t in schema.tables if t.name == "orders")
    assert len(orders.foreign_keys) == 1
    assert orders.foreign_keys[0].ref_table == "customers"
    # a table with no FK rows has none
    customers = next(t for t in schema.tables if t.name == "customers")
    assert customers.foreign_keys == ()
