"""The Ontology Reference contract — the ontology side of the join (Phase 0).

``OntologyClass`` is a domain entity as ontomesh would mint it (an IRI + a human
label + optional aliases). In Phase 0 these are *inputs* — Spine does not yet call
ontomesh; that wiring is Phase 1 (Seam 1). Keeping the ontology as an input is what
lets Phase 0 stand alone and be tested deterministically.

``OntologyRef`` is what a code node carries once a mapping is **confirmed**: the
``ontology_iri`` plus the confidence and rationale that justified it. This is the
reference the PKG persists and the lineage record cites.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OntologyClass:
    """A domain entity/class from the ontology (ontomesh-minted in production)."""

    iri: str
    label: str
    aliases: tuple[str, ...] = ()

    def surface_forms(self) -> tuple[str, ...]:
        """All name strings this class can be matched against (label + aliases)."""
        return (self.label, *self.aliases)


@dataclass(frozen=True)
class OntologyRef:
    """A confirmed code-node → ontology-class reference.

    ``confidence`` is the mapper's score at confirmation time; ``rationale`` records
    *why* (exact match, alias, token overlap) so the join is auditable, never opaque.
    """

    ontology_iri: str
    label: str
    confidence: float
    rationale: str
    confirmed_by: str = ""
    matched_on: str = ""


@dataclass
class OntologyCatalog:
    """A small in-memory set of ontology classes to map against (Phase 0 input)."""

    classes: list[OntologyClass] = field(default_factory=list)

    def add(self, iri: str, label: str, aliases: tuple[str, ...] = ()) -> OntologyClass:
        cls = OntologyClass(iri=iri, label=label, aliases=aliases)
        self.classes.append(cls)
        return cls


__all__ = ["OntologyCatalog", "OntologyClass", "OntologyRef"]
