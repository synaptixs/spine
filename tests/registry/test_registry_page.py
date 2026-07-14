"""Phase A1: the registry browser page (templates / contracts / glossary)."""

from __future__ import annotations

import httpx

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings


def _no_db_app() -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def _login(client: httpx.AsyncClient) -> None:
    resp = await client.post("/login", json={"api_key": "dev-key"})
    assert resp.status_code == 204


async def test_registry_page_requires_login_then_renders() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anon = await client.get("/app/registry")
        assert anon.status_code == 303 and anon.headers["location"] == "/login"
        await _login(client)
        resp = await client.get("/app/registry")
    assert resp.status_code == 200
    body = resp.text
    assert "Registry · Spine" in body  # shell <title>
    assert 'class="navlink active"' in body  # nav marks Registry active
    # The three registry sections + their fill targets are present.
    for target in ("agent-templates", "tool-contracts", "glossary"):
        assert f'id="{target}"' in body
    assert "/static/registry.js" in body


async def test_registry_is_in_nav_and_home_cards() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        home = await client.get("/app")
    assert 'href="/app/registry"' in home.text  # reachable from a home card + the sidebar


async def test_registry_script_reads_the_three_apis() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        js = (await client.get("/static/registry.js")).text
    assert "/v1/agent-templates" in js
    assert "/v1/tool-contracts" in js
    assert "/v1/glossary" in js
    assert "/login" in js  # 401 → redirect to login (session auth)
