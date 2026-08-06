"""End-to-end tests for `orchestrator mcp contracts` argument type labels.

These drive the CLI command the way a user does (via Typer's ``CliRunner``),
with the MCP registry/handler layer stubbed, and assert that each argument is
rendered with a human-readable type label derived from ``MCPTool.input_schema``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

import orchestrator.cli as cli_mod
from orchestrator.cli import app
from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.handler import MCPRegisteredTool, MCPToolHandler, build_mcp_tools
from orchestrator.mcp.models import MCPTool


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


_TYPED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "additional_fields": {"type": "string"},
        "limit": {"type": "integer"},
        "cursor": {"type": ["string", "null"]},
        "payload": {"anyOf": [{"type": "string"}, {"type": "object"}]},
        "linked": {"$ref": "#/$defs/Thing"},
    },
}


def _tool(name: str, *, schema: dict[str, Any] | None, description: str = "Search issues.") -> MCPTool:
    return MCPTool(
        server="atlassian",
        name=name,
        description=description,
        input_schema=schema if schema is not None else {},
        read_only=True,
    )


class _FakeRegistry:
    """Stands in for ``MCPRegistry`` — only ``list_tools`` is exercised here."""

    def __init__(self, tools: list[MCPTool]) -> None:
        self._tools = tools

    async def list_tools(self) -> list[MCPTool]:
        return list(self._tools)

    async def call(self, qualified: str, args: dict[str, Any]) -> Any:  # pragma: no cover - unused
        raise AssertionError("mcp contracts must never invoke a tool")

    async def aclose(self) -> None:
        return None


async def _built(tools: list[MCPTool]) -> list[MCPRegisteredTool]:
    registry = _FakeRegistry(tools)
    configs = [MCPServerConfig(name="atlassian", command="echo", args=())]
    return await build_mcp_tools(registry, configs=configs)  # type: ignore[arg-type]


def _run_contracts(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tools: list[MCPTool]) -> Any:
    """Invoke `orchestrator mcp contracts` against a stubbed MCP layer."""

    def _fake_load_configs(path: str | None = None) -> list[MCPServerConfig]:
        return [MCPServerConfig(name="atlassian", command="echo", args=())]

    async def _fake_build(registry: Any, **kwargs: Any) -> list[MCPRegisteredTool]:
        return await _built(tools)

    monkeypatch.setattr(cli_mod, "_mcp_load_configs", _fake_load_configs, raising=False)
    monkeypatch.setattr(cli_mod, "_mcp_build_registry", lambda configs: _FakeRegistry(tools), raising=False)
    monkeypatch.setattr(cli_mod, "_mcp_build_tools", _fake_build, raising=False)
    return runner.invoke(app, ["mcp", "contracts"])


def test_handler_keeps_the_raw_tool_for_display() -> None:
    """`build_mcp_tools` must hand back the server's schema so the contracts view
    can derive type labels at display time."""
    import asyncio

    tool = _tool("jira_search", schema=_TYPED_SCHEMA)
    built = asyncio.run(_built([tool]))
    assert len(built) == 1
    handler = built[0].handler
    assert isinstance(handler, MCPToolHandler)
    assert handler.tool is tool
    assert handler.tool.input_schema["properties"]["additional_fields"]["type"] == "string"


def test_contracts_output_shows_declared_argument_types(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_contracts(runner, monkeypatch, [_tool("jira_search", schema=_TYPED_SCHEMA)])
    assert result.exit_code == 0, result.output
    out = result.output
    # name + declared type, together
    assert "additional_fields" in out
    assert "string" in out
    assert "additional_fields (string)" in out or '"additional_fields": "string"' in out
    assert "limit (integer)" in out or '"limit": "integer"' in out
    # union types are joined with a pipe
    assert "string|null" in out


def test_contracts_output_falls_back_to_any_for_untyped_arguments(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_contracts(runner, monkeypatch, [_tool("jira_search", schema=_TYPED_SCHEMA)])
    assert result.exit_code == 0, result.output
    out = result.output
    # anyOf / $ref arguments are still listed, labelled `any` rather than dropped
    assert "payload" in out and "linked" in out
    assert "payload (any)" in out or '"payload": "any"' in out
    assert "linked (any)" in out or '"linked": "any"' in out


def test_contracts_survives_an_empty_schema(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run_contracts(runner, monkeypatch, [_tool("jira_ping", schema={})])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "jira_ping" in result.output


def test_contracts_keeps_existing_fields(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tool name and description survive the type-label change."""
    tool = _tool("jira_search", schema=_TYPED_SCHEMA, description="Search issues.")
    result = _run_contracts(runner, monkeypatch, [tool])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "jira_search" in out
    assert "Search issues." in out
    # the contract itself is still well-formed if the CLI prints JSON
    if out.lstrip().startswith(("{", "[")):
        json.loads(out)


def test_contracts_help_is_available(runner: CliRunner) -> None:
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    assert "contracts" in result.stdout
