"""Phase C — Governance & trust: audit query (C1), policy/budget (C2), export (C3)."""

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
from orchestrator.registry.db.models import AuditLogRow
from orchestrator.registry.repositories import AuditLogRepo

_AUTH = {"X-API-Key": "dev-key"}
RUN = "RUN-C"


@pytest_asyncio.fixture
async def seeded() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, AuditLogRow.__table__).create)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def w(**kw: object) -> None:
        async with factory() as s:
            kw.setdefault("trace_id", RUN)
            await AuditLogRepo(s).write(**kw)  # type: ignore[arg-type]
            await s.commit()

    await w(actor="worker", action="sdlc_intake_analyzed", resource_type="sdlc", resource_id=RUN)
    await w(
        actor="worker",
        action="tool_invocation",
        resource_type="tool_contract",
        resource_id="tool.a",
        after={"cost_usd": 0.5, "task_id": RUN},
    )
    await w(
        actor="worker",
        action="tool_invocation",
        resource_type="tool_contract",
        resource_id="tool.b",
        after={"cost_usd": 1.25, "task_id": RUN},
    )
    await w(
        actor="worker",
        action="verifier_execution",
        resource_type="task",
        resource_id=RUN,
        after={"verifier_id": "policy", "outcome": "WARN", "payload": {"pii_email": {"field": "x"}}},
    )
    await w(actor="alice", action="approval_approve", resource_type="approval", resource_id=RUN)
    await w(
        actor="worker",
        action="sdlc_budget_exhausted",
        resource_type="sdlc",
        resource_id=RUN,
        after={"stage": "codegen", "spent_usd": 26.0, "max_cost_usd": 25.0},
    )
    await w(actor="worker", action="sdlc_prs_merged", resource_type="sdlc", resource_id=RUN)

    app = create_app(Settings())
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.session_factory = factory
    yield SimpleNamespace(app=app)
    await engine.dispose()


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=_AUTH)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# C1 — audit query
# --------------------------------------------------------------------------- #
async def test_audit_query_and_filters(seeded: SimpleNamespace) -> None:
    async with _client(seeded.app) as c:
        allrows = (await c.get("/v1/audit")).json()["items"]
        assert len(allrows) == 7
        # newest-first ordering
        assert allrows[0]["action"] == "sdlc_prs_merged"

        by_action = (await c.get("/v1/audit?action=sdlc_prs_merged")).json()["items"]
        assert len(by_action) == 1

        by_type = (await c.get("/v1/audit?resource_type=tool_contract")).json()["items"]
        assert len(by_type) == 2

        by_actor = (await c.get("/v1/audit?actor=alice")).json()["items"]
        assert len(by_actor) == 1 and by_actor[0]["action"] == "approval_approve"

        by_run = (await c.get(f"/v1/audit?run_id={RUN}")).json()["items"]
        assert len(by_run) == 7  # resource_id OR trace_id match


async def test_audit_requires_auth(seeded: SimpleNamespace) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seeded.app), base_url="http://test") as c:
        assert (await c.get("/v1/audit")).status_code == 401


# --------------------------------------------------------------------------- #
# C2 — policy & budget
# --------------------------------------------------------------------------- #
async def test_governance_derivation(seeded: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_RUN_BUDGET_USD", "1.0")  # below the 1.75 tool cost → over cap
    async with _client(seeded.app) as c:
        g = (await c.get(f"/v1/audit/{RUN}/governance")).json()
    assert g["spend"]["tool_cost_usd"] == 1.75
    assert g["spend"]["tool_calls"] == 2
    assert g["spend"]["budget_cap_usd"] == 1.0 and g["spend"]["over_cap"] is True
    assert g["spend"]["breaches"][0]["stage"] == "codegen"
    assert g["policy"][0]["outcome"] == "WARN" and g["policy"][0]["rules"] == ["pii_email"]
    assert g["approvals"][0]["action"] == "approval_approve"
    assert "not persisted" in g["note"]  # the honest caveat


async def test_governance_404_for_unknown_run(seeded: SimpleNamespace) -> None:
    async with _client(seeded.app) as c:
        assert (await c.get("/v1/audit/nope/governance")).status_code == 404


# --------------------------------------------------------------------------- #
# C3 — export
# --------------------------------------------------------------------------- #
async def test_export_bundle(seeded: SimpleNamespace) -> None:
    async with _client(seeded.app) as c:
        resp = await c.get(f"/v1/audit/{RUN}/export")
    assert resp.status_code == 200
    assert f'filename="{RUN}.bundle.json"' in resp.headers["content-disposition"]
    bundle = resp.json()
    assert bundle["run_id"] == RUN and bundle["state"] == "merged" and bundle["events"] == 7
    assert len(bundle["timeline"]) == 7
    assert bundle["governance"]["spend"]["tool_cost_usd"] == 1.75


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
async def test_governance_pages_render() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub"))
    app.router.lifespan_context = None  # type: ignore[assignment]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/app/audit")).status_code == 303  # login required
        await c.post("/login", json={"api_key": "dev-key"})
        audit = await c.get("/app/audit")
        gov = await c.get("/app/governance")
        home = await c.get("/app")
    assert "Audit log · Spine" in audit.text and "/static/audit.js" in audit.text
    assert "Policy &amp; budget · Spine" in gov.text and "/static/governance.js" in gov.text
    assert ">Govern</p>" in home.text  # sidebar section
    assert 'href="/app/audit"' in home.text and 'href="/app/governance"' in home.text
