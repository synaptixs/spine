from __future__ import annotations

import httpx
import pytest

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools import FetchMetricDefinitionHandler


def _ctx() -> InvocationContext:
    return InvocationContext(
        tool_id="tool.fetch_metric_definition",
        tool_version="0.1.0",
        trace_id="t-1",
        actor="dev",
    )


async def test_returns_canonical_definition() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/v1/glossary/churn" in str(request.url)
        assert request.headers["X-Trace-Id"] == "t-1"
        return httpx.Response(
            200,
            json={
                "id": "churn",
                "version": "0.1.0",
                "spec": {
                    "canonical_value": "logo churn",
                    "definition": "Customers who fully cancelled in the period.",
                    "source": "finance",
                    "owner": "rev-ops",
                    "formula": "1 - retention_rate",
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://reg")
    out = await FetchMetricDefinitionHandler(base_url="http://reg", client=client).__call__(
        {"term": "churn"}, _ctx()
    )
    await client.aclose()
    assert out["canonical_value"] == "logo churn"
    assert out["source"] == "finance"
    assert out["formula"] == "1 - retention_rate"


async def test_404_maps_to_lookup_error() -> None:
    handler = httpx.MockTransport(lambda r: httpx.Response(404, json={"detail": "missing"}))
    client = httpx.AsyncClient(transport=handler, base_url="http://reg")
    h = FetchMetricDefinitionHandler(base_url="http://reg", client=client)
    with pytest.raises(LookupError):
        await h({"term": "unknown_term"}, _ctx())
    await client.aclose()


async def test_empty_term_rejected() -> None:
    with pytest.raises(ValueError, match="term"):
        await FetchMetricDefinitionHandler().__call__({"term": "  "}, _ctx())
