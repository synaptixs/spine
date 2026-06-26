"""Notion source adapter (G7), driven against an httpx MockTransport (offline).

Routes /pages/{id} and /blocks/{id}/children to canned fixtures so the
adapter is exercised end-to-end — title extraction, block→text, child-page
discovery, BFS tree walk, pagination — without touching the network.
"""

from __future__ import annotations

import httpx
import pytest

from orchestrator.intake.notion import NotionAdapter, NotionConfig, NotionError

# --- fixture: a tiny Notion workspace --------------------------------------
# root page "Spec Home" → child page "Auth" ; each page has a few blocks.
_PAGES = {
    "root": {
        "id": "root",
        "url": "https://notion.so/root",
        "properties": {"title": {"type": "title", "title": [{"plain_text": "Spec Home"}]}},
    },
    "auth": {
        "id": "auth",
        "url": "https://notion.so/auth",
        "properties": {"title": {"type": "title", "title": [{"plain_text": "Auth"}]}},
    },
}
_BLOCKS = {
    "root": [
        {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Overview"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Build the thing."}]}},
        {"id": "auth", "type": "child_page", "child_page": {"title": "Auth"}},
        {"type": "divider", "divider": {}},  # no rich_text → skipped
    ],
    "auth": [
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "OAuth2"}]}},
    ],
}


class _NotionMock:
    def __init__(self) -> None:
        self.auth_seen: str | None = None
        self.version_seen: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth_seen = request.headers.get("Authorization")
        self.version_seen = request.headers.get("Notion-Version")
        path = request.url.path
        if path.startswith("/v1/pages/"):
            page = _PAGES.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=page) if page else httpx.Response(404, json={})
        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            block_id = path.split("/")[3]
            return httpx.Response(200, json={"results": _BLOCKS.get(block_id, []), "has_more": False})
        return httpx.Response(404, json={})


def _adapter(mock: _NotionMock) -> NotionAdapter:
    http = httpx.AsyncClient(transport=httpx.MockTransport(mock.handler))
    return NotionAdapter(NotionConfig(api_token="secret-tok"), http_client=http)


async def test_fetch_document_title_and_block_text() -> None:
    mock = _NotionMock()
    doc = await _adapter(mock).fetch_document("root")
    assert doc.title == "Spec Home"
    assert doc.url == "https://notion.so/root"
    # heading prefix + paragraph; divider (no text) dropped
    assert "# Overview" in doc.body
    assert "Build the thing." in doc.body
    assert "divider" not in doc.body
    # auth header + required version were sent
    assert mock.auth_seen == "Bearer secret-tok"
    assert mock.version_seen == "2022-06-28"


async def test_list_children_returns_child_pages_only() -> None:
    refs = await _adapter(_NotionMock()).list_children("root")
    assert [(r.id, r.title) for r in refs] == [("auth", "Auth")]


async def test_fetch_tree_walks_into_child_pages() -> None:
    result = await _adapter(_NotionMock()).fetch_tree("root")
    by_id = {d.id: d for d in result.documents}
    assert set(by_id) == {"root", "auth"}
    assert "OAuth2" in by_id["auth"].body
    assert result.truncated is False


async def test_fetch_tree_respects_max_docs() -> None:
    result = await _adapter(_NotionMock()).fetch_tree("root", max_docs=1)
    assert len(result.documents) == 1
    assert result.truncated is True


async def test_pagination_follows_cursor() -> None:
    pages = [
        {
            "results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "p1"}]}}],
            "has_more": True,
            "next_cursor": "c1",
        },
        {
            "results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "p2"}]}}],
            "has_more": False,
        },
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/pages/"):
            return httpx.Response(200, json={"id": "x", "properties": {}})
        page = pages[min(calls["n"], len(pages) - 1)]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    doc = await NotionAdapter(NotionConfig(api_token="t"), http_client=http).fetch_document("x")
    assert "p1" in doc.body and "p2" in doc.body  # both pages of blocks fetched


async def test_unconfigured_adapter_raises() -> None:
    adapter = NotionAdapter(NotionConfig(api_token=""))
    with pytest.raises(NotionError):
        await adapter.fetch_document("root")


async def test_api_error_surfaces() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = NotionAdapter(NotionConfig(api_token="t"), http_client=http)
    with pytest.raises(NotionError):
        await adapter.fetch_document("root")
