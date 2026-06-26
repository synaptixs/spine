"""Block B.1 unit tests: storage-format stripping + Confluence adapter.

The adapter is driven against an httpx MockTransport that routes the
Confluence v2 page + children endpoints. No network.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from orchestrator.intake.confluence import (
    ConfluenceAdapter,
    ConfluenceConfig,
    ConfluenceError,
    _storage_to_text,
)


def _config() -> ConfluenceConfig:
    return ConfluenceConfig(
        base_url="https://acme.atlassian.net/wiki",
        email="bot@acme.io",
        api_token="tok",
    )


# ---- storage → text -------------------------------------------------------


def test_storage_to_text_strips_tags_and_keeps_structure() -> None:
    storage = (
        "<h1>Title</h1>"
        "<p>First paragraph with <strong>bold</strong>.</p>"
        "<ul><li>one</li><li>two</li></ul>"
        "<p>Amp &amp; entity &lt;ok&gt;</p>"
    )
    text = _storage_to_text(storage)
    assert "Title" in text
    assert "First paragraph with bold." in text
    assert "one" in text and "two" in text
    assert "Amp & entity <ok>" in text
    assert "<" not in text.replace("<ok>", "")  # no leftover tags besides decoded entity


def test_storage_to_text_collapses_blank_runs() -> None:
    storage = "<p>a</p><p></p><p></p><p>b</p>"
    text = _storage_to_text(storage)
    assert "\n\n\n" not in text


def test_storage_to_text_drops_jira_macro_noise() -> None:
    # Live-testing finding: a Jira-issue macro's ac:parameter config leaked
    # "System JIRA", a server UUID, and the issue key into the text.
    storage = (
        '<ac:structured-macro ac:name="jira">'
        '<ac:parameter ac:name="server">System JIRA</ac:parameter>'
        '<ac:parameter ac:name="serverId">1e2bd18c-7773-3433-bbcd-af8096529c6c</ac:parameter>'
        '<ac:parameter ac:name="key">THAL-3</ac:parameter>'
        "</ac:structured-macro>"
        "<p>Real requirement prose.</p>"
    )
    text = _storage_to_text(storage)
    assert "Real requirement prose." in text
    assert "System JIRA" not in text
    assert "THAL-3" not in text
    assert "1e2bd18c" not in text


def test_storage_to_text_keeps_panel_rich_text_body() -> None:
    # ac:rich-text-body is real prose inside an info/panel macro — keep it.
    storage = (
        '<ac:structured-macro ac:name="info">'
        '<ac:parameter ac:name="title">Note</ac:parameter>'
        "<ac:rich-text-body><p>Important caveat to keep.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    text = _storage_to_text(storage)
    assert "Important caveat to keep." in text
    assert "Note" not in text  # the macro param title is dropped


# ---- adapter --------------------------------------------------------------


class _ConfluenceMock:
    def __init__(self, *, children: dict[str, list[str]] | None = None) -> None:
        # children maps page_id -> [child_id, ...]
        self.children = children or {}
        self.fetched: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # /wiki/api/v2/pages/{id}/children
        if path.endswith("/children"):
            page_id = path.split("/pages/")[1].split("/children")[0]
            kids = self.children.get(page_id, [])
            return httpx.Response(
                200,
                json={"results": [{"id": cid, "title": f"Child {cid}"} for cid in kids]},
            )
        # /wiki/api/v2/pages/{id}
        if "/pages/" in path:
            page_id = path.split("/pages/")[1]
            self.fetched.append(page_id)
            body: dict[str, Any] = {
                "id": page_id,
                "title": f"Page {page_id}",
                "spaceId": "SP1",
                "body": {"storage": {"value": f"<p>Body of {page_id}</p>"}},
                "_links": {"webui": f"/spaces/SP1/pages/{page_id}"},
            }
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={})


def _adapter(mock: _ConfluenceMock) -> tuple[ConfluenceAdapter, httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(mock.handler), base_url="https://acme.atlassian.net"
    )
    return ConfluenceAdapter(_config(), http_client=http), http


async def test_fetch_document_extracts_text_and_url() -> None:
    mock = _ConfluenceMock()
    adapter, http = _adapter(mock)
    try:
        doc = await adapter.fetch_document("123")
    finally:
        await http.aclose()
    assert doc.id == "123"
    assert doc.title == "Page 123"
    assert doc.body == "Body of 123"
    assert doc.url == "https://acme.atlassian.net/wiki/spaces/SP1/pages/123"
    assert doc.space == "SP1"


async def test_list_children_returns_refs() -> None:
    mock = _ConfluenceMock(children={"root": ["a", "b"]})
    adapter, http = _adapter(mock)
    try:
        refs = await adapter.list_children("root")
    finally:
        await http.aclose()
    assert [r.id for r in refs] == ["a", "b"]
    assert all(r.kind == "page" for r in refs)


async def test_fetch_tree_walks_breadth_first() -> None:
    # root → [a, b]; a → [c]
    mock = _ConfluenceMock(children={"root": ["a", "b"], "a": ["c"]})
    adapter, http = _adapter(mock)
    try:
        result = await adapter.fetch_tree("root", max_depth=3, max_docs=100)
    finally:
        await http.aclose()
    ids = [d.id for d in result.documents]
    assert ids == ["root", "a", "b", "c"]
    assert result.truncated is False


async def test_fetch_tree_respects_max_docs_cap() -> None:
    mock = _ConfluenceMock(children={"root": ["a", "b", "c", "d", "e"]})
    adapter, http = _adapter(mock)
    try:
        result = await adapter.fetch_tree("root", max_docs=3)
    finally:
        await http.aclose()
    assert len(result.documents) == 3
    assert result.truncated is True


async def test_fetch_tree_respects_max_depth() -> None:
    # depth 0: root; depth 1: a; depth 2: b — cap depth at 1 so b is never queued
    mock = _ConfluenceMock(children={"root": ["a"], "a": ["b"]})
    adapter, http = _adapter(mock)
    try:
        result = await adapter.fetch_tree("root", max_depth=1)
    finally:
        await http.aclose()
    assert [d.id for d in result.documents] == ["root", "a"]


async def test_create_page_posts_storage_body_with_parent() -> None:
    import json as jsonlib

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/pages"):
            captured["body"] = jsonlib.loads(request.content)
            return httpx.Response(
                200,
                json={"id": "999", "_links": {"webui": "/spaces/SP1/pages/999/Intents"}},
            )
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = ConfluenceAdapter(_config(), http_client=http)
    try:
        created = await adapter.create_page(
            space_id="SP1",
            title="Extracted Intents",
            body_storage="<h1>Intents</h1><p>One.</p>",
            parent_id="123",
        )
    finally:
        await http.aclose()

    assert created.id == "999"
    assert created.url == "https://acme.atlassian.net/wiki/spaces/SP1/pages/999/Intents"
    body = captured["body"]
    assert body["spaceId"] == "SP1"
    assert body["title"] == "Extracted Intents"
    assert body["parentId"] == "123"
    assert body["status"] == "current"
    assert body["body"]["representation"] == "storage"
    assert body["body"]["value"] == "<h1>Intents</h1><p>One.</p>"


async def test_unconfigured_adapter_raises() -> None:
    # _env_file=None so a developer's local .env (live-test creds) can't make
    # this "configured" and mask the guard.
    adapter = ConfluenceAdapter(ConfluenceConfig(_env_file=None))  # type: ignore[call-arg]
    with pytest.raises(ConfluenceError, match="not configured"):
        await adapter.fetch_document("1")


async def test_api_error_surfaces() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://acme.atlassian.net")
    adapter = ConfluenceAdapter(_config(), http_client=http)
    try:
        with pytest.raises(ConfluenceError, match="HTTP 403"):
            await adapter.fetch_document("1")
    finally:
        await http.aclose()
