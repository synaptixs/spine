"""TUI registry client (P4) — over an httpx MockTransport (no server, no Textual)."""

from __future__ import annotations

import httpx

from orchestrator.tui.client import RegistryClient


def _client(handler: object) -> RegistryClient:
    return RegistryClient("http://test", "k", transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_runs_and_approvals_unwrap_items_and_send_the_key() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key", "")
        body = {"items": [{"sdlc_id": "R1"}]} if "runs" in request.url.path else {"items": [{"id": "g1"}]}
        return httpx.Response(200, json=body)

    client = _client(handler)
    assert (await client.runs())[0]["sdlc_id"] == "R1"
    assert (await client.approvals())[0]["id"] == "g1"
    assert seen["key"] == "k"  # X-API-Key auth
    await client.aclose()


async def test_decide_posts_to_the_action_path() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(204)

    client = _client(handler)
    await client.decide("g1", "approve")
    assert seen["path"] == "/v1/approvals/g1/approve"
    await client.aclose()


async def test_start_run_posts_source_and_returns_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"sdlc_id": "new", "gates": {}})

    client = _client(handler)
    result = await client.start_run("confluence://1")
    assert result["sdlc_id"] == "new"
    await client.aclose()
