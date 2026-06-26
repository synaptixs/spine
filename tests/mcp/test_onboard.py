"""Onboard: handler registration (unit; the DB publish path is integration)."""

from __future__ import annotations

from typing import Any

from orchestrator.gateway.handlers import HandlerRegistry
from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.handler import build_mcp_tools
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.onboard import register_mcp_handlers
from orchestrator.mcp.registry import MCPRegistry


class _FakeClient:
    def __init__(self, server: str, tools: list[tuple[str, bool | None]]) -> None:
        self._server, self._tools = server, tools

    async def list_tools(self) -> list[MCPTool]:
        return [MCPTool(server=self._server, name=n, read_only=ro) for n, ro in self._tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        return MCPToolResult(text="ok")


async def _build() -> list[Any]:
    cfg = MCPServerConfig(name="fs", command="x", allow=("read_file",))
    registry = MCPRegistry([cfg], client_factory=lambda _c: _FakeClient("fs", [("read_file", True)]))
    return await build_mcp_tools(registry, configs=[cfg])


async def test_register_mcp_handlers_registers_and_is_idempotent() -> None:
    built = await _build()
    hr = HandlerRegistry()
    ids = register_mcp_handlers(hr, built)
    assert ids == ["mcp.fs.read_file"]
    assert hr.get("mcp.fs.read_file", "0.1.0") is not None
    # Re-onboarding must not raise on an already-registered handler.
    register_mcp_handlers(hr, built)
    assert len(hr.keys()) == 1
