"""evaluate_precision — proving the join is measured (Spine Phase 0 exit gate)."""

from __future__ import annotations

from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.spine import CodeOntologyMapper, OntologyClass, evaluate_precision


def _store() -> FactStore:
    b = FactBatch()
    p = Provenance(file="f.py", line=1)
    b.add_node(Node("n:fraud", NodeKind.TYPE, "FraudDetectorService", "python", p))
    b.add_node(Node("n:customer", NodeKind.ENTITY, "Customer", "python", p))
    return FactStore(b)


_CLASSES = [OntologyClass("ex:FraudDetector", "Fraud Detector"), OntologyClass("ex:Customer", "Customer")]
_GOLD = {"n:fraud": "ex:FraudDetector", "n:customer": "ex:Customer"}


def test_perfect_precision_and_recall() -> None:
    cands = CodeOntologyMapper(_CLASSES).propose(_store())
    rep = evaluate_precision(cands, _GOLD, threshold=0.5)
    assert rep.true_positive == 2 and rep.false_positive == 0 and rep.false_negative == 0
    assert rep.precision == 1.0 and rep.recall == 1.0 and rep.f1 == 1.0


def test_wrong_gold_counts_as_false_positive() -> None:
    cands = CodeOntologyMapper(_CLASSES).propose(_store())
    bad_gold = {"n:fraud": "ex:SomethingElse", "n:customer": "ex:Customer"}
    rep = evaluate_precision(cands, bad_gold, threshold=0.5)
    assert rep.true_positive == 1 and rep.false_positive == 1
    assert rep.precision == 0.5


def test_high_threshold_drops_to_misses() -> None:
    cands = CodeOntologyMapper(_CLASSES).propose(_store())
    # nothing scores >= 0.95 except the exact 1.0 Customer match → fraud (0.9) becomes a miss
    rep = evaluate_precision(cands, _GOLD, threshold=0.95)
    assert rep.true_positive == 1 and rep.false_negative == 1
