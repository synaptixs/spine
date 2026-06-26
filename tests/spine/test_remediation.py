"""plan_remediations — drift → governed, scoped remediation tasks (Spine Phase 2)."""

from __future__ import annotations

from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.spine import (
    CodeOntologyMapper,
    DriftReport,
    MappingLedger,
    OntologyClass,
    code_for_iri_from_ledger,
    plan_remediations,
)
from orchestrator.spine.drift import DriftFinding

ENTITY = "FraudDetector_v5::APAC::CardTransactions"
IRI = "ex:FraudDetector"


def _report(severity: str = "critical") -> DriftReport:
    return DriftReport(
        findings=(
            DriftFinding(
                entity_key=ENTITY,
                severity=severity,
                metric_type="ece",
                message="calibration eroded",
                recommendation="recalibrate scores; add a calibration monitor",
                window_id="w1",
            ),
        )
    )


def _ledger_with_fraud_mapping() -> MappingLedger:
    b = FactBatch()
    b.add_node(
        Node(
            "py:app.fraud.FraudDetector",
            NodeKind.TYPE,
            "FraudDetector",
            "python",
            Provenance(file="app/fraud.py", line=1),
        )
    )
    cands = CodeOntologyMapper([OntologyClass(IRI, "Fraud Detector")]).propose(FactStore(b))
    ledger = MappingLedger()
    ledger.confirm(cands[0], by="alice")
    return ledger


def test_code_scope_inverts_confirmed_mappings() -> None:
    code_for_iri = code_for_iri_from_ledger(_ledger_with_fraud_mapping())
    assert code_for_iri == {IRI: ["py:app.fraud.FraudDetector"]}


def test_plan_builds_scoped_task_with_provenance() -> None:
    code_for_iri = code_for_iri_from_ledger(_ledger_with_fraud_mapping())
    tasks = plan_remediations(
        _report(),
        entity_iris={ENTITY: IRI},
        code_for_iri=code_for_iri,
        guardrails_for_iri={IRI: ["A transaction must reference a known customer (SHACL)"]},
    )
    assert len(tasks) == 1
    t = tasks[0]
    assert t.entity_key == ENTITY and t.is_scoped
    assert t.code_node_ids == ["py:app.fraud.FraudDetector"]
    # spec is an orchestrator codegen spec, carrying the recommendation as a criterion
    assert t.spec["title"].startswith("Remediate drift")
    assert any("recalibrate" in c for c in t.spec["acceptance_criteria"])
    # guardrails: ontology/SHACL + human gate + scope-limit
    assert any("SHACL" in g for g in t.guardrails)
    assert any("Human approval required" in g for g in t.guardrails)
    assert any("Limit changes to" in g for g in t.guardrails)
    # provenance: the full chain
    assert t.provenance["entity_key"] == ENTITY
    assert t.provenance["ontology_iri"] == IRI
    assert t.provenance["window_id"] == "w1"
    assert t.provenance["findings"][0]["metric_type"] == "ece"


def test_unmapped_entity_still_produces_task_flagged_unscoped() -> None:
    tasks = plan_remediations(_report(), entity_iris={}, code_for_iri={})
    assert len(tasks) == 1
    assert tasks[0].is_scoped is False
    assert any("scope unresolved" in g.lower() for g in tasks[0].guardrails)


def test_below_severity_yields_no_task() -> None:
    tasks = plan_remediations(
        _report(severity="warning"),
        entity_iris={ENTITY: IRI},
        code_for_iri={},
        min_severity="critical",
    )
    assert tasks == []
