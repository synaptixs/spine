from __future__ import annotations

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.verifier import (
    SchemaVerifier,
    SchemaVerifierNode,
    VerifierOutcome,
)


def _template(extra_outputs: list[FieldSchema] | None = None) -> AgentTemplate:
    outputs = [
        FieldSchema(name="confidence", type="float"),
        FieldSchema(name="caveats", type="list[str]"),
        FieldSchema(name="findings", type="str"),
    ]
    if extra_outputs:
        outputs.extend(extra_outputs)
    return AgentTemplate(
        metadata=Metadata(id="agent.x", version="0.1.0", description="x"),
        spec=AgentSpec(outputs=outputs, model="claude-opus-4-7"),
    )


def test_pass_when_all_required_present_and_typed() -> None:
    verifier = SchemaVerifier.from_template(_template())
    result = verifier.verify({"confidence": 0.9, "caveats": ["x"], "findings": "ok"})
    assert result.outcome is VerifierOutcome.PASS
    assert result.failures == ()


def test_fail_on_missing_required_field() -> None:
    verifier = SchemaVerifier.from_template(_template())
    result = verifier.verify({"confidence": 0.9, "caveats": []})
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.field == "findings" and f.rule == "missing" for f in result.failures)


def test_fail_on_type_mismatch() -> None:
    verifier = SchemaVerifier.from_template(_template())
    result = verifier.verify({"confidence": 0.9, "caveats": [], "findings": 123})
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "type_mismatch" and f.field == "findings" for f in result.failures)


def test_warn_on_confidence_out_of_range() -> None:
    verifier = SchemaVerifier.from_template(_template())
    result = verifier.verify({"confidence": 1.5, "caveats": [], "findings": "x"})
    # Out-of-range is a warn, not a fail, because the field is present and numeric.
    assert result.outcome is VerifierOutcome.WARN
    assert any(f.rule == "out_of_range" for f in result.failures)


def test_fail_when_caveats_not_a_list() -> None:
    verifier = SchemaVerifier.from_template(_template())
    result = verifier.verify({"confidence": 0.5, "caveats": "oops", "findings": "x"})
    assert result.outcome is VerifierOutcome.FAIL


async def test_node_writes_result_into_state() -> None:
    node = SchemaVerifierNode(_template(), target_node="agent")
    state = {"node_outputs": {"agent": {"confidence": 0.5, "caveats": [], "findings": "ok"}}}
    update = await node(state)
    assert update["current_node_id"] == "verify"


async def test_verifier_node_runs_post_conditions() -> None:
    from orchestrator.runtime.post_conditions import (
        FailureAction,
        MinConfidenceRule,
        PostCondition,
        PostConditionOp,
    )

    node = SchemaVerifierNode(
        _template(),
        target_node="agent",
        post_conditions=[
            PostCondition(
                field="findings",
                op=PostConditionOp.NOT_EMPTY,
                description="findings must be substantive",
                on_failure=FailureAction.CONTINUE_WITH_WARNING,
            )
        ],
        min_confidence=MinConfidenceRule(threshold=0.8),
    )
    # Schema passes, but confidence is below the floor and findings is empty.
    state = {"node_outputs": {"agent": {"confidence": 0.5, "caveats": [], "findings": ""}}}
    update = await node(state)
    verify_value = update["node_outputs"]["verify"]
    # Schema check itself passes; post_conditions degrade outcome to warn.
    assert verify_value["outcome"] == "warn"
    assert verify_value["post_conditions"]["confidence_warning"]["actual"] == 0.5
    assert verify_value["post_conditions"]["failures"][0]["field"] == "findings"
