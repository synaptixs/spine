"""CodeOntologyMapper + MappingLedger — earning the join (Spine Phase 0)."""

from __future__ import annotations

from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.spine import CodeOntologyMapper, MappingLedger, OntologyClass


def _store() -> FactStore:
    b = FactBatch()
    prov = Provenance(file="app/fraud.py", line=1)
    b.add_node(
        Node("py:app.fraud.FraudDetectorService", NodeKind.TYPE, "FraudDetectorService", "python", prov)
    )
    b.add_node(Node("py:app.cust.Customer", NodeKind.ENTITY, "Customer", "python", prov))
    b.add_node(Node("py:app.misc.Unrelated", NodeKind.TYPE, "Unrelated", "python", prov))
    b.add_node(Node("py:app.fraud.compute", NodeKind.FUNCTION, "FraudDetector", "python", prov))  # wrong kind
    b.add_node(
        Node("py:ext.Customer", NodeKind.TYPE, "Customer", "python", None, external=True)
    )  # not grounded
    return FactStore(b)


def _classes() -> list[OntologyClass]:
    return [
        OntologyClass("ex:FraudDetector", "Fraud Detector", aliases=("fraud model",)),
        OntologyClass("ex:Customer", "Customer"),
    ]


def test_proposes_suffix_stripped_and_exact_matches() -> None:
    cands = CodeOntologyMapper(_classes()).propose(_store())
    by_node = {c.node_id: c for c in cands}
    # FraudDetectorService → FraudDetector by suffix-strip normalization (0.9)
    fd = by_node["py:app.fraud.FraudDetectorService"]
    assert fd.ontology_iri == "ex:FraudDetector" and fd.confidence == 0.9
    assert "normaliz" in fd.rationale
    # Customer → exact (1.0)
    cust = by_node["py:app.cust.Customer"]
    assert cust.ontology_iri == "ex:Customer" and cust.confidence == 1.0


def test_excludes_wrong_kind_and_ungrounded() -> None:
    ids = {c.node_id for c in CodeOntologyMapper(_classes()).propose(_store())}
    assert "py:app.fraud.compute" not in ids  # FUNCTION excluded
    assert "py:ext.Customer" not in ids  # external/ungrounded excluded


def test_floor_drops_weak_candidates() -> None:
    # "Unrelated" shares no tokens with any class → no candidate at any floor.
    cands = CodeOntologyMapper(_classes(), floor=0.3).propose(_store())
    assert all(c.node_id != "py:app.misc.Unrelated" for c in cands)


def test_results_sorted_strongest_first() -> None:
    conf = [c.confidence for c in CodeOntologyMapper(_classes()).propose(_store())]
    assert conf == sorted(conf, reverse=True)


def test_ledger_confirm_reject_audit() -> None:
    cands = CodeOntologyMapper(_classes()).propose(_store())
    fd = next(c for c in cands if c.node_id.endswith("FraudDetectorService"))
    cust = next(c for c in cands if c.node_id == "py:app.cust.Customer")
    ledger = MappingLedger()
    ledger.confirm(fd, by="alice")
    ledger.reject(cust, by="alice", reason="actually a different entity")

    resolved = ledger.resolved()
    assert set(resolved) == {"py:app.fraud.FraudDetectorService"}
    ref = resolved["py:app.fraud.FraudDetectorService"]
    assert ref.ontology_iri == "ex:FraudDetector" and ref.confirmed_by == "alice"
    assert ledger.ontology_iris() == {"py:app.fraud.FraudDetectorService": "ex:FraudDetector"}
    actions = [(d["action"], d["node_id"]) for d in ledger.audit()]
    assert ("confirm", "py:app.fraud.FraudDetectorService") in actions
    assert ("reject", "py:app.cust.Customer") in actions
