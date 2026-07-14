"""Phase A: persona detail (A2), system/readiness (A3), runs deepening (A4)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast

import httpx
import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import AuditLogRow
from orchestrator.registry.repositories import AuditLogRepo

_AUTH = {"X-API-Key": "dev-key"}


def _no_db_app() -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def _login(client: httpx.AsyncClient) -> None:
    resp = await client.post("/login", json={"api_key": "dev-key"})
    assert resp.status_code == 204


# --------------------------------------------------------------------------- #
# A2 — persona/skill detail
# --------------------------------------------------------------------------- #
async def test_personas_api_exposes_detail_fields() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        resp = await client.get("/v1/personas")
    assert resp.status_code == 200
    persona = resp.json()["items"][0]
    # A2 adds description + the full role (instructions) + the output schema.
    assert persona["description"] and persona["role"]
    assert isinstance(persona["outputs"], list)
    if persona["outputs"]:
        assert {"name", "type", "description"} <= persona["outputs"][0].keys()


async def test_personas_script_renders_instructions_and_outputs() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        js = (await client.get("/static/personas.js")).text
    assert "instructions" in js and "outputsTable" in js
    assert "details" in js  # expandable detail


# --------------------------------------------------------------------------- #
# A3 — system / readiness
# --------------------------------------------------------------------------- #
async def test_readiness_endpoint_returns_checks() -> None:
    app = _no_db_app()  # lifespan nulled → no engine → db not ready, but endpoint still answers
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        resp = await client.get("/v1/system/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["db_ready"] is False and "ok" in data
    assert isinstance(data["checks"], list) and data["checks"]
    assert {"name", "passed", "optional", "detail"} <= data["checks"][0].keys()


async def test_readiness_never_leaks_secret_values() -> None:
    # The doctor checks report variable presence, not values — a token set in the
    # env must not appear in the readiness payload.
    import os

    os.environ["JIRA_API_TOKEN"] = "super-secret-value-123"
    try:
        app = _no_db_app()
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
            resp = await client.get("/v1/system/readiness")
        assert "super-secret-value-123" not in resp.text
    finally:
        del os.environ["JIRA_API_TOKEN"]


async def test_system_page_and_nav() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anon = await client.get("/app/system")
        assert anon.status_code == 303 and anon.headers["location"] == "/login"
        await _login(client)
        page = await client.get("/app/system")
        home = await client.get("/app")
        js = (await client.get("/static/system.js")).text
    assert "System · Spine" in page.text and 'id="readiness"' in page.text
    assert 'href="/app/system"' in home.text  # nav + home card
    assert "/v1/system/readiness" in js


# --------------------------------------------------------------------------- #
# A4 — runs deepening (filter + inline trace + export)
# --------------------------------------------------------------------------- #
async def test_console_script_has_filter_export_and_trace() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        js = (await client.get("/static/console.js")).text
    assert "renderRuns" in js and "run-filter" in js  # client-side state filter
    assert "exportRun" in js and ".trace.json" in js  # export a run's trace
    assert "/v1/tasks/" in js and "toggleRunDetail" in js  # inline embedded trace


async def test_console_page_has_runs_filter() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _login(client)
        page = await client.get("/console")
    assert "id='run-filter'" in page.text and "value='merged'" in page.text


@pytest_asyncio.fixture
async def db_app() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, AuditLogRow.__table__).create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(Settings())
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.session_factory = factory
    yield SimpleNamespace(app=app, factory=factory)
    await engine.dispose()


async def test_trace_fallback_covers_sdlc_runs(db_app: SimpleNamespace) -> None:
    # SDLC runs record rows under resource_type="sdlc" with trace_id == sdlc_id and
    # no task_submit row — the A4 additive fallback makes /trace surface them.
    async with db_app.factory() as s:
        repo = AuditLogRepo(s)
        for action in ("sdlc_intake_analyzed", "sdlc_prs_merged"):
            await repo.write(
                actor="worker",
                action=action,
                resource_type="sdlc",
                resource_id="RUN-9",
                trace_id="RUN-9",
            )
        await s.commit()

    transport = httpx.ASGITransport(app=db_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        resp = await client.get("/v1/tasks/RUN-9/trace")
    assert resp.status_code == 200, resp.text
    actions = [e["action"] for e in resp.json()["audit"]]
    assert "sdlc_intake_analyzed" in actions and "sdlc_prs_merged" in actions
