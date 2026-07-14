"""Backlog preview folded into the registry app (unified UI, P0)."""

from __future__ import annotations

import httpx

from orchestrator.intake.gaps import GapFinding, GapSeverity
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import FeatureSpec
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings


class _FakeService:
    def __init__(self, plan: BacklogPlan) -> None:
        self._plan = plan

    async def analyze(self, root_id: str) -> BacklogPlan:
        return self._plan

    async def create_issues(self, *a: object, **k: object) -> None:  # pragma: no cover
        raise AssertionError("preview must never create issues")


def _plan() -> BacklogPlan:
    return BacklogPlan(
        documents=[SourceDocument(id="p1", title="Reqs", body="Need CSV export.")],
        intents=[],
        gaps=[
            GapFinding(
                rule_id="needs_input.open_questions",
                intent_id="add-csv-export",
                severity=GapSeverity.NEEDS_INPUT,
                message="Has open questions.",
            )
        ],
        specs=[
            FeatureSpec(
                intent_id="add-csv-export",
                title="Add CSV export",
                summary="Export the grid to CSV.",
                acceptance_criteria=["Downloads in <5s for 10k rows."],
                estimate="M",
            )
        ],
        blocked=True,
        truncated=False,
    )


def _app(*, builder: object = None) -> object:
    settings = Settings(database_url="postgresql+psycopg://stub/stub")
    app = create_app(settings)
    app.router.lifespan_context = None  # type: ignore[assignment]
    if builder is not None:
        app.state.intake_service_builder = builder
    return app


_AUTH = {"X-API-Key": "dev-key"}  # require_principal accepts the key (or a session)


async def test_backlog_page_requires_login_then_renders() -> None:
    app = _app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anon = await client.get("/app/backlog")
        assert anon.status_code == 303 and anon.headers["location"] == "/login"
        await client.post("/login", json={"api_key": "dev-key"})
        resp = await client.get("/app/backlog")
    assert resp.status_code == 200
    assert "Backlog · Spine" in resp.text  # shell title
    assert 'class="navlink active"' in resp.text  # Backlog active in the shared nav
    assert "/static/intake.css" in resp.text and "/static/intake.js" in resp.text
    assert "Confluence" in resp.text


async def test_preview_requires_auth() -> None:
    app = _app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/intake/preview", json={"source": "confluence://1"})
    assert resp.status_code == 401  # the preview endpoint is no longer open


async def test_preview_runs_read_only_against_an_injected_service() -> None:
    fake = _FakeService(_plan())

    def builder(*, dry_run: bool, rules_path: str | None = None) -> _FakeService:
        return fake

    app = _app(builder=builder)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/intake/preview", json={"source": "confluence://123"}, headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["documents"] == 1 and body["blocked"] is True
    assert body["specs"][0]["title"] == "Add CSV export"


async def test_preview_accepts_non_confluence_source() -> None:
    # Preview is no longer confluence-only (D2): a non-confluence scheme now
    # reaches the builder instead of being pre-rejected. (Previously notion://
    # 400'd before the builder was even consulted.)
    fake = _FakeService(_plan())

    def builder(*, dry_run: bool, rules_path: str | None = None) -> _FakeService:
        return fake

    app = _app(builder=builder)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/intake/preview", json={"source": "notion://123"}, headers=_AUTH)
    assert resp.status_code == 200
