from __future__ import annotations

import httpx
import pytest

from orchestrator.core.tool_client import (
    CredentialsUnavailableError,
    GatewayClient,
    HandlerFailedError,
    RateLimitedError,
    ToolCallResult,
    ToolNotRegisteredError,
)


def _client_with_handler(handler: httpx.MockTransport) -> GatewayClient:
    transport_client = httpx.AsyncClient(transport=handler, base_url="http://gw")
    return GatewayClient(base_url="http://gw", api_key="k", client=transport_client)


async def test_successful_invoke_returns_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "k"
        assert request.headers["X-Trace-Id"] == "t-1"
        return httpx.Response(
            200,
            json={
                "tool_id": "tool.echo",
                "tool_version": "0.1.0",
                "output": {"echoed": {"msg": "hi"}},
                "elapsed_ms": 1.5,
                "cost_usd": 0.0,
            },
        )

    client = _client_with_handler(httpx.MockTransport(handler))
    result = await client.invoke("tool.echo", "0.1.0", {"msg": "hi"}, trace_id="t-1")
    assert isinstance(result, ToolCallResult)
    assert result.output == {"echoed": {"msg": "hi"}}
    assert result.elapsed_ms == 1.5


async def test_404_maps_to_tool_not_registered() -> None:
    handler = httpx.MockTransport(lambda r: httpx.Response(404, json={"detail": "missing"}))
    client = _client_with_handler(handler)
    with pytest.raises(ToolNotRegisteredError, match="missing"):
        await client.invoke("tool.x", "0.1.0", {})


async def test_429_maps_to_rate_limited_with_retry_after() -> None:
    handler = httpx.MockTransport(
        lambda r: httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "7"})
    )
    client = _client_with_handler(handler)
    with pytest.raises(RateLimitedError) as exc:
        await client.invoke("tool.x", "0.1.0", {})
    assert exc.value.retry_after_seconds == 7.0


async def test_503_maps_to_credentials_error() -> None:
    handler = httpx.MockTransport(lambda r: httpx.Response(503, json={"detail": "missing API key"}))
    client = _client_with_handler(handler)
    with pytest.raises(CredentialsUnavailableError):
        await client.invoke("tool.x", "0.1.0", {})


async def test_500_maps_to_handler_failed() -> None:
    handler = httpx.MockTransport(
        lambda r: httpx.Response(500, json={"detail": "Handler raised: ValueError"})
    )
    client = _client_with_handler(handler)
    with pytest.raises(HandlerFailedError):
        await client.invoke("tool.x", "0.1.0", {})
