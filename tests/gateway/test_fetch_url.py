from __future__ import annotations

import httpx
import pytest

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools import FetchUrlHandler


def _ctx() -> InvocationContext:
    return InvocationContext(
        tool_id="tool.fetch_url",
        tool_version="0.1.0",
        trace_id="t-1",
        actor="dev",
    )


async def test_fetch_returns_body_status_and_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>hi</html>", headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await FetchUrlHandler(client=client).__call__({"url": "https://example.com/x"}, _ctx())
    await client.aclose()
    assert out["status"] == 200
    assert out["content_type"] == "text/html"
    assert "<html>" in out["body"]
    assert out["truncated"] is False


async def test_allowlist_blocks_disallowed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_FETCH_URL_ALLOWLIST", "example.com,trusted.org")
    with pytest.raises(PermissionError, match="not in TOOL_FETCH_URL_ALLOWLIST"):
        await FetchUrlHandler().__call__({"url": "https://evil.test/"}, _ctx())


async def test_allowlist_permits_listed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_FETCH_URL_ALLOWLIST", "example.com")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await FetchUrlHandler(client=client).__call__({"url": "https://example.com/x"}, _ctx())
    await client.aclose()
    assert out["body"] == "ok"


async def test_body_truncated_above_cap() -> None:
    big = "a" * (FetchUrlHandler.MAX_BYTES + 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big.encode(), headers={"content-type": "text/plain"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await FetchUrlHandler(client=client).__call__({"url": "https://example.com/big"}, _ctx())
    await client.aclose()
    assert out["truncated"] is True
    assert len(out["body"].encode("utf-8")) <= FetchUrlHandler.MAX_BYTES
