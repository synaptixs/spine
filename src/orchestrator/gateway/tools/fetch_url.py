"""HTTP fetch handler with an optional domain allowlist."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from orchestrator.gateway.invocation import InvocationContext


class FetchUrlHandler:
    contract_id: str = "tool.fetch_url"
    contract_version: str = "0.1.0"

    MAX_BYTES = 2 * 1024 * 1024  # 2 MiB cap on response body

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        _ = ctx  # ctx unused; kept for protocol compatibility
        url = str(inputs["url"])
        headers = dict(inputs.get("headers") or {})

        host = urlparse(url).hostname or ""
        if not _domain_allowed(host):
            raise PermissionError(f"fetch_url: host {host!r} not in TOOL_FETCH_URL_ALLOWLIST")

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        try:
            response = await client.get(url, headers=headers, follow_redirects=True)
        finally:
            if owns_client:
                await client.aclose()

        body = response.text
        if len(body.encode("utf-8")) > self.MAX_BYTES:
            body = body.encode("utf-8")[: self.MAX_BYTES].decode("utf-8", errors="ignore")
        return {
            "status": response.status_code,
            "url": str(response.url),
            "content_type": response.headers.get("content-type", ""),
            "body": body,
            "truncated": len(response.text.encode("utf-8")) > self.MAX_BYTES,
        }


def _domain_allowed(host: str) -> bool:
    """Empty allowlist (default) = allow all. Configured allowlist = strict match."""
    allowlist = os.getenv("TOOL_FETCH_URL_ALLOWLIST", "").strip()
    if not allowlist:
        return True
    allowed = {entry.strip().lower() for entry in allowlist.split(",") if entry.strip()}
    return host.lower() in allowed


FETCH_URL_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.fetch_url",
        "version": "0.1.0",
        "description": "Fetch the body of an arbitrary URL via HTTP GET.",
        "tags": ["http"],
    },
    "spec": {
        "purpose": "GET an HTTP URL and return its body, status, and content type.",
        "side_effects": "read",
        "idempotent": True,
        "rate_limits": {"requests_per_minute": 120, "burst": 20},
        "authentication": {"type": "none"},
        "observability": {"audit": True, "trace": True},
    },
}
