"""``MCPRegistry.call`` refuses a tool that is not read-only unless the server is ``write_enabled`` (B43).

``MCPToolHandler`` always applied this rule; the registry — which intake, ``db.py``, the docs pull and
the CLI call directly — checked only the allow-list, so a misconfigured source tool name reached a
write tool with no gate. The rule now lives where every path passes.
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry


class _Client:
    def __init__(self, tools: dict[str, bool | None], *, down: bool = False) -> None:
        self._tools = tools
        self._down = down
        self.calls: list[str] = []
        self.list_count = 0

    async def list_tools(self) -> list[MCPTool]:
        self.list_count += 1
        if self._down:
            raise RuntimeError("server down")
        return [MCPTool(server="s", name=n, read_only=ro) for n, ro in self._tools.items()]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self.calls.append(name)
        return MCPToolResult(text="ok")


def _registry(client: _Client, *, write_enabled: bool = False) -> MCPRegistry:
    cfg = MCPServerConfig(name="s", command="x", write_enabled=write_enabled)
    return MCPRegistry([cfg], client_factory=lambda _c: client)


_TOOLS: dict[str, bool | None] = {"read": True, "unannotated": None, "write": False}


async def test_a_write_tool_is_refused_before_it_reaches_the_server() -> None:
    client = _Client(_TOOLS)
    with pytest.raises(PermissionError, match="write_enabled"):
        await _registry(client).call("s:write", {})
    assert client.calls == [], "the server must not be reached"


async def test_an_unannotated_tool_is_refused_too() -> None:
    """``read_only=None`` is *unknown*, not safe — the handler's rule is ``is True``."""
    client = _Client(_TOOLS)
    with pytest.raises(PermissionError):
        await _registry(client).call("s:unannotated", {})
    assert client.calls == []


async def test_a_read_only_tool_is_called() -> None:
    client = _Client(_TOOLS)
    assert (await _registry(client).call("s:read", {})).text == "ok"
    assert client.calls == ["read"]


async def test_a_write_enabled_server_lets_a_write_tool_through_without_a_lookup() -> None:
    client = _Client(_TOOLS)
    await _registry(client, write_enabled=True).call("s:write", {})
    assert client.calls == ["write"]
    assert client.list_count == 0, "nothing to look up: the operator already opted in"


async def test_a_vouched_call_skips_the_gate_and_the_lookup() -> None:
    """The docs pull clears its tools against ``trust_read_only`` itself and says so."""
    client = _Client(_TOOLS)
    await _registry(client).call("s:unannotated", {}, vouched=True)
    assert client.calls == ["unannotated"]
    assert client.list_count == 0


async def test_discovery_already_run_costs_no_second_lookup() -> None:
    client = _Client(_TOOLS)
    registry = _registry(client)
    await registry.list_tools()
    listed = client.list_count
    await registry.call("s:read", {})
    assert client.list_count == listed


async def test_a_cold_lookup_is_paid_once_and_cached() -> None:
    client = _Client(_TOOLS)
    registry = _registry(client)
    for _ in range(3):
        await registry.call("s:read", {})
    assert client.list_count == 1


async def test_a_failed_lookup_fails_closed() -> None:
    """Cannot tell whether it is a write tool, so it is not called."""
    client = _Client(_TOOLS, down=True)
    with pytest.raises(PermissionError, match="could not"):
        await _registry(client).call("s:read", {})
    assert client.calls == []


async def test_a_tool_the_server_does_not_list_is_refused() -> None:
    client = _Client(_TOOLS)
    with pytest.raises(PermissionError):
        await _registry(client).call("s:ghost", {})
    assert client.calls == []


async def test_the_refusal_names_the_two_ways_forward() -> None:
    with pytest.raises(PermissionError) as info:
        await _registry(_Client(_TOOLS)).call("s:write", {})
    assert "s:write" in str(info.value)
    assert "write_enabled" in str(info.value)
