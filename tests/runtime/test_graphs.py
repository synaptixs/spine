"""End-to-end (in-process) graph tests with a MockLLMClient."""

from __future__ import annotations

import json
from typing import Any

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime import build_single_agent_graph


def _template() -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="agent.test", version="0.1.0", description="Test agent."),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="answer", type="str"),
            ],
            model="claude-opus-4-7",
        ),
    )


def _llm_with_canned_response(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub_complete(messages: list[Message], **kwargs: object) -> CompletionResult:
        return CompletionResult(
            text=text,
            model="claude-opus-4-7",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    client.complete = stub_complete  # type: ignore[method-assign]
    return client


async def test_graph_runs_agent_then_verifier_pass() -> None:
    payload: dict[str, Any] = {"confidence": 0.9, "caveats": [], "answer": "42"}
    graph = build_single_agent_graph(template=_template(), llm=_llm_with_canned_response(json.dumps(payload)))
    final_state = await graph.ainvoke({"task_metadata": {"objective": "What is the meaning?"}})
    assert final_state["node_outputs"]["agent"] == payload
    assert final_state["node_outputs"]["verify"]["outcome"] == "pass"
    assert final_state["current_node_id"] == "verify"


async def test_graph_marks_fail_when_required_field_missing() -> None:
    # Missing 'answer' required field.
    payload = {"confidence": 0.9, "caveats": []}
    graph = build_single_agent_graph(template=_template(), llm=_llm_with_canned_response(json.dumps(payload)))
    final_state = await graph.ainvoke({"task_metadata": {"objective": "x"}})
    assert final_state["node_outputs"]["verify"]["outcome"] == "fail"
    failures = final_state["node_outputs"]["verify"]["failures"]
    assert any(f["field"] == "answer" for f in failures)
