"""`probe()` says WHY a server produced no tools.

Before this existed, every failure looked the same from the outside: an empty tool list and
a log line nobody reads. A missing `mcp` extra, an unpulled Docker image, a typo in
`command` and a genuinely dead server were indistinguishable — which turned a one-command
fix into a debugging session.
"""

from __future__ import annotations

from typing import Any

from orchestrator.mcp.client import MCPClient
from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry


class _Boom:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def list_tools(self) -> list[MCPTool]:
        raise self._exc

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        raise self._exc


class _Fine:
    def __init__(self, name: str) -> None:
        self._name = name

    async def list_tools(self) -> list[MCPTool]:
        return [MCPTool(server=self._name, name="jira_get_issue")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        return MCPToolResult(text="{}")


def _registry(cfgs: list[MCPServerConfig], client: MCPClient) -> MCPRegistry:
    return MCPRegistry(cfgs, client_factory=lambda _c: client)


async def test_missing_extra_is_a_config_error_with_a_remedy() -> None:
    """The failure that actually cost a session: the tool list was empty and said nothing."""
    cfg = MCPServerConfig(name="atlassian", command="docker")
    exc = RuntimeError("MCP support needs the 'mcp' extra — install with: pip install ...")
    [status] = await _registry([cfg], _Boom(exc)).probe()

    assert status.kind == "config", "permanent — retrying will never help"
    assert "pip install" in status.remedy
    assert not status.ok


async def test_a_missing_command_is_a_config_error() -> None:
    """Docker not installed, or a typo in `command` — the operator must fix it."""
    cfg = MCPServerConfig(name="atlassian", command="dokcer")
    [status] = await _registry([cfg], _Boom(FileNotFoundError("dokcer"))).probe()
    assert status.kind == "config"
    assert "PATH" in status.remedy


async def test_a_timeout_is_unreachable_not_config() -> None:
    """This one may fix itself; telling the operator to install something would be wrong."""
    cfg = MCPServerConfig(name="slow", command="x")
    [status] = await _registry([cfg], _Boom(TimeoutError("timed out"))).probe()
    assert status.kind == "unreachable"


async def test_an_unrecognised_failure_defaults_to_unreachable() -> None:
    """Guessing `config` for an unknown error would send people to fix the wrong thing."""
    cfg = MCPServerConfig(name="odd", command="x")
    [status] = await _registry([cfg], _Boom(ValueError("something else entirely"))).probe()
    assert status.kind == "unreachable"
    assert status.error, "the raw message is still reported"


async def test_a_healthy_server_reports_its_tools() -> None:
    cfg = MCPServerConfig(name="atlassian", command="x", allow=("jira_get_issue",))
    [status] = await _registry([cfg], _Fine("atlassian")).probe()
    assert status.ok
    assert [t.name for t in status.tools] == ["jira_get_issue"]


async def test_list_tools_still_ignores_failures() -> None:
    """The lenient behaviour must survive: mid-run, one bad server cannot blank the rest."""
    cfg = MCPServerConfig(name="down", command="x")
    assert await _registry([cfg], _Boom(RuntimeError("nope"))).list_tools() == []


async def test_allow_list_still_filters_probe_results() -> None:
    """probe() must not become a way to see tools the operator did not opt into."""
    cfg = MCPServerConfig(name="atlassian", command="x", allow=("something_else",))
    [status] = await _registry([cfg], _Fine("atlassian")).probe()
    assert status.ok
    assert status.tools == ()
