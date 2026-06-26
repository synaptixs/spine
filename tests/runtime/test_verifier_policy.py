from __future__ import annotations

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.verifiers import (
    PolicyVerifier,
    VerifierContext,
    VerifierOutcome,
)


def _template(*, classification_ceiling: str | None = None, requires_approval: bool = False) -> AgentTemplate:
    constraints: dict[str, object] = {}
    if classification_ceiling is not None:
        constraints["max_classification"] = classification_ceiling
    if requires_approval:
        constraints["requires_approval"] = True
    return AgentTemplate(
        metadata=Metadata(id="agent.x", version="0.1.0", description="x"),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="findings", type="str"),
            ],
            model="claude-haiku-4-5-20251001",
            constraints=constraints,
        ),
    )


def _ctx(template: AgentTemplate | None = None) -> VerifierContext:
    return VerifierContext(
        template=template or _template(),
        node_id="n_x",
        task_id="t",
        trace_id="tr",
    )


async def test_clean_output_passes() -> None:
    v = PolicyVerifier()
    result = await v.verify(
        {"confidence": 0.9, "caveats": [], "findings": "Q1 revenue rose."},
        _ctx(),
    )
    assert result.outcome is VerifierOutcome.PASS


async def test_ssn_detection_fails() -> None:
    v = PolicyVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "caveats": [],
            "findings": "Customer SSN 123-45-6789 should never appear.",
        },
        _ctx(),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "pii_ssn" for f in result.failures)
    # Audit row must not contain the matched value itself.
    for f in result.failures:
        assert "123-45-6789" not in f.message


async def test_credit_card_with_valid_luhn_fails() -> None:
    """4111 1111 1111 1111 is the standard test Visa number; passes Luhn."""
    v = PolicyVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "caveats": [],
            "findings": "Card: 4111-1111-1111-1111",
        },
        _ctx(),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "pii_credit_card" for f in result.failures)


async def test_random_16_digit_string_is_not_credit_card() -> None:
    """Same length, but invalid Luhn — must not trigger."""
    v = PolicyVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "caveats": [],
            "findings": "Lot ID 1234567890123456 (not a card).",
        },
        _ctx(),
    )
    assert not any(f.rule == "pii_credit_card" for f in result.failures)


async def test_email_detection_fails() -> None:
    v = PolicyVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "caveats": [],
            "findings": "Reach me at jane.doe@example.com.",
        },
        _ctx(),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "pii_email" for f in result.failures)


async def test_phone_detection_warns() -> None:
    v = PolicyVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "caveats": [],
            "findings": "Call (415) 555-1212 for details.",
        },
        _ctx(),
    )
    # Phone-like tokens warn, not fail (false-positive rate is high).
    assert result.outcome is VerifierOutcome.WARN
    assert any(f.rule == "pii_phone" for f in result.failures)


async def test_classification_above_ceiling_fails() -> None:
    v = PolicyVerifier()
    template = _template(classification_ceiling="internal")
    result = await v.verify(
        {"confidence": 0.9, "caveats": [], "findings": "x", "classification": "restricted"},
        _ctx(template),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "classification_exceeds_ceiling" for f in result.failures)


async def test_classification_at_or_below_ceiling_passes() -> None:
    v = PolicyVerifier()
    template = _template(classification_ceiling="confidential")
    result = await v.verify(
        {"confidence": 0.9, "caveats": [], "findings": "x", "classification": "internal"},
        _ctx(template),
    )
    assert result.outcome is VerifierOutcome.PASS


async def test_unknown_classification_warns() -> None:
    v = PolicyVerifier()
    template = _template(classification_ceiling="internal")
    result = await v.verify(
        {"confidence": 0.9, "caveats": [], "findings": "x", "classification": "ultra-classified"},
        _ctx(template),
    )
    assert result.outcome is VerifierOutcome.WARN


async def test_approval_missing_warns() -> None:
    v = PolicyVerifier()
    template = _template(requires_approval=True)
    result = await v.verify(
        {"confidence": 0.9, "caveats": [], "findings": "x"},
        _ctx(template),
    )
    assert result.outcome is VerifierOutcome.WARN
    assert any(f.rule == "approval_missing" for f in result.failures)


async def test_approval_present_passes() -> None:
    v = PolicyVerifier()
    template = _template(requires_approval=True)
    result = await v.verify(
        {
            "confidence": 0.9,
            "caveats": [],
            "findings": "x",
            "approval_artifact_id": "task/t/approval/0001.json",
        },
        _ctx(template),
    )
    assert result.outcome is VerifierOutcome.PASS
