"""Unit tests for Planner v0 — stub the SQLAlchemy session so no DB is required."""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.ir.graph import NodeType, WorkflowPattern
from orchestrator.planner import PlannerError, PlannerV0


class _Row:
    """Minimal stand-in for AgentTemplateRow that satisfies the planner's reads."""

    def __init__(
        self,
        id: str,
        version: str,
        description: str,
        tags: list[str],
        spec_json: dict[str, Any],
    ) -> None:
        self.id = id
        self.version = version
        self.description = description
        self.tags = tags
        self.spec_json = spec_json


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[Any]:
        return list(self._items)


class _StubSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, stmt: object) -> _ScalarResult:
        _ = stmt
        return _ScalarResult(self._rows)


def _row(id: str = "agent.research", version: str = "0.1.0") -> _Row:
    return _Row(
        id=id,
        version=version,
        description="d",
        tags=["research"],
        spec_json={"known_limitations": []},
    )


async def test_no_candidates_raises() -> None:
    planner = PlannerV0(llm=MockLLMClient())
    with pytest.raises(PlannerError, match="no published"):
        await planner.plan("anything", session=_StubSession([]))  # type: ignore[arg-type]


async def test_single_candidate_emits_single_agent_ir_without_llm() -> None:
    planner = PlannerV0(llm=MockLLMClient())
    ir = await planner.plan(
        "Summarize the Big Bang",
        session=_StubSession([_row()]),  # type: ignore[arg-type]
    )
    assert ir.spec.workflow_pattern is WorkflowPattern.SINGLE_AGENT
    assert len(ir.spec.nodes) == 1
    node = ir.spec.nodes[0]
    assert node.type is NodeType.AGENT
    assert node.template_id == "agent.research"
    assert node.template_version == "0.1.0"


async def test_multiple_candidates_calls_llm_and_uses_choice() -> None:
    chosen = {
        "template_id": "agent.research",
        "template_version": "0.2.0",
        "justification": "matches research tag",
    }
    llm = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        return CompletionResult(
            text='{"template_id": "agent.research", "template_version": "0.2.0", '
            '"justification": "matches research tag"}',
            model="claude-opus-4-7",
            prompt_tokens=5,
            completion_tokens=5,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    llm.complete = stub  # type: ignore[method-assign]

    rows = [_row(version="0.1.0"), _row(version="0.2.0")]
    planner = PlannerV0(llm=llm)
    ir = await planner.plan(
        "Find recent papers",
        session=_StubSession(rows),  # type: ignore[arg-type]
    )
    assert ir.spec.nodes[0].template_version == chosen["template_version"]
    assert ir.spec.nodes[0].config["justification"] == chosen["justification"]


async def test_llm_choice_outside_candidates_raises() -> None:
    llm = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        return CompletionResult(
            text='{"template_id": "nonexistent", "template_version": "1.0.0"}',
            model="claude-opus-4-7",
            prompt_tokens=1,
            completion_tokens=1,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    llm.complete = stub  # type: ignore[method-assign]

    planner = PlannerV0(llm=llm)
    with pytest.raises(PlannerError, match="unknown template"):
        await planner.plan(
            "x",
            session=_StubSession([_row(version="0.1.0"), _row(version="0.2.0")]),  # type: ignore[arg-type]
        )


async def test_glossary_passthrough() -> None:
    planner = PlannerV0(llm=MockLLMClient())
    ir = await planner.plan(
        "Define churn",
        session=_StubSession([_row()]),  # type: ignore[arg-type]
        glossary={"churn": "logo churn"},
    )
    assert ir.spec.task_glossary["churn"].value == "logo churn"
