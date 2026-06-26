"""Block B web-preview tests: form render + read-only preview endpoint.

Drives the ASGI app via ASGITransport with an injected fake service, so no
Confluence/Jira env or network is touched. The fake's ``create_issues`` would
raise if called — proving the preview path is strictly read-only.
"""

from __future__ import annotations

import httpx

from orchestrator.intake.confluence import ConfluenceError
from orchestrator.intake.factory import IntakeNotConfiguredError
from orchestrator.intake.gaps import GapFinding, GapSeverity
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import FeatureSpec
from orchestrator.intake.web import create_app


class _FakeService:
    def __init__(self, plan: BacklogPlan) -> None:
        self._plan = plan
        self.analyzed: list[str] = []

    async def analyze(self, root_id: str) -> BacklogPlan:
        self.analyzed.append(root_id)
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


def _app(plan: BacklogPlan | None = None, *, builder=None):  # type: ignore[no-untyped-def]
    if builder is None:
        fake = _FakeService(plan or _plan())

        def builder(*, dry_run: bool, rules_path: str | None = None):  # type: ignore[no-untyped-def]
            return fake

    return create_app(service_builder=builder)


async def _client(app) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_index_serves_form() -> None:
    async with await _client(_app()) as c:
        resp = await c.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Confluence" in resp.text


async def test_healthz() -> None:
    async with await _client(_app()) as c:
        resp = await c.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_preview_returns_plan() -> None:
    async with await _client(_app()) as c:
        resp = await c.post("/v1/intake/preview", json={"source": "confluence://123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["documents"] == 1
    assert body["blocked"] is True
    assert body["gaps"][0]["severity"] == "needs_input"
    assert body["specs"][0]["title"] == "Add CSV export"
    assert body["specs"][0]["issue_summary"] == "Add CSV export"


async def test_preview_rejects_bad_uri() -> None:
    async with await _client(_app()) as c:
        resp = await c.post("/v1/intake/preview", json={"source": "no-scheme"})
    assert resp.status_code == 400


async def test_preview_rejects_non_confluence_kind() -> None:
    async with await _client(_app()) as c:
        resp = await c.post("/v1/intake/preview", json={"source": "notion://abc"})
    assert resp.status_code == 400
    assert "confluence" in resp.json()["detail"]


async def test_preview_maps_upstream_error_to_502() -> None:
    class _Boom:
        async def analyze(self, root_id: str) -> BacklogPlan:
            raise ConfluenceError("GET /pages/123 failed: HTTP 404")

    def builder(*, dry_run: bool, rules_path: str | None = None):  # type: ignore[no-untyped-def]
        return _Boom()

    async with await _client(_app(builder=builder)) as c:
        resp = await c.post("/v1/intake/preview", json={"source": "confluence://123"})
    assert resp.status_code == 502
    assert "404" in resp.json()["detail"]


async def test_preview_maps_not_configured_to_400() -> None:
    def builder(*, dry_run: bool, rules_path: str | None = None):  # type: ignore[no-untyped-def]
        raise IntakeNotConfiguredError("Confluence not configured.")

    async with await _client(_app(builder=builder)) as c:
        resp = await c.post("/v1/intake/preview", json={"source": "confluence://123"})
    assert resp.status_code == 400
    assert "not configured" in resp.json()["detail"]
