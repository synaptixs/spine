from __future__ import annotations

import json

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime import MemorySaver, build_single_agent_graph
from orchestrator.runtime.checkpointer import normalise_pg_url


def _template() -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="agent.x", version="0.1.0", description="x"),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="answer", type="str"),
            ],
            model="claude-opus-4-7",
        ),
    )


def _llm_returning(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        return CompletionResult(
            text=text,
            model="claude-opus-4-7",
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


def test_normalise_pg_url_strips_sqlalchemy_dialect() -> None:
    assert normalise_pg_url("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert normalise_pg_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


async def test_memory_saver_captures_state_for_resume() -> None:
    payload = {"confidence": 0.9, "caveats": [], "answer": "42"}
    saver = MemorySaver()
    graph = build_single_agent_graph(
        template=_template(), llm=_llm_returning(json.dumps(payload)), checkpointer=saver
    )
    config = {"configurable": {"thread_id": "thr-1"}}
    final = await graph.ainvoke({"task_metadata": {"objective": "What?"}}, config=config)
    assert final["node_outputs"]["agent"] == payload

    # The MemorySaver should now hold the terminal checkpoint for this thread.
    state = await graph.aget_state(config)
    assert state is not None
    assert state.values["node_outputs"]["verify"]["outcome"] == "pass"
