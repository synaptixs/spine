from __future__ import annotations

from typing import Any

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.artifacts import InMemoryArtifactStore
from orchestrator.runtime.verifiers import (
    EvidenceVerifier,
    VerifierContext,
    VerifierOutcome,
)


def _ctx(store: InMemoryArtifactStore | None = None) -> VerifierContext:
    template = AgentTemplate(
        metadata=Metadata(id="agent.x", version="0.1.0", description="x"),
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
    return VerifierContext(
        template=template,
        node_id="n_analyst",
        task_id="t-1",
        trace_id="tr-1",
        artifact_store=store,
    )


def _claim(
    *,
    id: str,
    artifact_id: str,
    metric_values: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "statement": f"{id} statement",
        "claim_type": "metric" if metric_values else "qualitative",
        "supporting_artifacts": [{"artifact_id": artifact_id}],
        "metric_values": metric_values or {},
        "confidence": 0.9,
        "caveats": [],
    }


async def test_no_claims_passes() -> None:
    v = EvidenceVerifier()
    result = await v.verify({"confidence": 0.9, "findings": "ok"}, _ctx())
    assert result.outcome is VerifierOutcome.PASS


async def test_claim_without_supporting_artifacts_fails() -> None:
    v = EvidenceVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "findings": "ok",
            "claims": [{"id": "c_1", "statement": "x", "supporting_artifacts": []}],
        },
        _ctx(InMemoryArtifactStore()),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "missing_supporting_artifacts" for f in result.failures)


async def test_artifact_present_no_metrics_passes() -> None:
    store = InMemoryArtifactStore()
    await store.put_json("art_001", {"findings": "ARR up"})
    v = EvidenceVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "findings": "ok",
            "claims": [_claim(id="c_1", artifact_id="art_001")],
        },
        _ctx(store),
    )
    assert result.outcome is VerifierOutcome.PASS


async def test_metric_within_tolerance_passes() -> None:
    store = InMemoryArtifactStore()
    await store.put_json("art_001", {"metrics": {"qoq_growth": 0.121}})
    v = EvidenceVerifier(tolerance=0.05)
    result = await v.verify(
        {
            "confidence": 0.9,
            "findings": "ok",
            "claims": [_claim(id="c_growth", artifact_id="art_001", metric_values={"qoq_growth": 0.12})],
        },
        _ctx(store),
    )
    assert result.outcome is VerifierOutcome.PASS


async def test_metric_outside_tolerance_fails() -> None:
    store = InMemoryArtifactStore()
    await store.put_json("art_001", {"metrics": {"qoq_growth": 0.15}})
    v = EvidenceVerifier(tolerance=0.01)
    result = await v.verify(
        {
            "confidence": 0.9,
            "findings": "ok",
            "claims": [_claim(id="c_growth", artifact_id="art_001", metric_values={"qoq_growth": 0.12})],
        },
        _ctx(store),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "metric_mismatch" for f in result.failures)


async def test_artifact_missing_fails() -> None:
    store = InMemoryArtifactStore()
    v = EvidenceVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "findings": "ok",
            "claims": [_claim(id="c_1", artifact_id="art_missing")],
        },
        _ctx(store),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "artifact_not_found" for f in result.failures)


async def test_metric_missing_in_artifact_fails() -> None:
    store = InMemoryArtifactStore()
    await store.put_json("art_001", {"metrics": {"churn": 0.05}})
    v = EvidenceVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "findings": "ok",
            "claims": [_claim(id="c_growth", artifact_id="art_001", metric_values={"qoq_growth": 0.12})],
        },
        _ctx(store),
    )
    assert result.outcome is VerifierOutcome.FAIL
    assert any(f.rule == "metric_missing_in_artifact" for f in result.failures)


async def test_spot_check_is_deterministic() -> None:
    """Same (claims, node_id) picks the same claim every run."""
    store = InMemoryArtifactStore()
    await store.put_json("art_001", {"metrics": {"a": 1.0}})
    await store.put_json("art_002", {"metrics": {"b": 2.0}})
    await store.put_json("art_003", {"metrics": {"c": 3.0}})

    v = EvidenceVerifier()
    output = {
        "confidence": 0.9,
        "findings": "ok",
        "claims": [
            _claim(id="c_a", artifact_id="art_001"),
            _claim(id="c_b", artifact_id="art_002"),
            _claim(id="c_c", artifact_id="art_003"),
        ],
    }
    runs = [await v.verify(output, _ctx(store)) for _ in range(5)]
    outcomes = [r.outcome for r in runs]
    assert all(o is outcomes[0] for o in outcomes)


async def test_no_artifact_store_warns_instead_of_failing() -> None:
    v = EvidenceVerifier()
    result = await v.verify(
        {
            "confidence": 0.9,
            "findings": "ok",
            "claims": [_claim(id="c_1", artifact_id="art_001")],
        },
        _ctx(store=None),
    )
    # Verifier is configured but cannot do the spot-check; warn rather than fail.
    assert result.outcome is VerifierOutcome.WARN
    assert any(f.rule == "artifact_store_unavailable" for f in result.failures)
