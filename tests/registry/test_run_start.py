"""Start-run API + inbox composer (unified UI — P2b)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings

_AUTH = {"X-API-Key": "dev-key"}
_RESULT = {
    "sdlc_id": "abc123",
    "workflow_id": "task-abc123",
    "task_queue": "sdlc-tasks",
    "gates": {"intents": "sdlc-abc123-0", "merge": "sdlc-abc123-1"},
}


def _no_db_app() -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def test_start_run_delegates_to_run_control(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.sdlc.run_control as rc

    seen: dict[str, Any] = {}

    async def fake_start(*, source: str, actor: str, tenant_id: str, create_jira: bool) -> dict[str, Any]:
        seen.update(source=source, actor=actor, tenant_id=tenant_id, create_jira=create_jira)
        return _RESULT

    monkeypatch.setattr(rc, "start_run", fake_start)
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/start", json={"source": "confluence://1"}, headers=_AUTH)
    assert resp.status_code == 202
    assert resp.json()["sdlc_id"] == "abc123"
    # The run is attributed to the caller (actor = principal id) and dry-run by default.
    assert seen["actor"] == "dev-key" and seen["create_jira"] is False


async def test_start_run_maps_bad_source_to_400(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.sdlc.run_control as rc

    async def fake_start(**_kw: Any) -> dict[str, Any]:
        raise ValueError("Unsupported source kind 'notion'")

    monkeypatch.setattr(rc, "start_run", fake_start)
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/start", json={"source": "notion://1"}, headers=_AUTH)
    assert resp.status_code == 400 and "Unsupported" in resp.json()["detail"]


async def test_start_run_maps_temporal_down_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.sdlc.run_control as rc

    async def fake_start(**_kw: Any) -> dict[str, Any]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(rc, "start_run", fake_start)
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/start", json={"source": "confluence://1"}, headers=_AUTH)
    assert resp.status_code == 503


async def test_start_run_requires_auth() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/start", json={"source": "confluence://1"})
    assert resp.status_code == 401


async def test_inbox_composer_posts_to_start_run() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        js = (await client.get("/static/inbox.js")).text
    assert "/v1/runs/start" in js and "delegate" in js
