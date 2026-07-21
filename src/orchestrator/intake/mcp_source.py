"""Requirements sources backed by an onboarded MCP server (C10).

Instead of direct REST creds, read a source through an MCP server the operator
already runs (e.g. ``mcp-atlassian``, which exposes *both* Confluence and Jira
tools). One generic ``MCPSourceAdapter`` maps the ``SourceAdapter`` seam's two
operations — fetch-document / list-children — onto whatever tools a given server
exposes; ``MCPSourceConfig`` names those tools. Presets cover the common cases:

* ``mcp-confluence`` — pages via ``confluence_get_page`` / ``_get_page_children``.
* ``mcp-jira`` — issues via ``jira_get_issue``; children via ``jira_search`` with
  a ``parent = <key>`` query.
* ``mcp`` — the generic escape hatch: point at *any* onboarded server by setting
  ``MCP_SOURCE_*`` (server + tool/arg names). Server *onboarding* is already
  handled by ``MCPRegistry.from_config()`` (the ``mcpServers`` config); this only
  adds the tool-mapping layer.

Result parsing is deliberately **lenient and unified**: it reads Confluence page
shapes, Jira ``fields.summary`` / ADF ``description``, and falls back to the raw
tool text when a server returns something else — so presets are exact and the
generic path degrades honestly rather than failing. Richer per-server field
mapping (JSON-path extraction) is intentionally deferred until a real
non-Atlassian server needs it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from orchestrator.intake.jira_source import _description_text
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
    """Which onboarded MCP server + tools back a source. Defaults = Confluence
    (so a bare ``MCPSourceConfig()`` stays backward-compatible)."""

    source_kind: str = "mcp-confluence"
    server: str = "confluence"
    doc_tool: str = "confluence_get_page"
    doc_arg: str = "page_id"
    children_tool: str = "confluence_get_page_children"
    children_arg: str = "parent_id"
    #: How to build the children-tool argument from a parent id. Confluence passes
    #: the id straight through (``{id}``); Jira searches for ``parent = {id}``.
    children_query: str = "{id}"

    @property
    def configured(self) -> bool:
        return bool(self.server and self.doc_tool)

    @classmethod
    def for_confluence(cls) -> MCPSourceConfig:
        return cls(
            source_kind="mcp-confluence",
            server=os.getenv("MCP_CONFLUENCE_SERVER", "confluence"),
            doc_tool=os.getenv("MCP_CONFLUENCE_PAGE_TOOL", "confluence_get_page"),
            doc_arg=os.getenv("MCP_CONFLUENCE_PAGE_ARG", "page_id"),
            children_tool=os.getenv("MCP_CONFLUENCE_CHILDREN_TOOL", "confluence_get_page_children"),
            children_arg=os.getenv("MCP_CONFLUENCE_CHILDREN_ARG", "parent_id"),
            children_query="{id}",
        )

    @classmethod
    def for_jira(cls) -> MCPSourceConfig:
        return cls(
            source_kind="mcp-jira",
            server=os.getenv("MCP_JIRA_SERVER", "jira"),
            doc_tool=os.getenv("MCP_JIRA_ISSUE_TOOL", "jira_get_issue"),
            doc_arg=os.getenv("MCP_JIRA_ISSUE_ARG", "issue_key"),
            children_tool=os.getenv("MCP_JIRA_SEARCH_TOOL", "jira_search"),
            children_arg=os.getenv("MCP_JIRA_SEARCH_ARG", "jql"),
            children_query=os.getenv("MCP_JIRA_CHILDREN_QUERY", "parent = {id}"),
        )

    @classmethod
    def from_env(cls) -> MCPSourceConfig:
        """Generic escape hatch — any onboarded server, configured via ``MCP_SOURCE_*``."""
        return cls(
            source_kind="mcp",
            server=os.getenv("MCP_SOURCE_SERVER", ""),
            doc_tool=os.getenv("MCP_SOURCE_DOC_TOOL", ""),
            doc_arg=os.getenv("MCP_SOURCE_DOC_ARG", "id"),
            children_tool=os.getenv("MCP_SOURCE_CHILDREN_TOOL", ""),
            children_arg=os.getenv("MCP_SOURCE_CHILDREN_ARG", "id"),
            children_query=os.getenv("MCP_SOURCE_CHILDREN_QUERY", "{id}"),
        )


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_document(doc_id: str, data: Any, raw_text: str) -> SourceDocument:
    """Normalise an MCP tool result into a SourceDocument, leniently.

    Handles Confluence page shapes (``title`` + ``body``/``content``/``text``),
    Jira issue shapes (``fields.summary`` + ADF ``fields.description``), a common
    ``{"page": {...}}`` envelope, and — when the server returns something else —
    falls back to the raw tool text as the body.
    """
    if not isinstance(data, dict):
        return SourceDocument(id=doc_id, title=doc_id, body=raw_text.strip())

    inner = data.get("page")
    doc: dict[str, Any] = inner if isinstance(inner, dict) else data
    fields_raw = doc.get("fields")
    fields: dict[str, Any] = fields_raw if isinstance(fields_raw, dict) else {}

    title = str(doc.get("title") or fields.get("summary") or doc.get("summary") or doc.get("key") or doc_id)

    body_raw = doc.get("body") or doc.get("content") or doc.get("text")
    if isinstance(body_raw, str):
        body = body_raw
    elif body_raw is not None:  # Confluence storage/ADF-ish object
        body = _description_text(body_raw)
    else:  # Jira: description lives under fields, often as ADF
        body = _description_text(fields.get("description"))
    body = body.strip() or raw_text.strip()

    labels = tuple(str(x) for x in (fields.get("labels") or doc.get("labels") or []))
    resolved_id = str(doc.get("id") or doc.get("key") or doc_id)
    return SourceDocument(id=resolved_id, title=title, body=body, labels=labels)


def _parse_children(data: Any) -> list[SourceRef]:
    """Children refs from a list/children/results/issues payload (id or key)."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("results") or data.get("children") or data.get("issues") or []
    else:
        items = []
    refs: list[SourceRef] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = it.get("id") or it.get("key")
        if not cid:
            continue
        it_fields_raw = it.get("fields")
        it_fields: dict[str, Any] = it_fields_raw if isinstance(it_fields_raw, dict) else {}
        title = str(it.get("title") or it_fields.get("summary") or "")
        refs.append(SourceRef(id=str(cid), title=title))
    return refs


