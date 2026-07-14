"""Delegation inbox page (unified UI — P2a)."""

from __future__ import annotations

import httpx

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings


def _no_db_app() -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def test_inbox_requires_login_then_renders() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anon = await client.get("/app/inbox")
        assert anon.status_code == 303 and anon.headers["location"] == "/login"
        await client.post("/login", json={"api_key": "dev-key"})
        resp = await client.get("/app/inbox")
    assert resp.status_code == 200
    assert "Inbox · Spine" in resp.text
    assert 'class="navlink active"' in resp.text  # Inbox active in the nav
    assert "/static/inbox.js" in resp.text and "/static/inbox.css" in resp.text
    # Guidance layer: a backend status strip + the equivalent CLI command shown.
    assert 'id="status"' in resp.text and 'class="cli"' in resp.text
    assert "orchestrator sdlc run --source" in resp.text
    # Phase 3: first-run onboarding on the front door, composer help + safe-by-default
    # in plain language, and a cross-link explaining when to use the Console.
    assert "How delegating works" in resp.text
    assert "What's a source?" in resp.text and "Safe by default" in resp.text
    assert 'href="/console"' in resp.text and "richer approval" in resp.text


async def test_inbox_script_consumes_the_live_stream_and_gates() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/static/inbox.js")
    assert resp.status_code == 200
    js = resp.text
    assert 'new EventSource("/v1/stream")' in js  # live updates via SSE
    assert "/v1/runs" in js and "/v1/approvals" in js  # feed + gates
    assert "approve" in js and "reject" in js  # inline gate decisions
    assert "/login" in js  # 401 → login
    assert "checkBackend" in js and "stage(" in js  # status strip + plain-language stages
    assert "lifecycle(" in js  # plain-language run status (In progress / Delivered / …)
