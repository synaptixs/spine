"""Phase E — advanced/experimental: evals (E1), cross-run memory (E2), advanced (E3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import MemoryRow
from orchestrator.registry.repositories import MemoryRepo

_AUTH = {"X-API-Key": "dev-key"}


def _no_db_app() -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def _login(c: httpx.AsyncClient) -> None:
    assert (await c.post("/login", json={"api_key": "dev-key"})).status_code == 204


# --------------------------------------------------------------------------- #
# E2 — cross-run memory
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def mem_app() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, MemoryRow.__table__).create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        repo = MemoryRepo(s)
        await repo.add(
            repo_key="acme/app",
            kind="convention",
            confidence=0.8,
            statement="Use absolute imports throughout the package.",
            evidence={"run_ids": ["run-1"]},
        )
        await repo.add(
            repo_key="acme/app",
            kind="pitfall",
            confidence=0.6,
            statement="Do not shadow the stdlib json module.",
            evidence={"run_ids": ["run-2", "run-3"]},
        )
        await repo.add(
            repo_key="other/repo",
            kind="convention",
            confidence=0.9,
            statement="Prefer dependency injection over globals.",
        )
        await s.commit()

    app = create_app(Settings())
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.session_factory = factory
    yield SimpleNamespace(app=app)
    await engine.dispose()


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=_AUTH)  # type: ignore[arg-type]


async def test_memory_list_filter_and_search(mem_app: SimpleNamespace) -> None:
    async with _client(mem_app.app) as c:
        all_items = (await c.get("/v1/memory")).json()["items"]
        assert len(all_items) == 3
        assert all_items[0]["confidence"] == 0.9  # confidence-desc ordering

        acme = (await c.get("/v1/memory?repo_key=acme/app")).json()["items"]
        assert len(acme) == 2 and all(m["repo_key"] == "acme/app" for m in acme)

        pit = (await c.get("/v1/memory?repo_key=acme/app&kind=pitfall")).json()["items"]
        assert len(pit) == 1 and pit[0]["kind"] == "pitfall"

        # search ranks by keyword overlap within the repo
        found = (await c.get("/v1/memory?repo_key=acme/app&query=imports")).json()["items"]
        assert any("absolute imports" in m["statement"] for m in found)

        repos = (await c.get("/v1/memory/repos")).json()["repos"]
        assert repos == ["acme/app", "other/repo"]


async def test_memory_requires_auth(mem_app: SimpleNamespace) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mem_app.app), base_url="http://test") as c:
        assert (await c.get("/v1/memory")).status_code == 401


# --------------------------------------------------------------------------- #
# E3 — advanced capability flags
# --------------------------------------------------------------------------- #
async def test_advanced_reports_flag_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_AGENTIC_CODEGEN", "1")  # truthy-mode → on
    monkeypatch.setenv("SPINE_ONTOMESH_URL", "http://onto.local")  # present-mode → on
    monkeypatch.delenv("ORCHESTRATOR_SEMANTIC_MEMORY", raising=False)  # → off
    app = _no_db_app()
    async with _client(app) as c:
        feats = {f["key"]: f for f in (await c.get("/v1/system/advanced")).json()["features"]}
    assert feats["SDLC_AGENTIC_CODEGEN"]["enabled"] is True
    assert feats["SPINE_ONTOMESH_URL"]["enabled"] is True
    assert feats["ORCHESTRATOR_SEMANTIC_MEMORY"]["enabled"] is False
    # kinds are set for grouping
    assert feats["SDLC_AGENTIC_CODEGEN"]["kind"] == "loop"


async def test_advanced_never_leaks_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_ONTOMESH_URL", "http://secret-internal-host:9999/x")
    app = _no_db_app()
    async with _client(app) as c:
        text = (await c.get("/v1/system/advanced")).text
    assert "secret-internal-host" not in text  # presence only, never the value


# --------------------------------------------------------------------------- #
# Pages + nav
# --------------------------------------------------------------------------- #
async def test_phase_e_pages_render_and_navigate() -> None:
    app = _no_db_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:  # type: ignore[arg-type]
        assert (await c.get("/app/memory")).status_code == 303  # login required
        await _login(c)
        mem = await c.get("/app/memory")
        evals = await c.get("/app/evals")
        adv = await c.get("/app/advanced")
        home = await c.get("/app")
        mem_js = (await c.get("/static/memory.js")).text
        evals_js = (await c.get("/static/evals.js")).text
        adv_js = (await c.get("/static/advanced.js")).text
    assert "Cross-run memory · Spine" in mem.text and "/static/memory.js" in mem.text
    assert "Evals · Spine" in evals.text and "/static/evals.js" in evals.text
    assert "Advanced · Spine" in adv.text and "/static/advanced.js" in adv.text
    assert "/v1/memory" in mem_js and "/v1/skills" in evals_js and "/v1/system/advanced" in adv_js
    assert ">Quality</p>" in home.text  # sidebar section
    for href in ("/app/evals", "/app/memory", "/app/advanced"):
        assert f'href="{href}"' in home.text
