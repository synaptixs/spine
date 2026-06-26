from __future__ import annotations

import httpx
import pytest

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools import WebSearchHandler


def _ctx() -> InvocationContext:
    return InvocationContext(
        tool_id="tool.web_search",
        tool_version="0.1.0",
        trace_id="t-1",
        actor="dev",
        credentials={"TAVILY_API_KEY": "fake-key"},
    )


async def test_web_search_returns_normalised_results() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T1", "url": "https://a", "content": "snip1"},
                    {"title": "T2", "url": "https://b", "content": "snip2"},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await WebSearchHandler(client=client).__call__({"query": "test", "max_results": 2}, _ctx())
    await client.aclose()

    assert out["query"] == "test"
    assert len(out["results"]) == 2
    assert out["results"][0] == {"title": "T1", "url": "https://a", "snippet": "snip1"}
    assert "tavily.com" in str(captured["url"])
    assert "fake-key" in str(captured["payload"])


async def test_empty_query_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        await WebSearchHandler().__call__({"query": "   "}, _ctx())


async def test_missing_credential_rejected() -> None:
    ctx = InvocationContext(
        tool_id="tool.web_search",
        tool_version="0.1.0",
        trace_id="t-1",
        actor="dev",
        credentials={},
    )
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        await WebSearchHandler().__call__({"query": "x"}, ctx)


async def test_max_results_clamped_to_twenty() -> None:
    seen: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.read())
        seen["max_results"] = int(body["max_results"])
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await WebSearchHandler(client=client).__call__({"query": "q", "max_results": 999}, _ctx())
    await client.aclose()
    assert seen["max_results"] == 20
