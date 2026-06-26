"""Slice 2: MCP tool → ToolContract derivation + write-gated gateway handler."""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.gateway.handlers import ToolHandler
from orchestrator.gateway.invocation import InvocationContext
from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.contract import contract_id_for, mcp_tool_to_contract
from orchestrator.mcp.handler import MCPToolHandler, build_mcp_tools
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry
from orchestrator.registry.tool_contract import ApprovalPolicy, SideEffect

_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "file path"}, "lines": {"type": "integer"}},
    "required": ["path"],
}


def _ctx() -> InvocationContext:
    return InvocationContext(tool_id="x", tool_version="0.1.0", trace_id="t1", actor="dev")


# ---- contract derivation ----------------------------------------------------


def test_read_tool_contract_is_read_idempotent_no_approval() -> None:
    tool = MCPTool(server="fs", name="read_file", input_schema=_SCHEMA, read_only=True)
    c = mcp_tool_to_contract(tool)
    assert c.metadata.id == "mcp.fs.read_file"
    assert c.spec.side_effects is SideEffect.READ
    assert c.spec.idempotent is True
    assert c.spec.requires_approval is ApprovalPolicy.NEVER
    names = {f.name: f for f in c.spec.inputs}
    assert names["path"].required is True and names["path"].type == "string"
    assert names["lines"].required is False and names["lines"].type == "integer"
    assert "idempotency_key" not in names  # read tools are idempotent


def test_write_tool_contract_is_write_gets_idempotency_key_and_approval() -> None:
    schema = {"properties": {"sql": {"type": "string"}}}
    tool = MCPTool(server="pg", name="exec_sql", input_schema=schema, read_only=False)
    c = mcp_tool_to_contract(tool)
    assert c.spec.side_effects is SideEffect.WRITE
    assert c.spec.idempotent is False
    assert c.spec.requires_approval is ApprovalPolicy.CONDITIONAL
    # non-idempotent contracts must accept idempotency_key (registry validator)
    assert any(f.name == "idempotency_key" for f in c.spec.inputs)


def test_contract_id_sanitizes_names() -> None:
    tool = MCPTool(server="My-Server", name="2do-Thing", read_only=True)
    assert contract_id_for(tool) == "mcp.my_server.t_2do_thing"


def test_handler_satisfies_gateway_protocol() -> None:
    h = MCPToolHandler(
        registry=MCPRegistry([]),
        tool=MCPTool(server="fs", name="read_file", read_only=True),
        contract_version="0.1.0",
        write_enabled=False,
    )
    assert isinstance(h, ToolHandler)  # runtime_checkable Protocol


# ---- handler invocation + write-gating --------------------------------------


class _FakeClient:
    def __init__(self, server: str, tools: list[tuple[str, bool | None]]) -> None:
        self._server, self._tools = server, tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[MCPTool]:
        return [MCPTool(server=self._server, name=n, read_only=ro) for n, ro in self._tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self.calls.append((name, arguments))
        return MCPToolResult(text=f"{name} ok")


def _registry(client: _FakeClient, cfg: MCPServerConfig) -> MCPRegistry:
    return MCPRegistry([cfg], client_factory=lambda _c: client)


async def test_read_tool_invokes_and_strips_idempotency_key() -> None:
    client = _FakeClient("fs", [("read_file", True)])
    cfg = MCPServerConfig(name="fs", command="x")
    h = MCPToolHandler(
        registry=_registry(client, cfg),
        tool=MCPTool(server="fs", name="read_file", read_only=True),
        contract_version="0.1.0",
        write_enabled=False,
    )
    out = await h({"path": "a.py", "idempotency_key": "k1"}, _ctx())
    assert out["text"] == "read_file ok" and out["is_error"] is False
    assert out["__cost_usd__"] == 0.0
    assert client.calls == [("read_file", {"path": "a.py"})]  # idempotency_key stripped


async def test_write_tool_blocked_when_not_write_enabled() -> None:
    client = _FakeClient("pg", [("exec_sql", False)])
    cfg = MCPServerConfig(name="pg", url="http://x")
    h = MCPToolHandler(
        registry=_registry(client, cfg),
        tool=MCPTool(server="pg", name="exec_sql", read_only=False),
        contract_version="0.1.0",
        write_enabled=False,
    )
    with pytest.raises(PermissionError, match="write tool"):
        await h({"sql": "delete from t"}, _ctx())
    assert client.calls == []  # never reached the server


async def test_write_tool_allowed_when_write_enabled() -> None:
    client = _FakeClient("pg", [("exec_sql", False)])
    cfg = MCPServerConfig(name="pg", url="http://x", write_enabled=True)
    h = MCPToolHandler(
        registry=_registry(client, cfg),
        tool=MCPTool(server="pg", name="exec_sql", read_only=False),
        contract_version="0.1.0",
        write_enabled=True,
    )
    out = await h({"sql": "update t set x=1"}, _ctx())
    assert out["text"] == "exec_sql ok"


async def test_build_mcp_tools_pairs_contracts_with_handlers() -> None:
    client = _FakeClient("fs", [("read_file", True), ("delete_file", False)])
    cfg = MCPServerConfig(name="fs", command="x", allow=("read_file", "delete_file"), write_enabled=False)
    built = await build_mcp_tools(_registry(client, cfg), configs=[cfg])
    by_id = {b.contract.metadata.id: b for b in built}
    assert set(by_id) == {"mcp.fs.read_file", "mcp.fs.delete_file"}
    assert by_id["mcp.fs.read_file"].handler.read_only is True
    assert by_id["mcp.fs.delete_file"].handler.write_enabled is False  # write tool, gated
