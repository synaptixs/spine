"""Web search handler backed by the Tavily Search API."""

from __future__ import annotations

from typing import Any

import httpx

from orchestrator.gateway.invocation import InvocationContext

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class WebSearchHandler:
    contract_id: str = "tool.web_search"
    contract_version: str = "0.1.0"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        query = str(inputs["query"]).strip()
        if not query:
            raise ValueError("web_search: 'query' must be a non-empty string")
        max_results = int(inputs.get("max_results", 5))

        api_key = ctx.credentials.get("TAVILY_API_KEY") or _from_authorization(ctx)
        if not api_key:
            raise RuntimeError("web_search: TAVILY_API_KEY not resolved by gateway auth")

        body = {
            "api_key": api_key,
            "query": query,
            "max_results": max(1, min(max_results, 20)),
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        try:
            response = await client.post(TAVILY_ENDPOINT, json=body)
        finally:
            if owns_client:
                await client.aclose()

        response.raise_for_status()
        payload = response.json()
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in payload.get("results", [])
        ]
        return {"results": results, "query": query}


def _from_authorization(ctx: InvocationContext) -> str | None:
    """Fall back to the Bearer token the gateway injected via the standard env var."""
    for value in ctx.credentials.values():
        if value:
            return value
    return None


WEB_SEARCH_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.web_search",
        "version": "0.1.0",
        "description": "Search the web via the Tavily API.",
        "tags": ["search", "research"],
    },
    "spec": {
        "purpose": "Return web search results for a free-text query.",
        "side_effects": "read",
        "idempotent": True,
        "data_freshness": "real-time",
        "rate_limits": {"requests_per_minute": 60, "burst": 10},
        "authentication": {"type": "api_key", "secret_ref": "TAVILY_API_KEY"},
        "observability": {"audit": True, "trace": True},
    },
}
