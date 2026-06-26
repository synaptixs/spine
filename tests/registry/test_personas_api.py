"""Personas + skills read API (unified UI — P1a)."""

from __future__ import annotations

import httpx

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings

_AUTH = {"X-API-Key": "dev-key"}


def _no_db_app() -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def test_personas_endpoint_lists_the_swe_persona() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/personas", headers=_AUTH)
    assert resp.status_code == 200
    items = resp.json()["items"]
    swe = next(p for p in items if p["id"] == "persona.software_engineer")
    assert swe["workflow_slot"] == "implement"
    assert "python-conventions" in swe["skills"]
    assert swe["role"].startswith("You are a senior software engineer")


async def test_skills_endpoint_reports_vetting_and_provenance() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/skills", headers=_AUTH)
    assert resp.status_code == 200
    items = {s["id"]: s for s in resp.json()["items"]}
    assert "repo-pkg-grounding" in items
    # Native skills are trusted by construction.
    assert items["repo-pkg-grounding"]["origin"] == "native"
    assert items["repo-pkg-grounding"]["vetting"] == "approved"
    # Catalog-wired skills are "active"; the SWE candidates are inert "candidate"s
    # until the measurement promotes them, and carry the phase(s) they condition.
    assert items["repo-pkg-grounding"]["status"] == "active"
    assert items["test-strategy"]["status"] == "candidate"
    assert items["test-strategy"]["phases"] == ["author_tests", "refine"]
    assert items["repo-pkg-grounding"]["phases"] == ["implement"]
    # No measured score until a candidate is promoted with evidence.
    assert items["test-strategy"]["score"] is None


async def test_persona_and_skill_reads_require_auth() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/v1/personas")).status_code == 401
        assert (await client.get("/v1/skills")).status_code == 401
