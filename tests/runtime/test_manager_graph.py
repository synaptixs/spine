"""In-process tests for build_manager_specialists_graph."""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime import (
    InMemoryArtifactStore,
    ManagerSpec,
    SpecialistSpec,
    build_manager_specialists_graph,
)


def _specialist_template(template_id: str, model: str = "claude-haiku-4-5-20251001") -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id=template_id, version="0.1.0", description=f"{template_id} description"),
        spec=AgentSpec(
            inputs=[FieldSchema(name="topic", type="str")],
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="findings", type="str"),
                FieldSchema(name="claims", type="list", required=False),
            ],
            model=model,
        ),
    )


def _manager_template() -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="agent.manager", version="0.1.0", description="manager"),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="synthesis", type="str"),
            ],
            model="claude-opus-4-7",
        ),
    )


def _llm_router(replies: dict[str, str]) -> MockLLMClient:
    """Route mock LLM replies by detecting an agent id or prompt phrase in the system message."""
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        system = messages[0].content if messages else ""
        for marker, body in replies.items():
            if marker in system:
                return CompletionResult(
                    text=body,
                    model="claude-opus-4-7",
                    prompt_tokens=5,
                    completion_tokens=5,
                    cost_usd=0.0,
                    latency_ms=0.0,
                )
        raise AssertionError(f"no canned reply for system prompt: {system[:120]!r}")

    client.complete = stub  # type: ignore[method-assign]
    return client


@pytest.fixture
def manager_setup() -> dict[str, Any]:
    specialists = [
        SpecialistSpec(node_id="n_alpha", template=_specialist_template("agent.alpha")),
        SpecialistSpec(node_id="n_beta", template=_specialist_template("agent.beta")),
        SpecialistSpec(node_id="n_gamma", template=_specialist_template("agent.gamma")),
    ]
    manager = ManagerSpec(node_id="n_manager", template=_manager_template())
    return {"specialists": specialists, "manager": manager}


def _dispatch_plan() -> dict[str, Any]:
    return {
        "dispatches": [
            {"specialist_id": "n_alpha", "inputs": {"topic": "supply chain"}},
            {"specialist_id": "n_beta", "inputs": {"topic": "demand signals"}},
            {"specialist_id": "n_gamma", "inputs": {"topic": "regulatory"}},
        ]
    }


def _specialist_output(specialist: str) -> dict[str, Any]:
    return {
        "confidence": 0.85,
        "caveats": [f"{specialist}: single-pass research"],
        "findings": f"{specialist} found three relevant signals.",
        "claims": [
            {
                "id": f"c_{specialist}_1",
                "statement": f"{specialist} primary claim.",
                "confidence": 0.9,
            }
        ],
    }


def _manager_synthesis() -> dict[str, Any]:
    return {
        "confidence": 0.78,
        "caveats": ["synthesised across three independent specialists"],
        "synthesis": "Three independent specialists converge on a self-serve growth story.",
    }


async def test_manager_dispatches_all_specialists_in_parallel(manager_setup: dict[str, Any]) -> None:
    llm = _llm_router(
        {
            "manager agent agent.manager": json.dumps(_dispatch_plan()),
            "agent.alpha description": json.dumps(_specialist_output("alpha")),
            "agent.beta description": json.dumps(_specialist_output("beta")),
            "agent.gamma description": json.dumps(_specialist_output("gamma")),
            "Synthesize a final answer": json.dumps(_manager_synthesis()),
        }
    )
    store = InMemoryArtifactStore()
    graph = build_manager_specialists_graph(
        manager=manager_setup["manager"],
        specialists=manager_setup["specialists"],
        llm=llm,
        artifact_store=store,
    )

    final = await graph.ainvoke({"task_metadata": {"task_id": "t-mgr-1", "objective": "Why is Q1 down?"}})

    # All three specialists landed in node_outputs and got artifact IDs.
    returns = final["node_outputs"]["run_specialists"]["specialist_returns"]
    ids_seen = sorted(r["specialist_id"] for r in returns)
    assert ids_seen == ["n_alpha", "n_beta", "n_gamma"]
    for r in returns:
        assert r["artifact_id"].startswith("task/t-mgr-1/")
        assert r["confidence"] == 0.85
    # All three full outputs persisted in the artifact store.
    assert sorted(store.keys()) == [
        "task/t-mgr-1/n_alpha/output.json",
        "task/t-mgr-1/n_beta/output.json",
        "task/t-mgr-1/n_gamma/output.json",
    ]
    # Manager synthesis ran and passed schema verification.
    assert final["node_outputs"]["n_manager"]["synthesis"].startswith("Three independent")
    assert final["node_outputs"]["verify_n_manager"]["outcome"] == "pass"


async def test_parallelism_max_serialises_dispatch(manager_setup: dict[str, Any]) -> None:
    """With parallelism_max=1, dispatches run one at a time."""
    import asyncio

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    real_router = _llm_router(
        {
            "manager agent agent.manager": json.dumps(_dispatch_plan()),
            "agent.alpha description": json.dumps(_specialist_output("alpha")),
            "agent.beta description": json.dumps(_specialist_output("beta")),
            "agent.gamma description": json.dumps(_specialist_output("gamma")),
            "Synthesize a final answer": json.dumps(_manager_synthesis()),
        }
    )

    real_complete = real_router.complete

    async def metered_complete(messages: list[Message], **kwargs: Any) -> CompletionResult:
        nonlocal in_flight, max_in_flight
        system = messages[0].content if messages else ""
        is_specialist = any(m in system for m in ("agent.alpha", "agent.beta", "agent.gamma"))
        if is_specialist:
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(0.01)
                return await real_complete(messages, **kwargs)
            finally:
                async with lock:
                    in_flight -= 1
        return await real_complete(messages, **kwargs)

    real_router.complete = metered_complete  # type: ignore[method-assign]

    serial_manager = ManagerSpec(
        node_id="n_manager",
        template=_manager_template(),
        parallelism_max=1,
    )
    graph = build_manager_specialists_graph(
        manager=serial_manager,
        specialists=manager_setup["specialists"],
        llm=real_router,
        artifact_store=InMemoryArtifactStore(),
    )
    await graph.ainvoke({"task_metadata": {"task_id": "t-serial", "objective": "x"}})
    assert max_in_flight == 1


async def test_unknown_specialist_in_dispatch_raises(manager_setup: dict[str, Any]) -> None:
    plan = {"dispatches": [{"specialist_id": "n_unknown", "inputs": {}}]}
    llm = _llm_router({"manager agent agent.manager": json.dumps(plan)})
    graph = build_manager_specialists_graph(
        manager=manager_setup["manager"],
        specialists=manager_setup["specialists"],
        llm=llm,
        artifact_store=InMemoryArtifactStore(),
    )
    with pytest.raises(Exception, match="unknown specialist_id"):
        await graph.ainvoke({"task_metadata": {"task_id": "t-x", "objective": "x"}})


async def test_empty_specialist_list_rejected_at_build_time() -> None:
    with pytest.raises(ValueError, match="at least one specialist"):
        build_manager_specialists_graph(
            manager=ManagerSpec(node_id="n_m", template=_manager_template()),
            specialists=[],
            llm=MockLLMClient(),
        )


async def test_duplicate_specialist_ids_rejected_at_build_time() -> None:
    spec = SpecialistSpec(node_id="dup", template=_specialist_template("agent.dup"))
    with pytest.raises(ValueError, match="duplicate specialist node_ids"):
        build_manager_specialists_graph(
            manager=ManagerSpec(node_id="n_m", template=_manager_template()),
            specialists=[spec, spec],
            llm=MockLLMClient(),
        )
