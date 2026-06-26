"""Notion source adapter (G7): a second requirements source behind ``SourceAdapter``.

Mirrors the Confluence adapter against the Notion API — proving the intake
seam is bring-your-own-stack, not Confluence-shaped. The intent extractor
consumes ``SourceDocument``s and never learns which source produced them.

Notion model: a page's *text* lives in its child **blocks** (paragraph,
heading, list item, …), each carrying a ``rich_text`` array; nested *pages*
appear as ``child_page`` blocks. So:

  - ``GET /v1/pages/{id}``            → the page's title (a ``title`` property)
  - ``GET /v1/blocks/{id}/children``  → body blocks (→ text) + child_page refs

Auth is a Bearer integration token plus the required ``Notion-Version``
header. Block-children listings paginate; ``_block_children`` follows the
cursor (capped) so a long page isn't silently truncated.
"""

from __future__ import annotations

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

_API_BASE = "https://api.notion.com/v1"
_MAX_BLOCK_PAGES = 25  # cursor-follow cap per page (25 × 100 blocks is plenty)
# Block types whose rich_text we render as a line of body text. A heading
# keeps a markdown-ish prefix so document structure survives into the prose.
_TEXT_PREFIX = {
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "- ",
    "to_do": "- ",
    "quote": "> ",
}


class NotionError(RuntimeError):
    """Raised when a Notion API call fails."""


class NotionConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTION_", env_file=".env", extra="ignore")

    api_token: str = Field(default="", description="Notion internal integration token.")
    version: str = Field(default="2022-06-28", description="Notion-Version header value.")

    @property
    def configured(self) -> bool:
        return bool(self.api_token)


def _rich_text(items: list[dict[str, Any]] | None) -> str:
    """Concatenate a Notion rich_text array to plain text."""
    if not items:
        return ""
    return "".join(str(it.get("plain_text") or "") for it in items)


def _block_text(block: dict[str, Any]) -> str:
    """One block → a line of text, or '' if it carries no rich_text."""
    btype = str(block.get("type") or "")
    payload = block.get(btype)
    if not isinstance(payload, dict):
        return ""
    text = _rich_text(payload.get("rich_text"))
    if not text:
        return ""
    return f"{_TEXT_PREFIX.get(btype, '')}{text}"


def _page_title(page: dict[str, Any]) -> str:
    """Extract the title from a page's properties (the ``title``-typed prop)."""
    props = page.get("properties")
    if isinstance(props, dict):
        for prop in props.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title = _rich_text(prop.get("title"))
                if title:
                    return title
    return ""


class NotionAdapter:
    """SourceAdapter over the Notion API."""

    source_kind = "notion"

    def __init__(self, config: NotionConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        page = await self._get(f"/pages/{doc_id}")
        blocks = await self._block_children(doc_id)
        body = "\n".join(line for b in blocks if (line := _block_text(b)))
        url = str(page.get("url") or "")
        return SourceDocument(
            id=str(page.get("id", doc_id)),
            title=_page_title(page),
            body=body,
            url=url,
        )

    async def list_children(self, doc_id: str) -> list[SourceRef]:
        refs: list[SourceRef] = []
        for block in await self._block_children(doc_id):
            if block.get("type") == "child_page" and block.get("id"):
                title = str((block.get("child_page") or {}).get("title") or "")
                refs.append(SourceRef(id=str(block["id"]), title=title, kind="page"))
        return refs

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

    async def _block_children(self, block_id: str) -> list[dict[str, Any]]:
        """All child blocks of ``block_id``, following pagination (capped)."""
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(_MAX_BLOCK_PAGES):
            params = {"page_size": "100"}
            if cursor:
                params["start_cursor"] = cursor
            data = await self._get(f"/blocks/{block_id}/children", params=params)
            out.extend(r for r in (data.get("results") or []) if isinstance(r, dict))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return out

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        if not self._config.configured:
            raise NotionError("Notion not configured (need NOTION_API_TOKEN).")
        url = f"{_API_BASE}{path}"
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            resp = await client.request(
                "GET",
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._config.api_token}",
                    "Notion-Version": self._config.version,
                    "Accept": "application/json",
                },
            )
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()
        if resp.status_code != httpx.codes.OK:
            raise NotionError(f"GET {path} failed: HTTP {resp.status_code} {resp.text[:256]}")
        data: dict[str, Any] = resp.json()
        return data

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()


__all__ = ["NotionAdapter", "NotionConfig", "NotionError"]
