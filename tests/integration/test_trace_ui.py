"""Integration tests for the trace UI endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import AgentTemplateRow
from orchestrator.registry.loader import load_agent_template
from orchestrator.registry.repositories import VersionedRepo

pytestmark = pytest.mark.integration

API_KEY = "test-key"
EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples"


def _settings() -> Settings:
    return Settings(
        database_url=os.getenv(
            "ORCHESTRATOR_TEST_DATABASE_URL",
            "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
        ),
        api_key=API_KEY,
    )


async def _publish_research(session: AsyncSession) -> None:
    template = load_agent_template(EXAMPLES_ROOT / "templates" / "research_agent.yaml")
    repo = VersionedRepo(session, AgentTemplateRow)
    await repo.create(
        id=template.metadata.id,
        version=template.metadata.version,
        description=template.metadata.description,
        tags=list(template.metadata.tags),
        spec=template.spec.model_dump(mode="json"),
    )
    await repo.publish(template.metadata.id, template.metadata.version)
    await session.commit()


def _canned_output() -> dict[str, Any]:
    return {
        "confidence": 0.8,
        "caveats": ["single-pass research"],
        "findings": "Vitamin C does not prevent the common cold in the general population.",
        "claims": [
            {
                "id": "c_vc_1",
                "statement": "No effect on incidence in general population.",
                "claim_type": "qualitative",
                "supporting_artifacts": [{"artifact_id": "fixture://cochrane"}],
                "confidence": 0.9,
                "caveats": [],
            }
        ],
    }


def _llm() -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        return CompletionResult(
            text=json.dumps(_canned_output()),
            model="claude-opus-4-7",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=0.0001,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


async def test_trace_json_returns_audit_for_completed_task(session: AsyncSession) -> None:
    await _publish_research(session)
    app = create_app(_settings(), llm_client=_llm())

    # Sprint 10: pre-load the cited artifact so the chain's EvidenceVerifier
    # can resolve it.
    from orchestrator.runtime import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    await store.put_json("fixture://cochrane", {"findings": "supporting evidence"})
    app.state.artifact_store = store

    headers = {"X-API-Key": API_KEY, "X-Trace-Id": "trace-trace-1"}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
            headers=headers,
        ) as client,
    ):
        submit = await client.post(
            "/v1/tasks",
            json={
                "objective": "Does vitamin C prevent colds?",
                "template": {"id": "agent.research"},
            },
        )
        assert submit.status_code == 200, submit.text
        task_id = submit.json()["task_id"]

        trace = await client.get(f"/v1/tasks/{task_id}/trace")
    assert trace.status_code == 200, trace.text
    body = trace.json()
    assert body["task_id"] == task_id
    assert body["workflow_pattern"] == "single_agent"
    assert body["verifier_outcome"] == "pass"
    assert body["templates"] == [{"id": "agent.research", "version": "0.1.0"}]
    submit_rows = [r for r in body["audit"] if r["action"] == "task_submit"]
    assert len(submit_rows) == 1


async def test_trace_html_renders_for_known_task(session: AsyncSession) -> None:
    await _publish_research(session)
    app = create_app(_settings(), llm_client=_llm())

    from orchestrator.runtime import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    await store.put_json("fixture://cochrane", {"findings": "supporting evidence"})
    app.state.artifact_store = store

    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
            headers=headers,
        ) as client,
    ):
        submit = await client.post(
            "/v1/tasks",
            json={
                "objective": "Does vitamin C prevent colds?",
                "template": {"id": "agent.research"},
            },
        )
        task_id = submit.json()["task_id"]

        # HTML page embeds the audit timeline, so it requires a web session (P0b):
        # log in to set the session cookie, then fetch the page.
        await client.post("/login", json={"api_key": API_KEY})
        html = await client.get(f"/trace/{task_id}")
    assert html.status_code == 200
    assert "task_submit" in html.text
    assert "outcome-pass" in html.text
    assert "agent.research" in html.text


async def test_trace_json_returns_404_for_unknown_task(session: AsyncSession) -> None:
    app = create_app(_settings(), llm_client=_llm())
    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
            headers=headers,
        ) as client,
    ):
        response = await client.get("/v1/tasks/not-a-real-task/trace")
    assert response.status_code == 404
