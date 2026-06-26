"""Sprint 12.7: end-to-end replan loop integration test.

Two scenarios:

  1. ``test_replan_succeeds_on_retry`` — first agent pass fails the
     ConfidenceVerifier (low confidence), the orchestration layer calls
     ``planner.replan()`` to swap to a second template, the second pass
     passes. Final response carries ``replan_count == 1`` and the trace
     audit contains one ``task_replan`` row.

  2. ``test_replan_budget_exhausted_terminates`` — every agent pass
     stays below the confidence floor, the planner keeps replanning,
     and the loop ends cleanly when ``max_replan_count`` is reached.
     The final response carries ``replan_count == 2`` (the default
     budget) and the history records ``budget_exhausted``.

Both tests drive the planner end-to-end (initial plan + replan) and
configure ``on_failure='replan'`` on the request so the verifier chain
dispatches into the replan path on every failure.
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

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
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


async def _publish_two_research_templates(session: AsyncSession) -> None:
    base = load_agent_template(EXAMPLES_ROOT / "templates" / "research_agent.yaml")
    repo = VersionedRepo(session, AgentTemplateRow)

    # Original template.
    await repo.create(
        id=base.metadata.id,
        version=base.metadata.version,
        description=base.metadata.description,
        tags=list(base.metadata.tags),
        spec=base.spec.model_dump(mode="json"),
    )
    await repo.publish(base.metadata.id, base.metadata.version)

    # Alternate template the planner can pick during replan. Same schema,
    # different id so the replan choice is unambiguous.
    await repo.create(
        id="agent.research_v2",
        version="0.1.0",
        description="Alternate research agent used for replan tests.",
        tags=["research", "replan-target"],
        spec=base.spec.model_dump(mode="json"),
    )
    await repo.publish("agent.research_v2", "0.1.0")
    await session.commit()


def _bad_output() -> dict[str, Any]:
    """Output that fails ConfidenceVerifier (default threshold 0.7).

    Confidence 0.4 sits well below the 0.63 fail band, so the chain's
    on-failure policy fires deterministically.
    """
    return {
        "confidence": 0.4,
        "caveats": ["low-evidence single-source pass"],
        "findings": "Inconclusive.",
        "claims": [
            {
                "id": "c1",
                "statement": "Antibiotics are ineffective against viruses.",
                "claim_type": "qualitative",
                "supporting_artifacts": [{"artifact_id": "fixture://cdc-guideline"}],
                "confidence": 0.4,
                "caveats": [],
            }
        ],
    }


def _good_output() -> dict[str, Any]:
    return {
        "confidence": 0.85,
        "caveats": [],
        "findings": "Antibiotics treat bacterial infections, not viral ones.",
        "claims": [
            {
                "id": "c1",
                "statement": "Antibiotics are ineffective against viruses.",
                "claim_type": "qualitative",
                "supporting_artifacts": [{"artifact_id": "fixture://cdc-guideline"}],
                "confidence": 0.9,
                "caveats": [],
            }
        ],
    }


def _replan_plan_picking(template_id: str) -> dict[str, Any]:
    return {
        "pattern": "single_agent",
        "template_id": template_id,
        "template_version": "0.1.0",
        "justification": f"replan: try {template_id} after the prior pass came in below the confidence floor",
    }


def _sequenced_llm(responses: list[str]) -> MockLLMClient:
    """Return a MockLLMClient whose ``complete`` walks through ``responses``
    in order. Trailing responses repeat the final entry, so callers don't
    have to size the list to the exact call count.
    """
    client = MockLLMClient()
    state = {"i": 0}

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        _ = messages, kwargs
        idx = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return CompletionResult(
            text=responses[idx],
            model="claude-opus-4-7",
            prompt_tokens=50,
            completion_tokens=50,
            cost_usd=0.001,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


async def test_replan_succeeds_on_retry(session: AsyncSession) -> None:
    """First pass fails confidence floor, planner replans to a second template,
    second pass passes — replan_count == 1, audit has one task_replan row."""
    await _publish_two_research_templates(session)

    # LLM call order with on_failure=replan + LLM-planned IR:
    #   1. planner.plan() → choose agent.research
    #   2. agent.research's SingleAgentNode → bad output
    #   3. planner.replan() → choose agent.research_v2
    #   4. agent.research_v2's SingleAgentNode → good output
    responses = [
        json.dumps(_replan_plan_picking("agent.research")),
        json.dumps(_bad_output()),
        json.dumps(_replan_plan_picking("agent.research_v2")),
        json.dumps(_good_output()),
    ]
    app = create_app(_settings(), llm_client=_sequenced_llm(responses))

    from orchestrator.runtime import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    await store.put_json("fixture://cdc-guideline", {"findings": "antibiotics target bacteria"})
    app.state.artifact_store = store

    headers = {"X-API-Key": API_KEY, "X-Trace-Id": "trace-replan-success"}
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
                "on_failure": "replan",
                "glossary": {"research_question": "Do antibiotics work against viral infections?"},
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["replan_count"] == 1
    assert len(body["replan_history"]) == 1
    history = body["replan_history"][0]
    assert history["outcome"] == "replanned"
    assert history["previous_pattern"] == "single_agent"
    assert history["new_pattern"] == "single_agent"
    assert history["new_templates"] == [{"id": "agent.research_v2", "version": "0.1.0"}]

    # The terminal output reflects the second (successful) pass.
    assert body["verifier"]["outcome"] == "pass"
    assert body["output"]["confidence"] == 0.85
    # Final templates list points at the replan target.
    assert body["templates"] == [{"id": "agent.research_v2", "version": "0.1.0"}]

    rows = (await session.execute(select(AuditLogRow))).scalars().all()
    replan_rows = [r for r in rows if r.action == "task_replan"]
    assert len(replan_rows) == 1
    assert (replan_rows[0].after_json or {}).get("outcome") == "replanned"

    submits = [r for r in rows if r.action == "task_submit"]
    assert len(submits) == 1
    assert (submits[0].after_json or {}).get("replan_count") == 1


async def test_replan_budget_exhausted_terminates(session: AsyncSession) -> None:
    """Every pass fails the confidence floor; loop exits cleanly at budget."""
    await _publish_two_research_templates(session)

    # Default budget is 2 retries. Sequence:
    #   1. planner.plan → pick agent.research
    #   2. agent → bad   (attempt 1)
    #   3. planner.replan → pick v2
    #   4. agent → bad   (attempt 2, replan_count == 1)
    #   5. planner.replan → pick v2 again
    #   6. agent → bad   (attempt 3, replan_count == 2; can't replan further)
    responses = [
        json.dumps(_replan_plan_picking("agent.research")),
        json.dumps(_bad_output()),
        json.dumps(_replan_plan_picking("agent.research_v2")),
        json.dumps(_bad_output()),
        json.dumps(_replan_plan_picking("agent.research_v2")),
        json.dumps(_bad_output()),
    ]
    app = create_app(_settings(), llm_client=_sequenced_llm(responses))

    from orchestrator.runtime import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    await store.put_json("fixture://cdc-guideline", {"findings": "antibiotics target bacteria"})
    app.state.artifact_store = store

    headers = {"X-API-Key": API_KEY, "X-Trace-Id": "trace-replan-exhausted"}
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
                "on_failure": "replan",
                "glossary": {"research_question": "Do antibiotics work against viral infections?"},
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()

    # Default budget = 2 retries → replan_count caps at 2, last entry is budget_exhausted.
    assert body["replan_count"] == 2
    assert len(body["replan_history"]) == 3
    assert body["replan_history"][-1]["outcome"] == "budget_exhausted"
    # The earlier two attempts succeeded in producing a revised IR.
    assert body["replan_history"][0]["outcome"] == "replanned"
    assert body["replan_history"][1]["outcome"] == "replanned"

    # The chain (which includes ConfidenceVerifier) records the final failure.
    # The "verifier" field in the response is schema-only and will still be
    # "pass" because the agent's output JSON shape is valid — what flunked is
    # the chain's semantic checks.
    chain_slot = body["node_outputs"].get("chain_agent") or {}
    assert chain_slot.get("dispatch", {}).get("next_step") == "replan"

    rows = (await session.execute(select(AuditLogRow))).scalars().all()
    replan_rows = [r for r in rows if r.action == "task_replan"]
    assert len(replan_rows) == 3
    outcomes = [(r.after_json or {}).get("outcome") for r in replan_rows]
    assert outcomes == ["replanned", "replanned", "budget_exhausted"]
