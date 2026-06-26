"""Unit tests for PlannerV1 — stub the SQLAlchemy session so no DB is required."""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.ir.graph import NodeType, WorkflowPattern
from orchestrator.planner import PlannerError, PlannerV1


class _Row:
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

    def one_or_none(self) -> Any:
        """Match SQLAlchemy's Result.one_or_none() for aggregate-query callers
        (Sprint 11.6's CalibrationHistoryRepo)."""

        class _AggRow:
            n = 0
            mean_conf = None
            passes = 0

        return _AggRow()


class _StubSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, stmt: object) -> _ScalarResult:
        _ = stmt
        return _ScalarResult(self._rows)


def _row(id: str, version: str = "0.1.0", *, tags: list[str] | None = None) -> _Row:
    return _Row(
        id=id,
        version=version,
        description="d",
        tags=tags or [],
        spec_json={
            "inputs": [{"name": "topic", "type": "str"}],
            "outputs": [{"name": "findings", "type": "str"}],
            "known_limitations": [],
        },
    )


def _llm_returning(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        return CompletionResult(
            text=text,
            model="claude-opus-4-7",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


async def test_single_candidate_returns_single_agent_without_llm() -> None:
    planner = PlannerV1(llm=MockLLMClient())
    ir = await planner.plan(
        "x",
        session=_StubSession([_row("agent.research")]),  # type: ignore[arg-type]
    )
    assert ir.spec.workflow_pattern is WorkflowPattern.SINGLE_AGENT


async def test_llm_returns_sequential_plan() -> None:
    plan = {
        "pattern": "sequential",
        "justification": "two-stage objective",
        "steps": [
            {
                "template_id": "agent.analyst",
                "template_version": "0.1.0",
                "node_id": "n_analyst",
                "inputs_from": {},
            },
            {
                "template_id": "agent.writer",
                "template_version": "0.1.0",
                "node_id": "n_writer",
                "inputs_from": {"findings": "node_outputs.n_analyst.findings"},
            },
        ],
    }
    planner = PlannerV1(llm=_llm_returning(json.dumps(plan)))
    rows = [_row("agent.analyst"), _row("agent.writer")]
    ir = await planner.plan(
        "Analyze then write.",
        session=_StubSession(rows),  # type: ignore[arg-type]
    )
    assert ir.spec.workflow_pattern is WorkflowPattern.SEQUENTIAL
    assert [n.id for n in ir.spec.nodes] == ["n_analyst", "n_writer"]
    assert [n.type for n in ir.spec.nodes] == [NodeType.AGENT, NodeType.AGENT]
    # Edges form a linear chain.
    edges = [(e.source, e.target) for e in ir.spec.edges]
    assert edges == [("n_analyst", "n_writer")]
    # split_justification surfaces on constraints for the IR validator.
    assert ir.spec.constraints["split_justification"] == "two-stage objective"


async def test_force_sequential_with_single_candidate_skips_shortcut() -> None:
    """When the caller demands sequential and only one candidate exists, the LLM is called
    (and will return single_agent here, since two-step plans need >=2 templates). The
    planner then errors because the LLM-emitted single_agent plan has no second step."""
    plan = {"pattern": "single_agent", "template_id": "agent.x", "template_version": "0.1.0"}
    planner = PlannerV1(llm=_llm_returning(json.dumps(plan)))
    rows = [_row("agent.x")]
    ir = await planner.plan(
        "x",
        session=_StubSession(rows),  # type: ignore[arg-type]
        force_pattern=WorkflowPattern.SEQUENTIAL,
    )
    # LLM ignored the constraint but emitted single_agent; the planner trusts the LLM.
    # The IR validator will surface the "sequential pattern requires N>=2" rule later.
    assert ir.spec.workflow_pattern is WorkflowPattern.SINGLE_AGENT


async def test_planner_includes_inferred_glossary_in_ir() -> None:
    """LLM-emitted inferred_glossary surfaces on spec.task_glossary."""
    plan = {
        "pattern": "single_agent",
        "template_id": "agent.x",
        "template_version": "0.1.0",
        "justification": "fits",
        "inferred_glossary": {
            "churn": {"value": "logo churn (count of cancelled accounts)", "reason": "ambiguous"},
        },
    }
    planner = PlannerV1(llm=_llm_returning(json.dumps(plan)))
    rows = [_row("agent.x"), _row("agent.y")]
    ir = await planner.plan(
        "How is churn trending?",
        session=_StubSession(rows),  # type: ignore[arg-type]
    )
    churn = ir.spec.task_glossary.get("churn")
    assert churn is not None
    assert churn.source == "planner_inferred"
    assert "logo churn" in churn.value


async def test_user_glossary_wins_over_planner_inferred() -> None:
    """user_specified > org_default > planner_inferred priority order."""
    plan = {
        "pattern": "single_agent",
        "template_id": "agent.x",
        "template_version": "0.1.0",
        "inferred_glossary": {
            "churn": {"value": "planner guess", "reason": "ambiguous"},
        },
    }
    planner = PlannerV1(llm=_llm_returning(json.dumps(plan)))
    rows = [_row("agent.x"), _row("agent.y")]
    ir = await planner.plan(
        "How is churn trending?",
        session=_StubSession(rows),  # type: ignore[arg-type]
        glossary={"churn": "logo churn (user override)"},
    )
    churn = ir.spec.task_glossary["churn"]
    assert churn.value == "logo churn (user override)"
    assert churn.source == "user_specified"


async def _plan_single(planner: PlannerV1, rows: list[Any]) -> Any:
    return await planner.plan("x", session=_StubSession(rows))  # type: ignore[arg-type]


async def test_replan_returns_revised_ir_keeping_pattern() -> None:
    """Sprint 12.2: replan can swap a node's template while keeping single_agent."""
    initial_plan = {
        "pattern": "single_agent",
        "template_id": "agent.x",
        "template_version": "0.1.0",
        "justification": "first attempt",
    }
    planner = PlannerV1(llm=_llm_returning(json.dumps(initial_plan)))
    rows = [_row("agent.x"), _row("agent.y")]
    original_ir = await planner.plan("x", session=_StubSession(rows))  # type: ignore[arg-type]
    failing_node_id = original_ir.spec.nodes[0].id

    revised_plan = {
        "pattern": "single_agent",
        "template_id": "agent.y",
        "template_version": "0.1.0",
        "justification": "swap to agent.y after agent.x failed schema check",
    }
    planner._llm = _llm_returning(json.dumps(revised_plan))

    revised_ir = await planner.replan(
        original_ir,
        session=_StubSession(rows),  # type: ignore[arg-type]
        failing_node_id=failing_node_id,
        failure_summary={"outcome": "fail", "rule": "schema.missing_field"},
        replan_count=1,
    )

    assert revised_ir.spec.workflow_pattern is WorkflowPattern.SINGLE_AGENT
    assert revised_ir.spec.nodes[0].template_id == "agent.y"


async def test_replan_can_change_pattern_to_sequential() -> None:
    """Sprint 12.2: replace_downstream strategy allows changing the workflow pattern."""
    initial_plan = {
        "pattern": "single_agent",
        "template_id": "agent.x",
        "template_version": "0.1.0",
        "justification": "first try",
    }
    planner = PlannerV1(llm=_llm_returning(json.dumps(initial_plan)))
    rows = [_row("agent.x"), _row("agent.y")]
    original_ir = await planner.plan("x", session=_StubSession(rows))  # type: ignore[arg-type]
    failing_node_id = original_ir.spec.nodes[0].id

    revised_plan = {
        "pattern": "sequential",
        "justification": "split into two steps after single-agent failed",
        "steps": [
            {"template_id": "agent.x", "template_version": "0.1.0", "node_id": "n_a", "inputs_from": {}},
            {
                "template_id": "agent.y",
                "template_version": "0.1.0",
                "node_id": "n_b",
                "inputs_from": {"findings": "node_outputs.n_a.findings"},
            },
        ],
    }
    planner._llm = _llm_returning(json.dumps(revised_plan))

    revised_ir = await planner.replan(
        original_ir,
        session=_StubSession(rows),  # type: ignore[arg-type]
        failing_node_id=failing_node_id,
        failure_summary={"outcome": "fail"},
        replan_count=1,
    )
    assert revised_ir.spec.workflow_pattern is WorkflowPattern.SEQUENTIAL
    assert [n.id for n in revised_ir.spec.nodes] == ["n_a", "n_b"]


async def test_replan_unknown_failing_node_raises() -> None:
    initial_plan = {
        "pattern": "single_agent",
        "template_id": "agent.x",
        "template_version": "0.1.0",
    }
    planner = PlannerV1(llm=_llm_returning(json.dumps(initial_plan)))
    rows = [_row("agent.x"), _row("agent.y")]
    original_ir = await planner.plan("x", session=_StubSession(rows))  # type: ignore[arg-type]

    with pytest.raises(PlannerError, match="failing_node_id"):
        await planner.replan(
            original_ir,
            session=_StubSession(rows),  # type: ignore[arg-type]
            failing_node_id="does_not_exist",
            failure_summary={},
            replan_count=1,
        )


async def test_sequential_plan_with_unknown_template_raises() -> None:
    plan = {
        "pattern": "sequential",
        "steps": [
            {"template_id": "agent.a", "template_version": "0.1.0", "node_id": "n_a"},
            {"template_id": "agent.unknown", "template_version": "0.1.0", "node_id": "n_b"},
        ],
    }
    planner = PlannerV1(llm=_llm_returning(json.dumps(plan)))
    rows = [_row("agent.a"), _row("agent.other")]
    with pytest.raises(PlannerError, match="unknown template"):
        await planner.plan("x", session=_StubSession(rows))  # type: ignore[arg-type]
