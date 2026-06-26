"""Operator console (G12): the data-free shell + the runs summarizer."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.runs import _summarize
from orchestrator.registry.db.models import AuditLogRow


def _no_db_app() -> object:
    settings = Settings(database_url="postgresql+psycopg://stub/stub")
    app = create_app(settings)
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def _login(client: httpx.AsyncClient) -> None:
    resp = await client.post("/login", json={"api_key": "dev-key"})
    assert resp.status_code == 204


async def test_console_requires_login_then_renders_through_the_shell() -> None:
    """The /console page requires a session, then renders through the shared web
    shell (nav + one stylesheet), referencing real static assets."""
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anon = await client.get("/console")
        assert anon.status_code == 303 and anon.headers["location"] == "/login"
        await _login(client)
        resp = await client.get("/console")
    assert resp.status_code == 200
    body = resp.text
    assert "Console · Orchestrator" in body  # shell <title>
    assert 'class="navlink active"' in body  # shared nav, Console marked active
    assert "/static/app.css" in body and "/static/console.css" in body
    assert "/static/console.js" in body  # JS is a real file, not inline


async def test_console_script_drives_the_session_authed_api() -> None:
    """The console JS (a real static asset) wires the API + gate actions + the
    guarded live-poll loop, and bounces to /login on a 401 (session-based auth)."""
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/static/console.js")  # static asset, not auth-gated
    assert resp.status_code == 200
    js = resp.text
    assert "/v1/approvals" in js and "/v1/runs" in js
    assert "/login" in js  # 401 → redirect to login (no typed API key anymore)
    for action in ("approve", "reject", "modify_input"):
        assert action in js
    assert "setInterval" in js
    assert "anyDetailOpen" in js and "document.hidden" in js


def _row(action: str, minute: int) -> AuditLogRow:
    """A transient AuditLogRow (unpersisted) — the summarizer only reads
    ``action`` and ``timestamp``."""
    return AuditLogRow(
        actor="system",
        action=action,
        resource_type="sdlc",
        resource_id="run",
        timestamp=datetime(2026, 6, 14, 10, minute, tzinfo=UTC),
    )


def test_summarize_derives_merged_state() -> None:
    # newest-first, as the query returns them
    events = [_row("sdlc_prs_merged", 9), _row("sdlc_issues_created", 2), _row("sdlc_intake_analyzed", 1)]
    s = _summarize("run-1", events)
    assert s.state == "merged"
    assert s.last_action == "sdlc_prs_merged"
    assert s.events == 3
    assert s.started_at.endswith("10:01:00+00:00")
    assert s.updated_at.endswith("10:09:00+00:00")


def test_summarize_running_when_no_terminal_action() -> None:
    s = _summarize("run-2", [_row("sdlc_issues_created", 3), _row("sdlc_intake_analyzed", 1)])
    assert s.state == "running"


def test_summarize_denied_and_failed() -> None:
    assert _summarize("r", [_row("sdlc_merge_denied", 5)]).state == "denied"
    assert _summarize("r", [_row("sdlc_features_failed", 5)]).state == "failed"
    assert _summarize("r", [_row("sdlc_cancelled", 5)]).state == "cancelled"
