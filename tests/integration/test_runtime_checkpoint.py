"""Integration test: round-trip a graph through the Postgres checkpointer.

Run with ``pytest -m integration`` after ``docker compose up``.
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime import build_single_agent_graph, open_postgres_checkpointer

pytestmark = pytest.mark.integration


def _template() -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="agent.ck", version="0.1.0", description="x"),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="answer", type="str"),
            ],
            model="claude-opus-4-7",
        ),
    )


def _llm() -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        return CompletionResult(
            text=json.dumps({"confidence": 0.7, "caveats": [], "answer": "ok"}),
            model="claude-opus-4-7",
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


async def test_checkpointer_persists_terminal_state(session: AsyncSession) -> None:
    _ = session  # fixture ensures DB is migrated and clean
    url = os.getenv(
        "ORCHESTRATOR_TEST_DATABASE_URL",
        "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
    )
    config = {"configurable": {"thread_id": "ck-thr-1"}}

    async with open_postgres_checkpointer(url) as saver:
        graph = build_single_agent_graph(template=_template(), llm=_llm(), checkpointer=saver)
        await graph.ainvoke({"task_metadata": {"objective": "round trip"}}, config=config)

    # Reopen the checkpointer; final state must still be retrievable.
    async with open_postgres_checkpointer(url) as saver:
        graph = build_single_agent_graph(template=_template(), llm=_llm(), checkpointer=saver)
        state = await graph.aget_state(config)
        assert state is not None
        assert state.values["node_outputs"]["verify"]["outcome"] == "pass"
        assert state.values["node_outputs"]["agent"]["answer"] == "ok"
