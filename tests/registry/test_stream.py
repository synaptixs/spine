"""Run-state SSE stream (unified UI — P1b): event mapping + the endpoint."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.responses import StreamingResponse

from orchestrator.registry.api import stream as stream_mod
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.deps import Principal
from orchestrator.registry.api.stream import event_type, format_sse, parse_cursor, sse_generator, to_event
from orchestrator.registry.db.models import AuditLogRow

_AUTH = {"X-API-Key": "dev-key"}


def _no_db_app() -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


class TestEventMapping:
    def test_event_type_by_action(self) -> None:
        assert event_type("task_submit", "task") == "run.created"
        assert event_type("sdlc_prs_merged", "sdlc") == "run.completed"
        assert event_type("approval_decided", "approval") == "approval.updated"
        assert event_type("sdlc_codegen", "sdlc") == "run.stage"

    def test_to_event_envelope(self) -> None:
        row = AuditLogRow(
            timestamp=datetime(2026, 6, 24, 10, 0, tzinfo=UTC),
            actor="worker",
            action="sdlc_prs_merged",
            resource_type="sdlc",
            resource_id="DRY-1",
            after_json={"pr": 128},
        )
        ev = to_event(row)
        assert ev["type"] == "run.completed" and ev["run_id"] == "DRY-1"
        assert ev["payload"]["state"] == "merged" and ev["payload"]["after"] == {"pr": 128}
        assert ev["seq"] == ev["ts"] == "2026-06-24T10:00:00+00:00"

    def test_format_sse_is_a_valid_message(self) -> None:
        sse = format_sse({"seq": "s1", "type": "run.stage", "run_id": "r"})
        assert "id: s1\n" in sse and "event: run.stage\n" in sse
        assert sse.startswith("id:") and sse.endswith("\n\n")

    def test_parse_cursor(self) -> None:
        assert parse_cursor(None) is None
        assert parse_cursor("not-a-date") is None
        assert parse_cursor("2026-06-24T10:00:00+00:00") == datetime(2026, 6, 24, 10, 0, tzinfo=UTC)


async def test_stream_requires_auth() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/stream")
    assert resp.status_code == 401


async def test_sse_generator_emits_connect_then_events(monkeypatch: pytest.MonkeyPatch) -> None:
    row = AuditLogRow(
        timestamp=datetime(2026, 6, 24, 10, 0, tzinfo=UTC),
        actor="worker",
        action="sdlc_codegen",
        resource_type="sdlc",
        resource_id="R1",
    )
    calls = {"n": 0}

    async def fake_fetch(factory: object, tenant: str, run_id: object, since: object) -> list[AuditLogRow]:
        calls["n"] += 1
        return [row] if calls["n"] == 1 else []  # one row on the first poll, then idle

    monkeypatch.setattr(stream_mod, "_fetch_new", fake_fetch)
    gen = sse_generator(None, "default", None, None)
    assert await anext(gen) == ": connected\n\n"  # immediate, before any DB poll
    msg = await asyncio.wait_for(anext(gen), 2)  # the event from the first poll
    assert "event: run.stage" in msg and "R1" in msg
    await gen.aclose()


async def test_stream_endpoint_returns_an_event_stream_response() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=None)), headers={})
    resp = await stream_mod.stream(request, Principal(id="u"), run_id=None)  # type: ignore[arg-type]
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache"
