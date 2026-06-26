"""Golden walking-skeleton test: submit a research task, end to end.

- Default (mocked) variant: ``pytest -m integration``. Uses MockLLMClient,
  needs only a live Postgres. Runs in CI's nightly integration job.
- Real LLM variant: ``pytest -m "integration and real_llm"``. Hits the
  configured provider, needs ANTHROPIC_API_KEY / OPENAI_API_KEY.

This test pins the contract every later sprint must keep honouring: an
objective enters, a published agent template runs, the verifier passes,
and an audit row records the outcome.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.llm import CompletionResult, LiteLLMClient, Message, MockLLMClient
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import AgentTemplateRow, AuditLogRow
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


async def _publish_research_template(session: AsyncSession) -> None:
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


def _canned_research_output() -> dict[str, Any]:
    return {
        "confidence": 0.8,
        "caveats": ["Single-pass research; no cross-source verification."],
        "findings": "Antibiotics treat bacterial infections, not viral ones.",
        "claims": [
            {
                "id": "c_atbio_1",
                "statement": "Antibiotics are ineffective against viruses.",
                "claim_type": "qualitative",
                "supporting_artifacts": [{"artifact_id": "fixture://cdc-guideline"}],
                "confidence": 0.9,
                "caveats": [],
            }
        ],
    }


def _mock_llm_returning(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        return CompletionResult(
            text=text,
            model="claude-opus-4-7",
            prompt_tokens=120,
            completion_tokens=80,
            cost_usd=0.002,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


async def test_golden_research_task_via_mock_llm(session: AsyncSession) -> None:
    await _publish_research_template(session)
    payload_text = json.dumps(_canned_research_output())
    app = create_app(_settings(), llm_client=_mock_llm_returning(payload_text))

    # Sprint 10: the default verifier chain runs after the agent. Pre-load the
    # one cited artifact so EvidenceVerifier's spot-check can find it. Without
    # this the chain correctly fails on artifact_not_found.
    from orchestrator.runtime import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    await store.put_json("fixture://cdc-guideline", {"findings": "antibiotics target bacteria"})
    app.state.artifact_store = store

    headers = {"X-API-Key": API_KEY, "X-Trace-Id": "trace-golden-1"}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test", headers=headers
        ) as client,
    ):
        response = await client.post(
            "/v1/tasks",
            json={
                "objective": "Do antibiotics work against viral infections?",
                "template": {"id": "agent.research"},
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()

    # Walking-skeleton contract that later sprints must preserve.
    assert body["templates"] == [{"id": "agent.research", "version": "0.1.0"}]
    assert body["workflow_pattern"] == "single_agent"
    assert body["trace_id"] == "trace-golden-1"
    assert body["verifier"]["outcome"] == "pass"

    output = body["output"]
    assert {"confidence", "caveats", "findings", "claims"} <= set(output)
    assert 0.0 <= output["confidence"] <= 1.0
    assert isinstance(output["caveats"], list)
    assert len(output["claims"]) >= 1
    assert all("supporting_artifacts" in c and c["supporting_artifacts"] for c in output["claims"])

    rows = (await session.execute(select(AuditLogRow))).scalars().all()
    submits = [r for r in rows if r.action == "task_submit"]
    assert len(submits) == 1
    assert submits[0].after_json is not None
    assert submits[0].after_json["verifier_outcome"] == "pass"
    assert submits[0].trace_id == "trace-golden-1"


@pytest.mark.real_llm
async def test_golden_research_task_via_real_llm(session: AsyncSession) -> None:
    """Hits the provider configured via env. Skipped unless ``-m real_llm`` is set."""
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")):
        pytest.skip("no provider API key in environment")

    await _publish_research_template(session)
    app = create_app(_settings(), llm_client=LiteLLMClient())

    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
            headers=headers,
            timeout=httpx.Timeout(60.0),
        ) as client,
    ):
        response = await client.post(
            "/v1/tasks",
            json={
                "objective": "Do antibiotics work against viral infections?",
                "template": {"id": "agent.research"},
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    output = body["output"]
    assert {"confidence", "caveats", "findings", "claims"} <= set(output)
    assert 0.0 <= float(output["confidence"]) <= 1.0
