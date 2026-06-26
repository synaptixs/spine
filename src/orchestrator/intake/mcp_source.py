"""Atlassian (Confluence) requirements source via an onboarded MCP server.

Reuses the MCP-client onboarding (Phase 1): instead of direct REST creds, read
Confluence pages through an MCP server the operator already runs (e.g.
``mcp-atlassian``). A ``SourceAdapter`` so it drops into the existing intake
pipeline (intents → specs → Jira) like the native Confluence adapter.

Tool/argument names differ across Atlassian MCP servers, so they're configurable
(``MCPSourceConfig``) with ``mcp-atlassian`` defaults. Results are parsed
leniently — JSON when the server returns it, else the raw text as the body.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from orchestrator.intake.source import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_DOCS,
    FetchTreeResult,
    SourceDocument,
    SourceRef,
)
from orchestrator.mcp.registry import MCPRegistry


@dataclass(frozen=True)
class MCPSourceConfig:
    """Which onboarded MCP server + tools back the Confluence source."""

    server: str = "confluence"
    page_tool: str = "confluence_get_page"
    page_arg: str = "page_id"
    children_tool: str = "confluence_get_page_children"
    children_arg: str = "parent_id"

    @classmethod
    def from_env(cls) -> MCPSourceConfig:
        return cls(
            server=os.getenv("MCP_CONFLUENCE_SERVER", "confluence"),
            page_tool=os.getenv("MCP_CONFLUENCE_PAGE_TOOL", "confluence_get_page"),
            page_arg=os.getenv("MCP_CONFLUENCE_PAGE_ARG", "page_id"),
            children_tool=os.getenv("MCP_CONFLUENCE_CHILDREN_TOOL", "confluence_get_page_children"),
            children_arg=os.getenv("MCP_CONFLUENCE_CHILDREN_ARG", "parent_id"),
        )


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


class MCPConfluenceAdapter:
    """Confluence requirements source backed by an onboarded MCP server."""

    source_kind = "mcp-confluence"

    def __init__(self, registry: MCPRegistry, config: MCPSourceConfig) -> None:
        self._registry = registry
        self._config = config

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        cfg = self._config
        result = await self._registry.call(f"{cfg.server}:{cfg.page_tool}", {cfg.page_arg: doc_id})
        data = _loads(result.text)
        if isinstance(data, dict):
            inner = data.get("page")
            page: dict[str, Any] = inner if isinstance(inner, dict) else data
            title = str(page.get("title") or "")
            body = str(page.get("body") or page.get("content") or page.get("text") or "")
        else:
            title, body = "", result.text
        return SourceDocument(id=doc_id, title=title or doc_id, body=body)

    async def list_children(self, doc_id: str) -> list[SourceRef]:
        cfg = self._config
        result = await self._registry.call(f"{cfg.server}:{cfg.children_tool}", {cfg.children_arg: doc_id})
        data = _loads(result.text)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("results") or data.get("children") or []
        else:
            items = []
        return [
            SourceRef(id=str(it["id"]), title=str(it.get("title") or ""))
            for it in items
            if isinstance(it, dict) and it.get("id")
        ]

    async def fetch_tree(
        self,
        root_id: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_docs: int = DEFAULT_MAX_DOCS,
    ) -> FetchTreeResult:
        documents: list[SourceDocument] = []
        truncated = False
        seen: set[str] = set()
        queue: list[tuple[str, int]] = [(root_id, 0)]
        while queue:
            doc_id, depth = queue.pop(0)
            if doc_id in seen:
                continue
            seen.add(doc_id)
            if len(documents) >= max_docs:
                truncated = True
                break
            documents.append(await self.fetch_document(doc_id))
            if depth < max_depth:
                for ref in await self.list_children(doc_id):
                    if ref.id not in seen:
                        queue.append((ref.id, depth + 1))
        return FetchTreeResult(documents=documents, truncated=truncated)


__all__ = ["MCPConfluenceAdapter", "MCPSourceConfig"]
