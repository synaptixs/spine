"""LineageIndex — query any artifact → the full chain (Spine Phase 4)."""

from __future__ import annotations

from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.spine import (
    CodeOntologyMapper,
    DriftReport,
    LineageIndex,
    MappingLedger,
    OntologyClass,
    RegistrationRequest,
    code_for_iri_from_ledger,
    correlation_handles,
    plan_remediations,
)
from orchestrator.spine.drift import DriftFinding

EK = "FraudDetector_v5::APAC::CardTransactions"
IRI = "ex:FraudDetector"
NODE = "py:app.fraud.FraudDetector"


def _ledger() -> MappingLedger:
    b = FactBatch()
    b.add_node(Node(NODE, NodeKind.TYPE, "FraudDetector", "python", Provenance(file="app/fraud.py", line=1)))
    cands = CodeOntologyMapper([OntologyClass(IRI, "Fraud Detector")]).propose(FactStore(b))
    ledger = MappingLedger()
    ledger.confirm(cands[0])
    return ledger


def _full_index() -> LineageIndex:
    ledger = _ledger()
    report = DriftReport(
        findings=(
            DriftFinding(
                entity_key=EK,
                severity="critical",
                metric_type="ece",
                message="calibration eroded",
                window_id="w1",
            ),
        )
    )
    tasks = plan_remediations(report, entity_iris={EK: IRI}, code_for_iri=code_for_iri_from_ledger(ledger))
    idx = LineageIndex()
    idx.add_mappings(ledger)
    idx.add_shipment(
        RegistrationRequest(
            entity_key=EK,
            version="5",
            model_version="5",
            baseline_id=f"{EK}@ship",
            ontology_iri=IRI,
            trace_id="trace-xyz",
            pr_url="http://pr/9",
        )
    )
    idx.add_drift(report.findings[0])
    idx.add_remediation(tasks[0])
    idx.add_memory(EK, "mem:never-ship-uncalibrated-scores")
    return idx


def test_for_entity_reconstructs_the_full_chain() -> None:
    rec = _full_index().for_entity(EK)
    assert rec.ontology_iri == IRI and rec.ontology_label == "Fraud Detector"
    assert rec.code_node_ids == (NODE,)
    assert rec.shipments[0]["version"] == "5" and rec.shipments[0]["trace_id"] == "trace-xyz"
    assert rec.drift[0]["metric_type"] == "ece"
    assert rec.remediations[0]["title"].startswith("Remediate drift")
    assert rec.memory_refs == ("mem:never-ship-uncalibrated-scores",)
    # every link of the chain is present
    assert rec.stages_present == {
        "domain",
        "code",
        "deployment",
        "shipment",
        "drift",
        "remediation",
        "memory",
    }


def test_query_from_any_entry_point_yields_same_entity() -> None:
    idx = _full_index()
    assert idx.for_node(NODE)[0].entity_key == EK  # from code
    assert idx.for_iri(IRI)[0].entity_key == EK  # from domain concept
    assert idx.for_trace("trace-xyz")[0].entity_key == EK  # from a build trace


def test_unknown_entry_points_are_empty() -> None:
    idx = _full_index()
    assert idx.for_node("py:nope") == []
    assert idx.for_trace("no-such-trace") == []
    assert idx.for_iri("ex:Nothing") == []


def test_correlation_handles_span_three_planes() -> None:
    rec = _full_index().for_entity(EK)
    handles = correlation_handles(rec)
    assert handles["entity_key"] == EK
    assert handles["ontology_iri"] == IRI
    assert handles["trace_ids"] == ["trace-xyz"]
    assert 'trace_id="trace-xyz"' in handles["otel_filter"]  # orchestrator OTel
    assert handles["prometheus_label"] == f'entity_key="{EK}"'  # infodrift/ontomesh


def test_shipment_auto_links_entity_to_iri_without_explicit_call() -> None:
    # add_shipment carries ontology_iri → the entity↔iri link is inferred.
    idx = LineageIndex()
    idx.add_shipment(
        RegistrationRequest(entity_key=EK, version="5", model_version="5", baseline_id="b", ontology_iri=IRI)
    )
    assert idx.for_iri(IRI)[0].entity_key == EK
