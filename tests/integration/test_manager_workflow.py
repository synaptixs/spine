"""End-to-end manager-with-specialists test against real Postgres + MinIO.

Uses the default ``artifacts`` bucket; artifacts are namespaced under
``task/<task_id>/`` so concurrent test runs don't collide.
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
from orchestrator.runtime import ObjectStoreArtifactStore
from orchestrator.storage import ObjectStoreClient

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


async def _publish(session: AsyncSession, yaml_name: str) -> None:
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


def _specialist_output(label: str) -> dict[str, Any]:
    return {
        "confidence": 0.85,
        "caveats": [f"{label}: single-pass research"],
        "findings": f"{label} dimension: signals look favourable.",
        "claims": [
            {
                "id": f"c_{label}_1",
                "statement": f"{label} primary claim.",
                "claim_type": "qualitative",
                "supporting_artifacts": [{"artifact_id": f"fixture://{label}"}],
                "confidence": 0.9,
                "caveats": [],
            }
        ],
    }


def _manager_synthesis() -> dict[str, Any]:
    return {
        "confidence": 0.78,
        "caveats": ["synthesised across three independent specialists"],
        "narrative": (
            "Across supply, demand and regulatory dimensions, all three specialists "
            "converge on a self-serve growth story for Q1."
        ),
    }


def _dispatch_plan() -> dict[str, Any]:
    return {
        "dispatches": [
            {"specialist_id": "n_supply", "inputs": {"research_question": "supply chain"}},
            {"specialist_id": "n_demand", "inputs": {"research_question": "demand"}},
            {"specialist_id": "n_regul", "inputs": {"research_question": "regulatory"}},
        ]
    }


def _planner_plan() -> dict[str, Any]:
    return {
        "pattern": "manager_specialists",
        "justification": "objective decomposes into three independent investigation axes",
        "manager": {
            "template_id": "agent.business_writer",
            "template_version": "0.1.0",
            "node_id": "n_manager",
        },
        "specialists": [
            {
                "template_id": "agent.research",
                "template_version": "0.1.0",
                "node_id": "n_supply",
            },
            {
                "template_id": "agent.research",
                "template_version": "0.1.0",
                "node_id": "n_demand",
            },
            {
                "template_id": "agent.research",
                "template_version": "0.1.0",
                "node_id": "n_regul",
            },
        ],
        "parallelism_max": 3,
    }


def _llm() -> MockLLMClient:
    """Routes LLM replies by phrase in the system prompt."""
    client = MockLLMClient()
    plan = _planner_plan()
    dispatch = _dispatch_plan()
    synthesis = _manager_synthesis()
    supply = _specialist_output("supply")
    demand = _specialist_output("demand")
    regul = _specialist_output("regul")

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        system = messages[0].content if messages else ""
        user = messages[1].content if len(messages) > 1 else ""
        if "orchestration planner" in system.lower():
            text = json.dumps(plan)
        elif "Decompose the user objective" in system:
            text = json.dumps(dispatch)
        elif "Synthesize a final answer" in system:
            text = json.dumps(synthesis)
        elif "supply chain" in user:
            text = json.dumps(supply)
        elif '"research_question": "demand"' in user or "research_question: demand" in user:
            text = json.dumps(demand)
        elif "regulatory" in user:
            text = json.dumps(regul)
        else:
            raise AssertionError(f"no canned reply for system: {system[:80]!r} / user: {user[:120]!r}")
        return CompletionResult(
            text=text,
            model="claude-opus-4-7",
            prompt_tokens=20,
            completion_tokens=20,
            cost_usd=0.0005,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


async def test_manager_specialists_end_to_end(session: AsyncSession) -> None:
    """Three research specialists + a business_writer manager.

    Artifacts land in the default ``artifacts`` bucket under
    ``task/<task_id>/...`` so multiple runs co-exist.
    """
    await _publish(session, "research_agent.yaml")
    await _publish(session, "business_writer.yaml")

    artifact_store = ObjectStoreArtifactStore(client=ObjectStoreClient())
    app = create_app(_settings(), llm_client=_llm())
    app.state.artifact_store = artifact_store

    headers = {"X-API-Key": API_KEY, "X-Trace-Id": "trace-mgr-1"}
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
                "objective": "Brief leadership on Q1 dynamics across three dimensions.",
                "workflow_pattern": "manager_specialists",
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["workflow_pattern"] == "manager_specialists"
    template_ids = [t["id"] for t in body["templates"]]
    assert "agent.business_writer" in template_ids
    assert template_ids.count("agent.research") == 3
    # Terminal output is the manager's synthesis.
    assert body["output"]["narrative"].startswith("Across supply, demand and regulatory")
    assert body["verifier"]["outcome"] == "pass"
    # Each specialist's SpecialistReturn shows up in the run_specialists slice.
    returns = body["node_outputs"]["run_specialists"]["specialist_returns"]
    assert sorted(r["specialist_id"] for r in returns) == ["n_demand", "n_regul", "n_supply"]
    for r in returns:
        assert len(r["summary"]) <= 2048
        assert r["artifact_id"].startswith(f"task/{body['task_id']}/")

    # Verify the three full outputs round-trip from the artifact store.
    for r in returns:
        full = await artifact_store.get_json(r["artifact_id"])
        assert full["confidence"] == 0.85
        assert full["findings"].endswith("favourable.")
