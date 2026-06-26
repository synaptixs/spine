"""Client for invoking tools through the gateway.

LangGraph nodes (Sprint 5) use this client; it knows nothing about
LangGraph, just speaks HTTP to the gateway. Errors are mapped to a
typed hierarchy so callers can react to retry-able cases without
inspecting status codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class GatewayError(RuntimeError):
    """Base class for gateway client errors."""


class ToolNotRegisteredError(GatewayError):
    """The gateway returned 404 — tool unknown."""


class RateLimitedError(GatewayError):
    """The gateway returned 429 with a Retry-After hint."""

    def __init__(self, message: str, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class CredentialsUnavailableError(GatewayError):
    """The gateway returned 503 — credential resolution failed."""


class HandlerFailedError(GatewayError):
    """The gateway returned 500 — handler raised."""


@dataclass(frozen=True)
class ToolCallResult:
    output: dict[str, Any]
    elapsed_ms: float
    cost_usd: float


class GatewayClient:
    """HTTP client for the tool gateway.

    Synchronous and asynchronous flavours both exist on the same class so
    LangGraph nodes (async) and CLI tooling (sync) can share configuration.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client = client

    def _headers(
        self,
        *,
        trace_id: str | None,
        task_id: str | None,
        agent_template_id: str | None,
    ) -> dict[str, str]:
        headers = {"X-API-Key": self._api_key}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        if task_id:
            headers["X-Task-Id"] = task_id
        if agent_template_id:
            headers["X-Agent-Template-Id"] = agent_template_id
        return headers

    async def invoke(
        self,
        tool_id: str,
        version: str,
        inputs: dict[str, Any],
        *,
        trace_id: str | None = None,
        task_id: str | None = None,
        agent_template_id: str | None = None,
    ) -> ToolCallResult:
        url = f"{self._base_url}/v1/tools/{tool_id}/{version}/invoke"
        headers = self._headers(trace_id=trace_id, task_id=task_id, agent_template_id=agent_template_id)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(url, json=inputs, headers=headers)
        finally:
            if owns_client:
                await client.aclose()

        return self._unpack(response)

    @staticmethod
    def _unpack(response: httpx.Response) -> ToolCallResult:
        if response.status_code == 200:
            body = response.json()
            return ToolCallResult(
                output=body["output"],
                elapsed_ms=float(body["elapsed_ms"]),
                cost_usd=float(body["cost_usd"]),
            )
        detail = _safe_detail(response)
        if response.status_code == 404:
            raise ToolNotRegisteredError(detail)
        if response.status_code == 429:
            retry = float(response.headers.get("Retry-After", "1"))
            raise RateLimitedError(detail, retry_after_seconds=retry)
        if response.status_code == 503:
            raise CredentialsUnavailableError(detail)
        if response.status_code == 500:
            raise HandlerFailedError(detail)
        raise GatewayError(f"Unexpected status {response.status_code}: {detail}")


def _safe_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)
