"""Measure mapping quality — *prove* the join, don't assume it (Phase 0 exit gate).

Phase 0 is only "done" when the code↔ontology mapper clears a precision bar on a
real domain. This evaluates the mapper's **top-1 proposal per node** at a
confidence threshold against a gold-standard mapping (``node_id → ontology_iri``):

- **precision** — of the proposals we'd surface, how many are right (the number
  that matters: a wrong mapping silently corrupts the spine);
- **recall** — of the entities that should map, how many we proposed;
- **coverage** — how many gold entities got any proposal at/above threshold.

Sweep the threshold to choose the confidence floor for human review.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.spine.mapper import MappingCandidate


@dataclass(frozen=True)
class PrecisionReport:
    """Top-1 precision/recall of proposals vs. a gold mapping at a threshold."""

    threshold: float
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def proposed(self) -> int:
        return self.true_positive + self.false_positive

    @property
    def precision(self) -> float:
        return self.true_positive / self.proposed if self.proposed else 1.0

    @property
    def recall(self) -> float:
        relevant = self.true_positive + self.false_negative
        return self.true_positive / relevant if relevant else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "threshold": self.threshold,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
        }


def evaluate_precision(
    candidates: list[MappingCandidate],
    gold: dict[str, str],
    *,
    threshold: float = 0.5,
) -> PrecisionReport:
    """Score the mapper's best proposal per node against ``gold`` at ``threshold``.

    ``gold`` maps ``node_id → expected ontology_iri``. Only the highest-confidence
    candidate per node at/above ``threshold`` counts (that's what a reviewer sees).
    """
    best: dict[str, MappingCandidate] = {}
    for cand in candidates:
        if cand.confidence < threshold:
            continue
        current = best.get(cand.node_id)
        if current is None or cand.confidence > current.confidence:
            best[cand.node_id] = cand

    tp = fp = 0
    for node_id, cand in best.items():
        if node_id in gold and gold[node_id] == cand.ontology_iri:
            tp += 1
        else:
            fp += 1
    # Gold entities we never proposed (at/above threshold) are misses.
    fn = sum(1 for node_id in gold if node_id not in best)
    return PrecisionReport(threshold=threshold, true_positive=tp, false_positive=fp, false_negative=fn)


__all__ = ["PrecisionReport", "evaluate_precision"]
