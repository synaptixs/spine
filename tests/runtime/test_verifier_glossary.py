from __future__ import annotations

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.verifiers import (
    GlossaryVerifier,
    VerifierContext,
    VerifierOutcome,
)


def _ctx(glossary: dict[str, dict[str, str]] | None = None) -> VerifierContext:
    template = AgentTemplate(
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
    return VerifierContext(
        template=template,
        node_id="n_x",
        task_id="t",
        trace_id="tr",
        task_glossary=glossary or {},
    )


async def test_no_glossary_passes() -> None:
    result = await GlossaryVerifier().verify({"findings": "Q1 churn rose."}, _ctx())
    assert result.outcome is VerifierOutcome.PASS


async def test_consistent_inline_definition_passes() -> None:
    glossary = {"churn": {"value": "logo churn (count of fully cancelled accounts)", "source": "org_default"}}
    result = await GlossaryVerifier().verify(
        {"findings": "Churn means logo churn (count of fully cancelled accounts) in our model."},
        _ctx(glossary),
    )
    assert result.outcome is VerifierOutcome.PASS


async def test_contradicting_inline_definition_fails() -> None:
    glossary = {"churn": {"value": "logo churn", "source": "org_default"}}
    result = await GlossaryVerifier().verify(
        {"findings": "Churn means revenue churn for this analysis."},
        _ctx(glossary),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "glossary_contradiction" for f in result.failures)
    msg = result.failures[0].message
    assert "churn" in msg.lower() and "revenue churn" in msg.lower()


async def test_unrelated_text_passes_even_with_glossary() -> None:
    glossary = {"churn": {"value": "logo churn", "source": "org_default"}}
    result = await GlossaryVerifier().verify(
        {"findings": "Customer base grew 12% QoQ."},
        _ctx(glossary),
    )
    assert result.outcome is VerifierOutcome.PASS


async def test_handles_string_only_glossary_entries() -> None:
    # Backwards-compat: user-supplied glossary may be a bare string per term.
    result = await GlossaryVerifier().verify(
        {"findings": "Churn means revenue churn for this report."},
        _ctx({"churn": {"value": "logo churn", "source": "user_specified"}}),
    )
    assert result.outcome is VerifierOutcome.FAIL


async def test_equals_definition_form_also_caught() -> None:
    glossary = {"arr": {"value": "annual recurring revenue", "source": "org_default"}}
    result = await GlossaryVerifier().verify(
        {"findings": "ARR = annualised receivables. Q1 grew 12%."},
        _ctx(glossary),
    )
    assert result.outcome is VerifierOutcome.FAIL


async def test_walks_into_claims_list() -> None:
    glossary = {"churn": {"value": "logo churn", "source": "org_default"}}
    output = {
        "findings": "Q1 was strong.",
        "claims": [{"id": "c_1", "statement": "Churn means revenue churn here."}],
    }
    result = await GlossaryVerifier().verify(output, _ctx(glossary))
    assert result.outcome is VerifierOutcome.FAIL
    assert any("claims" in f.field for f in result.failures)
