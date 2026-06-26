"""MappingStore — durable confirmed mappings (Spine Phase 0 persistence)."""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.spine import CodeOntologyMapper, MappingLedger, MappingStore, OntologyClass
from orchestrator.spine.ontology import OntologyRef


def _resolved() -> dict[str, OntologyRef]:
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
    cands = CodeOntologyMapper([OntologyClass("ex:FraudDetector", "Fraud Detector")]).propose(FactStore(b))
    ledger = MappingLedger()
    ledger.confirm(cands[0], by="alice")
    return ledger.resolved()


def test_save_load_roundtrip(tmp_path: Path) -> None:
    store = MappingStore(tmp_path / "mappings.json")
    store.save(_resolved())
    loaded = store.load()
    assert set(loaded) == {"py:app.fraud.FraudDetector"}
    ref = loaded["py:app.fraud.FraudDetector"]
    assert ref.ontology_iri == "ex:FraudDetector"
    assert ref.label == "Fraud Detector"
    assert ref.confirmed_by == "alice"
    assert ref.confidence == 0.9


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    assert MappingStore(tmp_path / "nope.json").load() == {}


def test_code_for_iri_inverts(tmp_path: Path) -> None:
    store = MappingStore(tmp_path / "m.json")
    store.save(_resolved())
    assert store.code_for_iri() == {"ex:FraudDetector": ["py:app.fraud.FraudDetector"]}
