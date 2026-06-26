from __future__ import annotations

import pytest

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.verifiers import (
    ConfidenceVerifier,
    VerifierContext,
    VerifierOutcome,
)


def _ctx() -> VerifierContext:
    template = AgentTemplate(
        metadata=Metadata(id="agent.x", version="0.1.0", description="x"),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
            ],
            model="claude-haiku-4-5-20251001",
        ),
    )
    return VerifierContext(template=template, node_id="n_x", task_id="t", trace_id="tr")


async def test_above_threshold_passes() -> None:
    v = ConfidenceVerifier(threshold=0.7)
    result = await v.verify({"confidence": 0.9}, _ctx())
    assert result.outcome is VerifierOutcome.PASS
    assert result.failures == ()


async def test_within_warn_band_warns() -> None:
    """Threshold 0.7, warn_band 0.10 => warn floor 0.63. 0.65 ∈ [0.63, 0.70)."""
    v = ConfidenceVerifier(threshold=0.7, warn_band=0.10)
    result = await v.verify({"confidence": 0.65}, _ctx())
    assert result.outcome is VerifierOutcome.WARN
    assert any(f.rule == "below_threshold" for f in result.failures)


async def test_below_warn_band_fails() -> None:
    """0.5 is more than 10% below 0.7 -> fail."""
    v = ConfidenceVerifier(threshold=0.7, warn_band=0.10)
    result = await v.verify({"confidence": 0.5}, _ctx())
    assert result.outcome is VerifierOutcome.FAIL


async def test_missing_confidence_fails() -> None:
    v = ConfidenceVerifier()
    result = await v.verify({}, _ctx())
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "missing_or_non_numeric" for f in result.failures)


async def test_non_numeric_confidence_fails() -> None:
    v = ConfidenceVerifier()
    result = await v.verify({"confidence": "high"}, _ctx())
    assert result.outcome is VerifierOutcome.FAIL


async def test_out_of_range_confidence_fails() -> None:
    v = ConfidenceVerifier()
    result = await v.verify({"confidence": 1.5}, _ctx())
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "out_of_range" for f in result.failures)


def test_invalid_threshold_rejected() -> None:
    with pytest.raises(ValueError):
        ConfidenceVerifier(threshold=1.5)
    with pytest.raises(ValueError):
        ConfidenceVerifier(threshold=0.7, warn_band=0.0)
