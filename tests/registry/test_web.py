"""Unified UI (P0): the shared shell, home landing, and static assets."""

from __future__ import annotations

import httpx

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.web.shell import page_shell


def _no_db_app() -> object:
    settings = Settings(database_url="postgresql+psycopg://stub/stub")
    app = create_app(settings)
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


class TestPageShell:
    def test_includes_nav_and_marks_active(self) -> None:
        out = page_shell(title="Home", active="Home", body="<p>hi</p>")
        assert '<link rel="stylesheet" href="/static/app.css">' in out
        assert 'href="/console"' in out and 'href="/app"' in out
        assert 'class="navlink active"' in out  # the active item is marked
        assert "<p>hi</p>" in out

    def test_escapes_title(self) -> None:
        out = page_shell(title="<x>", active="", body="")
        assert "<x>" not in out and "&lt;x&gt;" in out
        assert "navlink active" not in out  # nothing active for out-of-nav pages

    def test_nav_is_grouped_into_sections(self) -> None:
        out = page_shell(title="Home", active="Inbox", body="")
        # Grouped sidebar: section wrappers + headings, links still present.
        assert 'class="nav-section"' in out
        assert ">Deliver</p>" in out and ">Registry</p>" in out
        assert 'href="/app/inbox"' in out and 'class="navlink active"' in out


async def _login(client: httpx.AsyncClient) -> None:
    resp = await client.post("/login", json={"api_key": "dev-key"})
    assert resp.status_code == 204


async def test_home_page_requires_login_then_renders() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anon = await client.get("/app")  # unauthenticated → redirect to /login
        assert anon.status_code == 303 and anon.headers["location"] == "/login"
        await _login(client)  # the session cookie carries through the client
        resp = await client.get("/app")
    assert resp.status_code == 200
    assert "Spine" in resp.text  # branded as the product, not "Orchestrator"
    assert 'href="/console"' in resp.text  # nav + cards link the surfaces
    assert "/static/app.css" in resp.text
    # Capabilities are surfaced on Home (the "what can I do" gap — Phase 3, Step 8).
    assert "What Spine can build" in resp.text
    for capability in ("Python", "TypeScript", "existing repos", "Safe by default"):
        assert capability in resp.text


async def test_login_rejects_a_bad_key() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/login", json={"api_key": "wrong"})
    assert resp.status_code == 401


async def test_home_cards_link_the_full_surface() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        resp = await client.get("/app")
    for href in ("/app/inbox", "/console", "/app/backlog", "/app/personas"):
        assert f'href="{href}"' in resp.text


async def test_personas_browser_requires_login_then_renders() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anon = await client.get("/app/personas")
        assert anon.status_code == 303 and anon.headers["location"] == "/login"
        await _login(client)
        resp = await client.get("/app/personas")
    assert resp.status_code == 200
    assert "Personas · Spine" in resp.text
    assert "/static/personas.js" in resp.text


async def test_personas_browser_script_reads_the_api() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        js = (await client.get("/static/personas.js")).text
    assert "/v1/personas" in js and "/v1/skills" in js


async def test_root_redirects_to_the_app() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")  # no following — the root should 307 → /app
        assert resp.status_code == 307 and resp.headers["location"] == "/app"
        favicon = await client.get("/favicon.ico")
        assert favicon.status_code == 204


async def test_static_stylesheet_is_served() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/static/app.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]
    assert ".navlink" in resp.text  # the real stylesheet, not a Python string
