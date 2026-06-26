"""MCPRegistry: discovery (namespaced, allow-listed) + call routing."""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry


class _FakeClient:
    def __init__(self, server: str, tools: list[tuple[str, bool | None]], *, down: bool = False) -> None:
        self._server = server
        self._tools = tools
        self._down = down
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[MCPTool]:
        if self._down:
            raise RuntimeError("server down")
        return [MCPTool(server=self._server, name=n, read_only=ro) for n, ro in self._tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self.calls.append((name, arguments))
        return MCPToolResult(text=f"{self._server}:{name} ok")


def _registry(clients: dict[str, _FakeClient], configs: list[MCPServerConfig]) -> MCPRegistry:
    return MCPRegistry(configs, client_factory=lambda cfg: clients[cfg.name])


async def test_list_is_namespaced_and_allowlisted() -> None:
    clients = {
        "fs": _FakeClient("fs", [("read_file", True), ("delete_file", False)]),
        "pg": _FakeClient("pg", [("query", True)]),
    }
    configs = [
        MCPServerConfig(name="fs", command="x", allow=("read_file",)),  # delete_file filtered out
        MCPServerConfig(name="pg", url="http://x"),  # no allow-list → all
    ]
    tools = await _registry(clients, configs).list_tools()
    names = {t.qualified_name for t in tools}
    assert names == {"fs:read_file", "pg:query"}
    assert next(t for t in tools if t.name == "read_file").read_only is True


async def test_call_routes_to_the_right_server() -> None:
    clients = {"fs": _FakeClient("fs", [("read_file", True)]), "pg": _FakeClient("pg", [("query", True)])}
    configs = [
        MCPServerConfig(name="fs", command="x", allow=("read_file",)),
        MCPServerConfig(name="pg", url="http://x"),
    ]
    result = await _registry(clients, configs).call("pg:query", {"sql": "select 1"})
    assert result.text == "pg:query ok"
    assert clients["pg"].calls == [("query", {"sql": "select 1"})]
    assert clients["fs"].calls == []


async def test_unknown_server_raises_keyerror() -> None:
    reg = _registry({"fs": _FakeClient("fs", [])}, [MCPServerConfig(name="fs", command="x")])
    with pytest.raises(KeyError, match="unknown MCP server"):
        await reg.call("nope:tool", {})


async def test_non_allowlisted_tool_is_refused() -> None:
    clients = {"fs": _FakeClient("fs", [("read_file", True), ("delete_file", False)])}
    configs = [MCPServerConfig(name="fs", command="x", allow=("read_file",))]
    with pytest.raises(PermissionError, match="not allow-listed"):
        await _registry(clients, configs).call("fs:delete_file", {})


async def test_down_server_is_skipped_not_fatal() -> None:
    clients = {"up": _FakeClient("up", [("ok", True)]), "down": _FakeClient("down", [], down=True)}
    configs = [MCPServerConfig(name="up", command="x"), MCPServerConfig(name="down", command="y")]
    tools = await _registry(clients, configs).list_tools()
    assert {t.qualified_name for t in tools} == {"up:ok"}  # 'down' skipped, 'up' still listed


def test_disabled_server_is_excluded() -> None:
    reg = MCPRegistry([MCPServerConfig(name="off", command="x", enabled=False)])
    assert reg.server_names() == []
