"""Requirements sources via an onboarded MCP server (Confluence, Jira, generic)."""

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
from orchestrator.intake.mcp_source import MCPConfluenceAdapter, MCPSourceAdapter, MCPSourceConfig
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


# ---- Jira over MCP (preset) -------------------------------------------------


class _FakeJira:
    """Minimal mcp-atlassian Jira: jira_get_issue + jira_search."""

    def __init__(self, issues: dict[str, Any], children: dict[str, list[dict[str, Any]]]) -> None:
        self._issues, self._children = issues, children
        self.searched: list[str] = []

    async def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(server="jira", name="jira_get_issue", read_only=True),
            MCPTool(server="jira", name="jira_search", read_only=True),
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        if name == "jira_get_issue":
            return MCPToolResult(text=json.dumps(self._issues.get(arguments["issue_key"], {})))
        if name == "jira_search":
            jql = str(arguments["jql"])
            self.searched.append(jql)
            return MCPToolResult(text=json.dumps({"issues": self._children.get(jql, [])}))
        return MCPToolResult(text="{}")


def _jira_adapter(issues: dict[str, Any], children: dict[str, list[dict[str, Any]]]) -> MCPSourceAdapter:
    cfg = MCPServerConfig(name="jira", url="http://x", allow=("jira_get_issue", "jira_search"))
    registry = MCPRegistry([cfg], client_factory=lambda _c: _FakeJira(issues, children))
    return MCPSourceAdapter(registry, MCPSourceConfig.for_jira())


async def test_mcp_jira_fetches_issue_with_adf_description() -> None:
    adf = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "boom"}]}]}
    adapter = _jira_adapter(
        {"ENG-1": {"key": "ENG-1", "fields": {"summary": "Login 500", "description": adf}}}, {}
    )
    doc = await adapter.fetch_document("ENG-1")
    assert doc.id == "ENG-1" and doc.title == "Login 500" and "boom" in doc.body


async def test_mcp_jira_walks_children_via_parent_jql() -> None:
    adapter = _jira_adapter(
        {
            "ENG-1": {"key": "ENG-1", "fields": {"summary": "Epic"}},
            "ENG-2": {"key": "ENG-2", "fields": {"summary": "Sub"}},
        },
        {"parent = ENG-1": [{"key": "ENG-2", "fields": {"summary": "Sub"}}]},
    )
    result = await adapter.fetch_tree("ENG-1")
    assert {d.id for d in result.documents} == {"ENG-1", "ENG-2"}


# ---- generic escape hatch + raw-text fallback -------------------------------


class _FakeAny:
    def __init__(self, text: str) -> None:
        self._text = text

    async def list_tools(self) -> list[MCPTool]:
        return [MCPTool(server="acme", name="get_doc", read_only=True)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        return MCPToolResult(text=self._text)


async def test_generic_server_falls_back_to_raw_text() -> None:
    cfg = MCPServerConfig(name="acme", url="http://x", allow=("get_doc",))
    registry = MCPRegistry([cfg], client_factory=lambda _c: _FakeAny("plain requirements text"))
    config = MCPSourceConfig(
        source_kind="mcp", server="acme", doc_tool="get_doc", doc_arg="id", children_tool=""
    )
    adapter = MCPSourceAdapter(registry, config)
    doc = await adapter.fetch_document("D1")
    assert doc.id == "D1" and doc.body == "plain requirements text"
    assert await adapter.list_children("D1") == []  # no children_tool → no walk


# ---- factory dispatch -------------------------------------------------------


def test_mcp_kinds_are_supported() -> None:
    for kind in ("mcp-confluence", "mcp-jira", "mcp"):
        assert kind in SUPPORTED_SOURCE_KINDS


def test_generic_mcp_unconfigured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_SOURCE_SERVER", raising=False)
    monkeypatch.delenv("MCP_SOURCE_DOC_TOOL", raising=False)
    with pytest.raises(IntakeNotConfiguredError, match="MCP source"):
        build_service_for("mcp://D1", dry_run=True)


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
