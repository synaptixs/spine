"""End-to-end in-process test of build_sequential_graph with MockLLMClient."""

from __future__ import annotations

import json
from typing import Any

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime import build_sequential_graph
from orchestrator.runtime.graphs import SequentialStep


def _analyst_template() -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="agent.analyst", version="0.1.0", description="x"),
        spec=AgentSpec(
            inputs=[FieldSchema(name="topic", type="str")],
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="findings", type="str"),
            ],
            model="claude-haiku-4-5-20251001",
        ),
    )


def _writer_template() -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="agent.writer", version="0.1.0", description="x"),
        spec=AgentSpec(
            inputs=[
                FieldSchema(name="findings", type="str"),
                FieldSchema(name="audience", type="str"),
            ],
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="narrative", type="str"),
            ],
            model="claude-haiku-4-5-20251001",
        ),
    )


def _llm_with_router(responses: dict[str, str]) -> MockLLMClient:
    """Return a MockLLMClient that picks a canned response by detecting the agent id
    embedded in the system prompt. Simplifies multi-node mocking."""
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        system = messages[0].content if messages else ""
        for key, body in responses.items():
            if key in system:
                return CompletionResult(
                    text=body,
                    model="claude-haiku-4-5-20251001",
                    prompt_tokens=10,
                    completion_tokens=10,
                    cost_usd=0.0,
                    latency_ms=0.0,
                )
        raise AssertionError(f"no canned response for system prompt: {system[:120]!r}")

    client.complete = stub  # type: ignore[method-assign]
    return client


async def test_two_stage_chain_flows_outputs_into_next_inputs() -> None:
    analyst_out = {
        "confidence": 0.9,
        "caveats": [],
        "findings": "Q1 revenue rose 12% QoQ driven by self-serve.",
    }
    writer_out = {
        "confidence": 0.85,
        "caveats": [],
        "narrative": "Self-serve drove a 12% QoQ revenue gain in Q1.",
    }
    llm = _llm_with_router({"agent.analyst": json.dumps(analyst_out), "agent.writer": json.dumps(writer_out)})

    graph = build_sequential_graph(
        steps=[
            SequentialStep(node_id="n_analyst", template=_analyst_template()),
            SequentialStep(
                node_id="n_writer",
                template=_writer_template(),
                inputs_from={
                    "findings": "node_outputs.n_analyst.findings",
                    "audience": "task_metadata.audience",
                },
            ),
        ],
        llm=llm,
    )

    initial: dict[str, Any] = {
        "task_metadata": {
            "objective": "Analyze Q1 revenue and brief the exec team.",
            "topic": "Q1 revenue",
            "audience": "exec leadership",
        }
    }
    final = await graph.ainvoke(initial)

    assert final["node_outputs"]["n_analyst"] == analyst_out
    assert final["node_outputs"]["n_writer"] == writer_out
    # Both verifier nodes ran and both passed.
    assert final["node_outputs"]["verify_n_analyst"]["outcome"] == "pass"
    assert final["node_outputs"]["verify_n_writer"]["outcome"] == "pass"
    # Confidence history captured both agents in order.
    history_nodes = [h["node"] for h in final["confidence_history"]]
    assert history_nodes == ["n_analyst", "n_writer"]


async def test_duplicate_node_ids_raise() -> None:
    import pytest

    with pytest.raises(ValueError, match="duplicate"):
        build_sequential_graph(
            steps=[
                SequentialStep(node_id="dup", template=_analyst_template()),
                SequentialStep(node_id="dup", template=_writer_template()),
            ],
            llm=MockLLMClient(),
        )


async def test_empty_step_list_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least one"):
        build_sequential_graph(steps=[], llm=MockLLMClient())
