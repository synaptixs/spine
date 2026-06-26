"""Integration: onboard MCP tools → published contract + loader match (Postgres)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.gateway.handlers import HandlerRegistry
from orchestrator.gateway.loader import load_published_tools
from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.onboard import onboard_mcp_tools
from orchestrator.mcp.registry import MCPRegistry

pytestmark = pytest.mark.integration


class _FakeClient:
    def __init__(self, server: str, tools: list[tuple[str, bool | None]]) -> None:
        self._server, self._tools = server, tools

    async def list_tools(self) -> list[MCPTool]:
        return [MCPTool(server=self._server, name=n, read_only=ro) for n, ro in self._tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        return MCPToolResult(text="ok")


async def test_onboard_publishes_contract_and_loader_matches(session: AsyncSession, tmp_path: Path) -> None:
    cfgfile = tmp_path / "mcp.json"
    cfgfile.write_text('{"mcpServers": {"fs": {"command": "x", "allow": ["read_file"]}}}', encoding="utf-8")
    cfg = MCPServerConfig(name="fs", command="x", allow=("read_file",))
    registry = MCPRegistry([cfg], client_factory=lambda _c: _FakeClient("fs", [("read_file", True)]))
    handlers = HandlerRegistry()

    ids = await onboard_mcp_tools(session, handlers, config_path=cfgfile, mcp_registry=registry)
    assert "mcp.fs.read_file" in ids

    # The published contract + registered handler now match through the loader.
    report = await load_published_tools(session, handlers)
    assert report.by_id_version("mcp.fs.read_file", "0.1.0") is not None

    # Idempotent: a second onboard neither duplicates nor raises.
    ids_again = await onboard_mcp_tools(session, handlers, config_path=cfgfile, mcp_registry=registry)
    assert ids_again == ids
