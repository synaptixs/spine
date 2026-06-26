"""Block B.1: Confluence source adapter.

Implements ``SourceAdapter`` against the Confluence Cloud REST API v2:

  - ``GET /wiki/api/v2/pages/{id}?body-format=storage`` → page + body
  - ``GET /wiki/api/v2/pages/{id}/children``            → child pages

Auth is HTTP Basic with an Atlassian account email + API token (the Cloud
pattern). The page body comes back as "storage format" — Confluence's
XHTML — which ``_storage_to_text`` strips to readable text. That stripper
is regex-based (no HTML-parser dependency); good enough for requirements
prose, and swappable for a real parser if structure-aware extraction is
needed later.

``fetch_tree`` walks the page hierarchy breadth-first, capped by depth +
doc count so one ingest can't pull an entire wiki.
"""

from __future__ import annotations

import html
import re
from collections import deque
from typing import Any

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from orchestrator.intake.source import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_DOCS,
    FetchTreeResult,
    SourceDocument,
    SourceRef,
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
# Confluence block elements we turn into newlines before stripping tags,
# so paragraphs/list items don't run together.
_BLOCK_BREAK_RE = re.compile(r"</(?:p|li|h[1-6]|tr|div|br\s*/?)>", re.IGNORECASE)
# Confluence macro noise: ``<ac:parameter>`` holds macro *config* (a Jira
# macro leaks "System JIRA", a server UUID, the issue key, column widths) —
# not prose. Drop these elements (and ``<ri:...>`` resource-identifiers)
# whole, before the generic tag strip, so they don't pollute the text the
# LLM reads. ``<ac:rich-text-body>`` (real prose inside panels/info macros)
# is deliberately left alone — only its wrapper tags get stripped.
_AC_PARAMETER_RE = re.compile(r"<ac:parameter\b[^>]*>.*?</ac:parameter>", re.IGNORECASE | re.DOTALL)
_RI_RE = re.compile(r"<ri:[^>]*?/?>", re.IGNORECASE)
_AC_EMPTY_MACRO_RE = re.compile(
    r"<ac:structured-macro\b[^>]*>\s*</ac:structured-macro>", re.IGNORECASE | re.DOTALL
)


class ConfluenceError(RuntimeError):
    """Raised when a Confluence API call fails."""


class ConfluenceConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONFLUENCE_", env_file=".env", extra="ignore")

    base_url: str = Field(
        default="",
        description="Confluence Cloud wiki base, e.g. https://yourorg.atlassian.net/wiki",
    )
    email: str = Field(default="", description="Atlassian account email for Basic auth.")
    api_token: str = Field(default="", description="Atlassian API token for Basic auth.")

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.email and self.api_token)

    @property
    def api_base(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v2"


def _storage_to_text(storage_xhtml: str) -> str:
    """Strip Confluence storage-format XHTML to readable text.

    Drops macro-config noise (``ac:parameter`` / ``ri:`` / empty macros)
    first, turns block-closing tags into newlines so structure survives,
    then removes remaining tags, unescapes entities, and collapses blank
    runs.
    """
    de_noised = _AC_PARAMETER_RE.sub("", storage_xhtml)
    de_noised = _RI_RE.sub("", de_noised)
    de_noised = _AC_EMPTY_MACRO_RE.sub("", de_noised)
    with_breaks = _BLOCK_BREAK_RE.sub("\n", de_noised)
    no_tags = _TAG_RE.sub("", with_breaks)
    unescaped = html.unescape(no_tags)
    collapsed = _WS_RE.sub("\n", unescaped)
    return _MULTI_BLANK_RE.sub("\n\n", collapsed).strip()


class ConfluenceAdapter:
    """SourceAdapter over Confluence Cloud v2."""

    source_kind = "confluence"

    def __init__(self, config: ConfluenceConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        data = await self._get(f"/pages/{doc_id}", params={"body-format": "storage"})
        body_storage = ((data.get("body") or {}).get("storage") or {}).get("value", "")
        webui = ((data.get("_links") or {}).get("webui")) or ""
        url = f"{self._config.base_url.rstrip('/')}{webui}" if webui else ""
        return SourceDocument(
            id=str(data.get("id", doc_id)),
            title=str(data.get("title", "")),
            body=_storage_to_text(str(body_storage)),
            url=url,
            space=str(data.get("spaceId", "")),
        )

    async def list_children(self, doc_id: str) -> list[SourceRef]:
        data = await self._get(f"/pages/{doc_id}/children", params={"limit": "250"})
        return [
            SourceRef(id=str(item.get("id", "")), title=str(item.get("title", "")), kind="page")
            for item in (data.get("results") or [])
            if item.get("id")
        ]

    async def fetch_tree(
        self,
        root_id: str,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_docs: int = DEFAULT_MAX_DOCS,
    ) -> FetchTreeResult:
        """Breadth-first walk from ``root_id``, capped by depth + doc count."""
        result = FetchTreeResult()
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(root_id, 0)])
        while queue:
            doc_id, depth = queue.popleft()
            if doc_id in seen:
                continue
            seen.add(doc_id)
            if len(result.documents) >= max_docs:
                result.truncated = True
                break
            result.documents.append(await self.fetch_document(doc_id))
            if depth < max_depth:
                for child in await self.list_children(doc_id):
                    if child.id not in seen:
                        queue.append((child.id, depth + 1))
        return result

    async def create_page(
        self, *, space_id: str, title: str, body_storage: str, parent_id: str | None = None
    ) -> SourceDocument:
        """Create a Confluence page (write). ``body_storage`` is storage-format
        XHTML. Returns the created page as a SourceDocument (id + url).

        This is the adapter's only write; it backs the "upload the extracted
        intents as a child page" step. Callers gate it behind explicit intent.
        """
        payload: dict[str, Any] = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": body_storage},
        }
        if parent_id:
            payload["parentId"] = parent_id
        data = await self._request("POST", "/pages", json_body=payload)
        page_id = str(data.get("id", ""))
        webui = ((data.get("_links") or {}).get("webui")) or ""
        url = f"{self._config.base_url.rstrip('/')}{webui}" if webui else ""
        return SourceDocument(id=page_id, title=title, body="", url=url, space=space_id)

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._config.configured:
            raise ConfluenceError("Confluence not configured (need CONFLUENCE_BASE_URL / EMAIL / API_TOKEN).")
        url = f"{self._config.api_base}{path}"
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            resp = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                auth=(self._config.email, self._config.api_token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()
        if resp.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
            raise ConfluenceError(f"{method} {path} failed: HTTP {resp.status_code} {resp.text[:256]}")
        data: dict[str, Any] = resp.json()
        return data

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
