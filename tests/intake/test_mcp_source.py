"""Atlassian (Confluence) requirements source via an onboarded MCP server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.intake.factory import (
    SUPPORTED_SOURCE_KINDS,
    IntakeNotConfiguredError,
    build_service_for,
)
from orchestrator.intake.mcp_source import MCPConfluenceAdapter, MCPSourceConfig
from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry


class _FakeAtlassian:
    def __init__(self, pages: dict[str, Any], children: dict[str, Any]) -> None:
        self._pages, self._children = pages, children

    async def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(server="confluence", name="confluence_get_page", read_only=True),
            MCPTool(server="confluence", name="confluence_get_page_children", read_only=True),
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        if name == "confluence_get_page":
            return MCPToolResult(text=json.dumps(self._pages.get(arguments["page_id"], {})))
        if name == "confluence_get_page_children":
            return MCPToolResult(text=json.dumps(self._children.get(arguments["parent_id"], [])))
        return MCPToolResult(text="{}")


def _adapter(pages: dict[str, Any], children: dict[str, Any]) -> MCPConfluenceAdapter:
    cfg = MCPServerConfig(
        name="confluence",
        url="http://x",
        allow=("confluence_get_page", "confluence_get_page_children"),
    )
    registry = MCPRegistry([cfg], client_factory=lambda _c: _FakeAtlassian(pages, children))
    return MCPConfluenceAdapter(registry, MCPSourceConfig())


async def test_fetch_document_parses_a_page() -> None:
    adapter = _adapter({"123": {"title": "Spec", "body": "As a user I want CSV export."}}, {})
    doc = await adapter.fetch_document("123")
    assert doc.id == "123" and doc.title == "Spec" and "CSV export" in doc.body


async def test_fetch_tree_walks_children_breadth_first() -> None:
    adapter = _adapter(
        {"root": {"title": "Root", "body": "r"}, "c1": {"title": "Child", "body": "c"}},
        {"root": [{"id": "c1", "title": "Child"}]},
    )
    result = await adapter.fetch_tree("root")
    assert {d.id for d in result.documents} == {"root", "c1"}
    assert result.truncated is False


async def test_fetch_tree_respects_max_docs() -> None:
    adapter = _adapter({"root": {"body": "r"}, "c1": {"body": "c"}}, {"root": [{"id": "c1"}]})
    result = await adapter.fetch_tree("root", max_docs=1)
    assert len(result.documents) == 1 and result.truncated is True


# ---- factory dispatch -------------------------------------------------------


def test_mcp_confluence_is_a_supported_source_kind() -> None:
    assert "mcp-confluence" in SUPPORTED_SOURCE_KINDS


def test_unconfigured_mcp_confluence_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(tmp_path / "absent.json"))  # no servers
    with pytest.raises(IntakeNotConfiguredError, match="MCP Confluence source"):
        build_service_for("mcp-confluence://123", dry_run=True)


def test_configured_mcp_confluence_builds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfgfile = tmp_path / "mcp.json"
    cfgfile.write_text(
        '{"mcpServers": {"confluence": {"url": "http://x", "allow": ["confluence_get_page"]}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(cfgfile))
    service = build_service_for("mcp-confluence://123", dry_run=True)  # builds without connecting
    assert service is not None
