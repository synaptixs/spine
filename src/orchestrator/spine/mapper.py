"""Code ↔ ontology mapper — *earning* the join (Phase 0).

The spine is only real if a code symbol can be reliably tied to the domain entity
it implements. This module **proposes** mappings heuristically (it never silently
trusts them) and a human **confirms or rejects** each; only confirmed mappings
become authoritative ``OntologyRef``s. Every proposal and decision is auditable.

Heuristics (deliberately simple + explainable for Phase 0):
- consider only **grounded** nodes of eligible kinds (``Type`` / ``Entity`` by
  default — the symbols that model domain concepts);
- normalize names (split camelCase + snake_case, drop generic suffixes like
  ``Service`` / ``Repository`` / ``Model``);
- score against each class's label + aliases: exact name → 1.0, exact after
  suffix-strip → 0.9, else token-set Jaccard;
- keep the top candidates per node above a confidence floor.

The output is a ranked list of ``MappingCandidate``s; the ``MappingLedger`` turns
human decisions into the resolved ``OntologyRef`` set + an audit trail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from orchestrator.pkg.facts import NodeKind
from orchestrator.pkg.store import FactStore
from orchestrator.spine.ontology import OntologyClass, OntologyRef

# Generic suffixes that carry no domain meaning — stripped before matching so
# ``FraudDetectorService`` matches the ``FraudDetector`` ontology class.
_GENERIC_SUFFIXES = frozenset(
    {
        "service",
        "manager",
        "impl",
        "model",
        "repository",
        "repo",
        "controller",
        "handler",
        "client",
        "dto",
        "entity",
        "util",
        "utils",
        "helper",
        "factory",
    }
)

_DEFAULT_KINDS = frozenset({NodeKind.TYPE, NodeKind.ENTITY})
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _tokens(name: str) -> list[str]:
    """Split an identifier into lowercase word tokens (camelCase + snake_case)."""
    parts: list[str] = []
    for chunk in name.replace("-", "_").split("_"):
        parts.extend(m.group(0) for m in _CAMEL.finditer(chunk))
    return [p.lower() for p in parts if p]


def _meaningful(tokens: list[str]) -> set[str]:
    """Token set minus generic suffixes (keep at least one token)."""
    kept = {t for t in tokens if t not in _GENERIC_SUFFIXES}
    return kept or set(tokens)


@dataclass(frozen=True)
class MappingCandidate:
    """A proposed (not yet confirmed) code-node → ontology-class mapping."""

    node_id: str
    node_name: str
    node_kind: str
    ontology_iri: str
    label: str
    confidence: float
    rationale: str
    matched_on: str


def _score(node_name: str, cls: OntologyClass) -> tuple[float, str, str] | None:
    """Best (confidence, rationale, matched_form) of ``node_name`` vs ``cls``, or None."""
    n_raw = node_name.lower()
    n_tokens = _meaningful(_tokens(node_name))
    best: tuple[float, str, str] | None = None
    for form in cls.surface_forms():
        if not form:
            continue
        f_raw = form.lower()
        f_tokens = _meaningful(_tokens(form))
        if not n_tokens or not f_tokens:
            continue
        if n_raw == f_raw:
            cand = (1.0, "exact name match", form)
        elif n_tokens == f_tokens:
            cand = (0.9, "exact match after normalization", form)
        else:
            jaccard = len(n_tokens & f_tokens) / len(n_tokens | f_tokens)
            if jaccard == 0.0:
                continue
            cand = (round(jaccard, 3), f"token overlap {jaccard:.0%}", form)
        if best is None or cand[0] > best[0]:
            best = cand
    return best


class CodeOntologyMapper:
    """Propose code↔ontology mappings from a PKG against an ontology class set."""

    def __init__(
        self,
        classes: list[OntologyClass],
        *,
        floor: float = 0.3,
        top_k: int = 3,
        kinds: frozenset[NodeKind] = _DEFAULT_KINDS,
    ) -> None:
        self._classes = list(classes)
        self._floor = floor
        self._top_k = max(1, top_k)
        self._kinds = kinds

    def propose(self, store: FactStore) -> list[MappingCandidate]:
        """Ranked candidates for every eligible grounded node (best-first)."""
        out: list[MappingCandidate] = []
        for node in store.nodes:
            if not node.grounded or node.kind not in self._kinds:
                continue
            scored: list[MappingCandidate] = []
            for cls in self._classes:
                result = _score(node.name, cls)
                if result is None or result[0] < self._floor:
                    continue
                confidence, rationale, matched = result
                scored.append(
                    MappingCandidate(
                        node_id=node.id,
                        node_name=node.name,
                        node_kind=node.kind.value,
                        ontology_iri=cls.iri,
                        label=cls.label,
                        confidence=confidence,
                        rationale=rationale,
                        matched_on=matched,
                    )
                )
            scored.sort(key=lambda c: c.confidence, reverse=True)
            out.extend(scored[: self._top_k])
        # Strongest proposals first overall — the human reviews high-confidence first.
        out.sort(key=lambda c: c.confidence, reverse=True)
        return out


@dataclass
class MappingLedger:
    """Human-in-the-loop confirmation over proposed candidates.

    ``confirm`` / ``reject`` record decisions; ``resolved`` returns only the
    confirmed mappings as authoritative ``OntologyRef``s (keyed by node id);
    ``audit`` returns the full decision trail. A node id may have multiple
    candidates — confirming one is idempotent and auto-supersedes a prior confirm.
    """

    _confirmed: dict[str, OntologyRef] = field(default_factory=dict)
    _decisions: list[dict[str, object]] = field(default_factory=list)

    def confirm(self, candidate: MappingCandidate, *, by: str = "human") -> OntologyRef:
        ref = OntologyRef(
            ontology_iri=candidate.ontology_iri,
            label=candidate.label,
            confidence=candidate.confidence,
            rationale=candidate.rationale,
            confirmed_by=by,
            matched_on=candidate.matched_on,
        )
        self._confirmed[candidate.node_id] = ref
        self._decisions.append(
            {
                "action": "confirm",
                "node_id": candidate.node_id,
                "ontology_iri": candidate.ontology_iri,
                "confidence": candidate.confidence,
                "by": by,
            }
        )
        return ref

    def reject(self, candidate: MappingCandidate, *, by: str = "human", reason: str = "") -> None:
        self._decisions.append(
            {
                "action": "reject",
                "node_id": candidate.node_id,
                "ontology_iri": candidate.ontology_iri,
                "by": by,
                "reason": reason,
            }
        )

    def resolved(self) -> dict[str, OntologyRef]:
        """Confirmed node-id → ontology reference (the authoritative join)."""
        return dict(self._confirmed)

    def ontology_iris(self) -> dict[str, str]:
        """Confirmed node-id → ``ontology_iri`` — ready to persist onto PKG nodes."""
        return {node_id: ref.ontology_iri for node_id, ref in self._confirmed.items()}

    def audit(self) -> list[dict[str, object]]:
        return list(self._decisions)


__all__ = ["CodeOntologyMapper", "MappingCandidate", "MappingLedger"]
