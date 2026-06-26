"""Durable store for confirmed code↔ontology mappings (Phase 0 persistence).

A confirmed mapping (``MappingLedger.resolved()``) is the earned join; it must
survive across runs so later seams (remediation scoping, lineage, shipment) can
use it without re-confirming. This is a small JSON store — self-contained, no DB,
no external service — keyed by repo. The PKG carries the same ``ontology_iri`` on
nodes at extraction time later; this is the authoritative source of truth in the
meantime.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.spine.ontology import OntologyRef

_VERSION = 1


def serialize(resolved: dict[str, OntologyRef]) -> dict[str, object]:
    """Confirmed mappings → a JSON-able dict."""
    return {
        "version": _VERSION,
        "mappings": {
            node_id: {
                "ontology_iri": ref.ontology_iri,
                "label": ref.label,
                "confidence": ref.confidence,
                "rationale": ref.rationale,
                "confirmed_by": ref.confirmed_by,
                "matched_on": ref.matched_on,
            }
            for node_id, ref in resolved.items()
        },
    }


def deserialize(payload: dict[str, object]) -> dict[str, OntologyRef]:
    """A persisted dict → ``node_id -> OntologyRef``."""
    out: dict[str, OntologyRef] = {}
    mappings = payload.get("mappings") or {}
    if isinstance(mappings, dict):
        for node_id, raw in mappings.items():
            out[str(node_id)] = OntologyRef(
                ontology_iri=str(raw.get("ontology_iri", "")),
                label=str(raw.get("label", "")),
                confidence=float(raw.get("confidence", 0.0) or 0.0),
                rationale=str(raw.get("rationale", "")),
                confirmed_by=str(raw.get("confirmed_by", "")),
                matched_on=str(raw.get("matched_on", "")),
            )
    return out


class MappingStore:
    """JSON-file store of confirmed mappings for one repo."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def save(self, resolved: dict[str, OntologyRef]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(serialize(resolved), indent=2), encoding="utf-8")

    def load(self) -> dict[str, OntologyRef]:
        if not self._path.is_file():
            return {}
        return deserialize(json.loads(self._path.read_text(encoding="utf-8")))

    def code_for_iri(self) -> dict[str, list[str]]:
        """Inverted view ``ontology_iri -> [node_id]`` (the code scope for an entity)."""
        out: dict[str, list[str]] = {}
        for node_id, ref in self.load().items():
            out.setdefault(ref.ontology_iri, []).append(node_id)
        return out


__all__ = ["MappingStore", "deserialize", "serialize"]
