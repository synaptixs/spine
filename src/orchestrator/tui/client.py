"""HTTP client for the registry ``/v1`` API, used by the TUI (P4).

A thin async wrapper over httpx. The TUI is a programmatic caller, so it
authenticates with ``X-API-Key`` (not the browser session). Dependency-light (no
Textual) so it's unit-testable with an httpx ``MockTransport``.
"""

from __future__ import annotations

from typing import Any

import httpx


class RegistryClient:
    """Talks to the registry API as the operator (X-API-Key auth)."""

    def __init__(
        self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            transport=transport,
            timeout=15.0,
        )

    async def runs(self) -> list[dict[str, Any]]:
        return await self._items("/v1/runs")

    async def approvals(self) -> list[dict[str, Any]]:
        return await self._items("/v1/approvals")

    async def decide(self, approval_id: str, action: str) -> None:
        resp = await self._client.post(f"/v1/approvals/{approval_id}/{action}", json={})
        resp.raise_for_status()

    async def start_run(self, source: str, *, create_jira: bool = False) -> dict[str, Any]:
        resp = await self._client.post("/v1/runs/start", json={"source": source, "create_jira": create_jira})
        resp.raise_for_status()
        return dict(resp.json())

    async def _items(self, path: str) -> list[dict[str, Any]]:
        resp = await self._client.get(path)
        resp.raise_for_status()
        return list(resp.json().get("items", []))

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = ["RegistryClient"]
