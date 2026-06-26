"""Sprint 12.1: VerifierChainNode emits a replan_request when the dispatcher decides REPLAN.

The runtime can't do the replan from inside the graph (the planner needs a DB
session and a chance to rebuild the graph), so the chain node surfaces the
request on state and the orchestration layer (POST /v1/tasks) reads it off
the final state after the graph finishes its current pass.
"""

from __future__ import annotations

from typing import Any

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.chain_node import VerifierChainNode
from orchestrator.runtime.failure_dispatch import FailurePolicy
from orchestrator.runtime.post_conditions import FailureAction
from orchestrator.runtime.verifiers import (
    VerifierChain,
    VerifierContext,
    VerifierFailure,
    VerifierOutcome,
    VerifierResult,
)


class _FailingVerifier:
    """Always returns FAIL — used to drive the dispatcher into a non-pass branch."""

    verifier_id = "always_fail"

    async def verify(self, output: dict[str, Any], ctx: VerifierContext) -> VerifierResult:
        _ = output, ctx
        return VerifierResult(
            verifier_id=self.verifier_id,
            outcome=VerifierOutcome.FAIL,
            failures=(
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="forced",
                    field="root",
                    message="injected failure",
                ),
            ),
            elapsed_ms=1.0,
        )


def _template() -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="agent.x", version="0.1.0", description="x"),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="findings", type="str"),
            ],
            model="claude-haiku-4-5-20251001",
        ),
    )


def _state() -> dict[str, Any]:
    return {
        "task_metadata": {"task_id": "t-replan", "trace_id": "tr-replan"},
        "node_outputs": {"agent": {"confidence": 0.4, "caveats": [], "findings": "weak"}},
    }


async def test_chain_node_emits_replan_request_when_policy_is_replan() -> None:
    node = VerifierChainNode(
        template=_template(),
        chain=VerifierChain([_FailingVerifier()], chain_id="chain_x"),
        target_node="agent",
        verifier_id="chain_agent",
        policy=FailurePolicy(on_fail=FailureAction.REPLAN),
    )
    update = await node(_state())

    assert update["current_node_id"] == "__replan__"
    chain_slot = update["node_outputs"]["chain_agent"]
    assert chain_slot["dispatch"]["next_step"] == "replan"
    assert "replan_request" in chain_slot
    req = chain_slot["replan_request"]
    assert req["failing_node"] == "agent"
    assert req["verifier_id"] == "chain_agent"
    assert req["outcome"] == "fail"
    assert any("forced" in f["rule"] for f in req["failures"])


async def test_chain_node_does_not_emit_replan_when_policy_is_terminate() -> None:
    node = VerifierChainNode(
        template=_template(),
        chain=VerifierChain([_FailingVerifier()], chain_id="chain_x"),
        target_node="agent",
        verifier_id="chain_agent",
        policy=FailurePolicy(on_fail=FailureAction.TERMINATE),
    )
    update = await node(_state())

    assert update["current_node_id"] == "__terminate__"
    chain_slot = update["node_outputs"]["chain_agent"]
    assert chain_slot["dispatch"]["next_step"] == "terminate"
    assert "replan_request" not in chain_slot


async def test_chain_node_does_not_emit_replan_on_pass() -> None:
    class _PassingVerifier:
        verifier_id = "always_pass"

        async def verify(self, output: dict[str, Any], ctx: VerifierContext) -> VerifierResult:
            _ = output, ctx
            return VerifierResult(verifier_id=self.verifier_id, outcome=VerifierOutcome.PASS)

    node = VerifierChainNode(
        template=_template(),
        chain=VerifierChain([_PassingVerifier()], chain_id="chain_x"),
        target_node="agent",
        verifier_id="chain_agent",
        policy=FailurePolicy(on_fail=FailureAction.REPLAN),
    )
    update = await node(_state())

    assert update["current_node_id"] == "chain_agent"
    chain_slot = update["node_outputs"]["chain_agent"]
    assert chain_slot["dispatch"]["next_step"] == "continue"
    assert "replan_request" not in chain_slot
