"""Sprint 10.9: regression suite that injects errors across categories.

The spec mandates documented catch rates per category. Sprint 10's chain
defaults (confidence + evidence + policy) target ≥90% catch on:

  - low confidence
  - missing required fields
  - unsupported claim (claim with no supporting artifact OR claim citing
    an artifact that doesn't exist in the store)
  - PII leakage (SSN / credit card / email)

Each category gets 10 injected outputs. We assert each catches at least
9/10 — the "90%+" floor the spec calls out — and emit the measured
rate per category so a future tightening is auditable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.artifacts import InMemoryArtifactStore
from orchestrator.runtime.verifier import SchemaVerifier
from orchestrator.runtime.verifiers import (
    ConfidenceVerifier,
    EvidenceVerifier,
    PolicyVerifier,
    VerifierChain,
    VerifierContext,
    VerifierOutcome,
)

CATCH_RATE_FLOOR = 0.90


def _template() -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="agent.analyst", version="0.1.0", description="x"),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
                FieldSchema(name="findings", type="str"),
                FieldSchema(name="claims", type="list", required=False),
            ],
            model="claude-haiku-4-5-20251001",
        ),
    )


async def _populated_store() -> InMemoryArtifactStore:
    store = InMemoryArtifactStore()
    await store.put_json("art_q1", {"metrics": {"qoq_growth": 0.12}, "source": "warehouse"})
    return store


def _ctx(store: InMemoryArtifactStore) -> VerifierContext:
    return VerifierContext(
        template=_template(),
        node_id="n_analyst",
        task_id="t-1",
        trace_id="tr-1",
        artifact_store=store,
    )


def _baseline_output() -> dict[str, Any]:
    """A clean output that every verifier passes."""
    return {
        "confidence": 0.9,
        "caveats": ["single-pass"],
        "findings": "Q1 revenue rose 12% QoQ.",
        "claims": [
            {
                "id": "c_q1",
                "statement": "Q1 revenue rose 12% QoQ.",
                "claim_type": "metric",
                "supporting_artifacts": [{"artifact_id": "art_q1"}],
                "metric_values": {"qoq_growth": 0.12},
                "confidence": 0.9,
                "caveats": [],
            }
        ],
    }


# --- injectors ------------------------------------------------------------


def _inject_low_confidence(i: int) -> dict[str, Any]:
    out = _baseline_output()
    # Spread across the failure band so we don't accidentally hit a corner case.
    out["confidence"] = 0.40 + (i % 5) * 0.04  # 0.40, 0.44, 0.48, 0.52, 0.56
    out["claims"][0]["confidence"] = out["confidence"]
    return out


def _inject_missing_field(i: int) -> dict[str, Any]:
    out = _baseline_output()
    # Drop one of the required fields per injection, cycling through.
    field = ["caveats", "findings", "confidence"][i % 3]
    out.pop(field, None)
    return out


def _inject_unsupported_claim(i: int) -> dict[str, Any]:
    out = _baseline_output()
    # Half: claim with no supporting_artifacts. Half: artifact id that
    # doesn't exist in the store.
    if i % 2 == 0:
        out["claims"][0]["supporting_artifacts"] = []
    else:
        out["claims"][0]["supporting_artifacts"] = [{"artifact_id": f"art_missing_{i}"}]
    return out


_PII_SAMPLES = [
    "Customer SSN 123-45-6789",
    "Card on file: 4111-1111-1111-1111",
    "Reach me at jane.doe@example.com",
    "Patient SSN 234-56-7890",
    "Visa 4012 8888 8888 1881",
    "Email: alice@example.org",
    "SSN: 345-67-8901",
    "MasterCard 5555-5555-5555-4444",
    "contact: bob@example.net",
    "MCN 4485 4730 0220 6195",
]


def _inject_pii(i: int) -> dict[str, Any]:
    out = _baseline_output()
    out["findings"] = f"{out['findings']} {_PII_SAMPLES[i % len(_PII_SAMPLES)]}"
    return out


def _build_chain() -> VerifierChain:
    return VerifierChain(
        [
            ConfidenceVerifier(threshold=0.7),
            EvidenceVerifier(),
            PolicyVerifier(),
        ]
    )


async def _catch_rate(
    injector: Callable[[int], dict[str, Any]],
    *,
    samples: int = 10,
    schema_check: bool = False,
) -> tuple[float, list[str]]:
    """Run ``samples`` injections through the chain (+ schema check optionally).

    Returns (catch_rate, sampled_outcomes). A sample "catches" when the
    aggregate outcome is FAIL — that's what the spec measures.
    """
    store = await _populated_store()
    chain = _build_chain()
    schema_verifier = SchemaVerifier.from_template(_template())
    outcomes: list[str] = []
    catches = 0
    for i in range(samples):
        output = injector(i)
        # Schema check is a separate concern from the chain — the spec
        # lists missing-field as a category. For that injector we want the
        # combined outcome of schema + chain.
        if schema_check:
            schema_result = schema_verifier.verify(output)
            # Sprint 5's SchemaVerifier returns its own VerifierOutcome enum
            # under runtime.verifier; compare by string value across the two.
            if schema_result.outcome.value == "fail":
                outcomes.append("fail")
                catches += 1
                continue
        result = await chain.run(output, _ctx(store))
        outcomes.append(result.outcome.value)
        if result.outcome is VerifierOutcome.FAIL:
            catches += 1
    return catches / samples, outcomes


# --- the actual assertions -------------------------------------------------


async def test_low_confidence_catch_rate_at_least_90_percent() -> None:
    rate, outcomes = await _catch_rate(_inject_low_confidence)
    assert rate >= CATCH_RATE_FLOOR, f"low_confidence caught only {rate:.0%}: {outcomes}"


async def test_missing_required_field_catch_rate_at_least_90_percent() -> None:
    # Schema verification owns this category; the chain is a downstream
    # safety net.
    rate, outcomes = await _catch_rate(_inject_missing_field, schema_check=True)
    assert rate >= CATCH_RATE_FLOOR, f"missing_field caught only {rate:.0%}: {outcomes}"


async def test_unsupported_claim_catch_rate_at_least_90_percent() -> None:
    rate, outcomes = await _catch_rate(_inject_unsupported_claim)
    assert rate >= CATCH_RATE_FLOOR, f"unsupported_claim caught only {rate:.0%}: {outcomes}"


async def test_pii_leakage_catch_rate_at_least_90_percent() -> None:
    rate, outcomes = await _catch_rate(_inject_pii)
    assert rate >= CATCH_RATE_FLOOR, f"pii caught only {rate:.0%}: {outcomes}"


async def test_baseline_clean_output_is_not_a_false_positive() -> None:
    """The clean baseline output must pass every verifier — no false fails."""
    store = await _populated_store()
    chain = _build_chain()
    result = await chain.run(_baseline_output(), _ctx(store))
    assert result.outcome is VerifierOutcome.PASS, result.to_state_value()


@pytest.mark.parametrize(
    "label, injector, schema_check",
    [
        ("low_confidence", _inject_low_confidence, False),
        ("missing_field", _inject_missing_field, True),
        ("unsupported_claim", _inject_unsupported_claim, False),
        ("pii", _inject_pii, False),
    ],
    ids=["low_confidence", "missing_field", "unsupported_claim", "pii"],
)
async def test_catch_rate_per_category(
    label: str,
    injector: Callable[[int], dict[str, Any]],
    schema_check: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rate, outcomes = await _catch_rate(injector, schema_check=schema_check)
    # Surface the per-category rate so tightening the floor in the future is
    # informed by real numbers.
    print(f"category={label} catch_rate={rate:.0%} outcomes={outcomes}")
    assert rate >= CATCH_RATE_FLOOR
