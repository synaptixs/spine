"""The plugin's registry client — over an httpx MockTransport (no server)."""

from __future__ import annotations

import json

import httpx
import pytest

from orchestrator.plugin.registry_client import RegistryClient, RegistryError, registry_client


def _client(handler: object) -> RegistryClient:
    return RegistryClient("http://test/", "k", transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_runs_and_approvals_unwrap_items_send_the_key_and_the_limit() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key", "")
        seen[request.url.path] = request.url.params.get("limit", "")
        body = {"items": [{"sdlc_id": "R1"}]} if "runs" in request.url.path else {"items": [{"id": "g1"}]}
        return httpx.Response(200, json=body)

    client = _client(handler)
    assert (await client.runs(limit=7))[0]["sdlc_id"] == "R1"
    assert (await client.approvals(limit=9))[0]["id"] == "g1"
    assert seen["key"] == "k"  # X-API-Key auth
    assert seen["/v1/runs"] == "7" and seen["/v1/approvals"] == "9"
    await client.aclose()


async def test_decide_posts_to_the_action_path_with_only_what_was_given() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content or b"{}")
        return httpx.Response(200, json={"id": "g1", "status": "approved"})

    client = _client(handler)
    out = await client.decide("g1", "approve")
    assert out["status"] == "approved"
    assert seen["path"] == "/v1/approvals/g1/approve"
    assert seen["body"] == {}  # the API's ApprovalDecision forbids extras; send nothing spurious

    await client.decide("g1", "modify_input", rationale="why", modified_input={"k": 1})
    assert seen["body"] == {"rationale": "why", "modified_input": {"k": 1}}
    await client.aclose()


async def test_trace_reads_the_task_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/tasks/R1/trace"
        return httpx.Response(200, json={"task_id": "R1", "audit": []})

    client = _client(handler)
    assert (await client.trace("R1"))["task_id"] == "R1"
    await client.aclose()


async def test_a_server_that_is_not_there_is_a_registry_error_with_a_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client(handler)
    with pytest.raises(RegistryError) as info:
        await client.runs()
    assert "ConnectError" in str(info.value)
    assert "orchestrator up" in info.value.hint
    await client.aclose()


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(401, "ORCHESTRATOR_API_KEY"), (404, "list first"), (422, "rejected"), (500, "orchestrator up")],
)
async def test_http_failures_carry_a_hint_matched_to_the_status(status: int, fragment: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "nope"})

    client = _client(handler)
    with pytest.raises(RegistryError) as info:
        await client.approval("g1")
    assert f"{status}" in str(info.value) and "nope" in str(info.value)
    assert fragment in info.value.hint
    await client.aclose()


def test_the_factory_reads_the_same_env_as_every_other_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_URL", "http://registry:9000/")
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "sekrit")
    client = registry_client()
    assert client.base_url == "http://registry:9000"
    assert client._client.headers["x-api-key"] == "sekrit"