class MCPSourceAdapter:
    """A requirements source read through an onboarded MCP server (any kind)."""

    def __init__(self, registry: MCPRegistry, config: MCPSourceConfig) -> None:
        self._registry = registry
        self._config = config
        self.source_kind = config.source_kind

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        cfg = self._config
        result = await self._registry.call(f"{cfg.server}:{cfg.doc_tool}", {cfg.doc_arg: doc_id})
        return _parse_document(doc_id, _loads(result.text), result.text)

    async def list_children(self, doc_id: str) -> list[SourceRef]:
        cfg = self._config
        if not cfg.children_tool:
            return []
        arg_value = cfg.children_query.format(id=doc_id)
        result = await self._registry.call(f"{cfg.server}:{cfg.children_tool}", {cfg.children_arg: arg_value})
        return _parse_children(_loads(result.text))

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


class MCPConfluenceAdapter(MCPSourceAdapter):
    """Back-compat alias — Confluence-over-MCP. Prefer ``MCPSourceAdapter`` + a preset."""

    def __init__(self, registry: MCPRegistry, config: MCPSourceConfig | None = None) -> None:
        super().__init__(registry, config or MCPSourceConfig.for_confluence())


__all__ = ["MCPConfluenceAdapter", "MCPSourceAdapter", "MCPSourceConfig"]
