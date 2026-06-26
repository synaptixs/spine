"""End-to-end sequential workflow test: data_analyst -> business_writer.

Run with ``pytest -m integration`` and a live Postgres. Uses MockLLMClient
so no LLM provider key is required.
"""

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


async def _publish_template(session: AsyncSession, yaml_name: str) -> None:
    template = load_agent_template(EXAMPLES_ROOT / "templates" / yaml_name)
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


def _analyst_output() -> dict[str, Any]:
    return {
        "confidence": 0.85,
        "caveats": ["Single-pass; no rerun for cross-check."],
        "findings": "Q1 revenue rose 12% QoQ, driven by self-serve sign-ups (+38% MoM in March).",
        "claims": [
            {
                "id": "c_q1_growth",
                "statement": "Q1 revenue rose 12% QoQ.",
                "claim_type": "metric",
                "supporting_artifacts": [{"artifact_id": "fixture://q1-warehouse-extract"}],
                "metric_values": {"qoq_growth": 0.12},
                "confidence": 0.9,
                "caveats": [],
            }
        ],
    }


def _writer_output() -> dict[str, Any]:
    return {
        "confidence": 0.8,
        "caveats": ["Adapted from analyst findings; numbers unchanged."],
        "narrative": (
            "Q1 revenue grew 12% quarter-over-quarter, with self-serve sign-ups the "
            "primary driver — March alone added 38% more new accounts than February."
        ),
    }


def _router_llm() -> MockLLMClient:
    """Returns canned planner/analyst/writer responses by detecting agent id in the system prompt."""
    client = MockLLMClient()

    plan = {
        "pattern": "sequential",
        "justification": "objective has analysis and a separate write-up audience",
        "steps": [
            {
                "template_id": "agent.data_analyst",
                "template_version": "0.1.0",
                "node_id": "n_analyst",
                "inputs_from": {
                    "dataset_reference": "task_metadata.dataset_reference",
                    "time_period": "task_metadata.time_period",
                    "business_question": "task_metadata.objective",
                },
            },
            {
                "template_id": "agent.business_writer",
                "template_version": "0.1.0",
                "node_id": "n_writer",
                "inputs_from": {
                    "findings": "node_outputs.n_analyst.findings",
                    "audience": "task_metadata.audience",
                },
            },
        ],
    }

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        system = messages[0].content if messages else ""
        if "orchestration planner" in system.lower():
            text = json.dumps(plan)
        elif "agent.data_analyst" in system:
            text = json.dumps(_analyst_output())
        elif "agent.business_writer" in system:
            text = json.dumps(_writer_output())
        else:
            raise AssertionError(f"no canned response for system prompt: {system[:120]!r}")
        return CompletionResult(
            text=text,
            model="claude-opus-4-7",
            prompt_tokens=50,
            completion_tokens=50,
            cost_usd=0.001,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


async def test_sequential_data_analyst_to_business_writer(session: AsyncSession) -> None:
    await _publish_template(session, "data_analyst.yaml")
    await _publish_template(session, "business_writer.yaml")
    app = create_app(_settings(), llm_client=_router_llm())

    # Sprint 10: default verifier chain runs after each agent. Pre-load the
    # warehouse fixture so EvidenceVerifier's spot-check on the analyst's
    # qoq_growth claim resolves to a real artifact with the matching metric.
    from orchestrator.runtime import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    await store.put_json(
        "fixture://q1-warehouse-extract",
        {"metrics": {"qoq_growth": 0.12}, "source": "warehouse:orders"},
    )
    app.state.artifact_store = store

    headers = {"X-API-Key": API_KEY, "X-Trace-Id": "trace-seq-1"}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
            headers=headers,
        ) as client,
    ):
        response = await client.post(
            "/v1/tasks",
            json={
                "objective": "Analyze Q1 revenue and brief the exec team.",
                "workflow_pattern": "sequential",
                "glossary": {
                    "dataset_reference": "snowflake://prod/orders",
                    "time_period": "2026-01-01/2026-03-31",
                    "audience": "exec leadership",
                },
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["workflow_pattern"] == "sequential"
    assert [t["id"] for t in body["templates"]] == ["agent.data_analyst", "agent.business_writer"]
    # Terminal output is the writer's narrative; both verifiers pass.
    assert body["verifier"]["outcome"] == "pass"
    assert "narrative" in body["output"]
    # Each per-stage output landed in node_outputs.
    assert body["node_outputs"]["n_analyst"]["confidence"] == 0.85
    assert body["node_outputs"]["n_writer"]["narrative"].startswith("Q1 revenue grew 12%")
    # Per-stage verifiers landed too.
    assert body["node_outputs"]["verify_n_analyst"]["outcome"] == "pass"
    assert body["node_outputs"]["verify_n_writer"]["outcome"] == "pass"
