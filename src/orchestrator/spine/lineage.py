"""The Lineage Record — provenance, end to end (Phase 4).

The capstone of the spine: a queryable envelope that joins every stage on the
shared keys, so from *any* point you can reconstruct the whole chain —

    domain concept (ontology IRI)
      → code (node ids)
        → deployment (entity_key + version)
          → drift (findings)
            → governed remediation (tasks)
              → cross-run memory (lessons)

``LineageIndex`` ingests the typed artifacts the earlier phases already produce
(confirmed mappings, ``RegistrationRequest``s, ``DriftFinding``s,
``RemediationTask``s) and self-links them on ``ontology_iri`` / ``entity_key`` /
``trace_id``. Query from a code node, an entity, an IRI, or a trace — you get the
same ``LineageRecord``. ``correlation_handles`` yields the keys to pull each
system's telemetry (orchestrator OTel by ``trace_id``, infodrift/ontomesh by
``entity_key`` / IRI) — one correlation across three planes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.spine.drift import DriftFinding
from orchestrator.spine.mapper import MappingLedger
from orchestrator.spine.ontology import OntologyRef
from orchestrator.spine.remediation import RemediationTask
from orchestrator.spine.shipment import RegistrationRequest


@dataclass(frozen=True)
class LineageRecord:
    """The full provenance chain for one deployment unit (entity)."""

    entity_key: str
    ontology_iri: str = ""
    ontology_label: str = ""
    code_node_ids: tuple[str, ...] = ()
    shipments: tuple[dict[str, Any], ...] = ()
    drift: tuple[dict[str, Any], ...] = ()
    remediations: tuple[dict[str, Any], ...] = ()
    memory_refs: tuple[str, ...] = ()

    @property
    def stages_present(self) -> set[str]:
        """Which links of the chain this record has — for completeness checks."""
        present = {"deployment"} if self.entity_key else set()
        if self.ontology_iri:
            present.add("domain")
        if self.code_node_ids:
            present.add("code")
        if self.shipments:
            present.add("shipment")
        if self.drift:
            present.add("drift")
        if self.remediations:
            present.add("remediation")
        if self.memory_refs:
            present.add("memory")
        return present

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_key": self.entity_key,
            "ontology_iri": self.ontology_iri,
            "ontology_label": self.ontology_label,
            "code_node_ids": list(self.code_node_ids),
            "shipments": [dict(s) for s in self.shipments],
            "drift": [dict(d) for d in self.drift],
            "remediations": [dict(r) for r in self.remediations],
            "memory_refs": list(self.memory_refs),
        }


def correlation_handles(record: LineageRecord) -> dict[str, Any]:
    """The keys to pull each system's telemetry for this lineage, all aligned.

    - orchestrator **OpenTelemetry**: join spans by ``trace_id`` (the attribute the
      observability work already stamps);
    - infodrift / ontomesh: join by ``entity_key`` / ``ontology_iri``.
    """
    trace_ids = [s["trace_id"] for s in record.shipments if s.get("trace_id")]
    return {
        "entity_key": record.entity_key,
        "ontology_iri": record.ontology_iri,
        "trace_ids": trace_ids,
        "otel_filter": " or ".join(f'trace_id="{t}"' for t in trace_ids),
        "prometheus_label": f'entity_key="{record.entity_key}"',
    }


class LineageIndex:
    """Accumulates spine artifacts and answers lineage queries from any entry point."""

    def __init__(self) -> None:
        self._node_iri: dict[str, str] = {}
        self._iri_nodes: dict[str, set[str]] = {}
        self._iri_label: dict[str, str] = {}
        self._entity_iri: dict[str, str] = {}
        self._iri_entities: dict[str, set[str]] = {}
        self._shipments: dict[str, list[dict[str, Any]]] = {}
        self._drift: dict[str, list[dict[str, Any]]] = {}
        self._remediations: dict[str, list[dict[str, Any]]] = {}
        self._memory: dict[str, list[str]] = {}
        self._trace_entities: dict[str, set[str]] = {}

    # ---- ingest -----------------------------------------------------------
    def add_mapping(self, node_id: str, ref: OntologyRef) -> None:
        self._node_iri[node_id] = ref.ontology_iri
        self._iri_nodes.setdefault(ref.ontology_iri, set()).add(node_id)
        if ref.label:
            self._iri_label.setdefault(ref.ontology_iri, ref.label)

    def add_mappings(self, ledger: MappingLedger) -> None:
        for node_id, ref in ledger.resolved().items():
            self.add_mapping(node_id, ref)

    def link_entity(self, entity_key: str, ontology_iri: str, *, label: str = "") -> None:
        if not ontology_iri:
            return
        self._entity_iri[entity_key] = ontology_iri
        self._iri_entities.setdefault(ontology_iri, set()).add(entity_key)
        if label:
            self._iri_label.setdefault(ontology_iri, label)

    def add_shipment(self, request: RegistrationRequest) -> None:
        ek = request.entity_key
        self.link_entity(ek, request.ontology_iri)
        self._shipments.setdefault(ek, []).append(
            {
                "version": request.version,
                "baseline_id": request.baseline_id,
                "trace_id": request.trace_id,
                "pr_url": request.pr_url,
            }
        )
        if request.trace_id:
            self._trace_entities.setdefault(request.trace_id, set()).add(ek)

    def add_drift(self, finding: DriftFinding) -> None:
        self._drift.setdefault(finding.entity_key, []).append(
            {
                "metric_type": finding.metric_type,
                "severity": finding.severity,
                "layer": finding.layer,
                "window_id": finding.window_id,
            }
        )

    def add_remediation(self, task: RemediationTask) -> None:
        ek = task.entity_key
        iri = str(task.provenance.get("ontology_iri") or "")
        self.link_entity(ek, iri)
        self._remediations.setdefault(ek, []).append(
            {
                "title": task.title,
                "window_id": task.provenance.get("window_id", ""),
                "code_node_ids": list(task.provenance.get("code_node_ids") or []),
                "is_scoped": task.is_scoped,
            }
        )

    def add_memory(self, entity_key: str, memory_ref: str) -> None:
        self._memory.setdefault(entity_key, []).append(memory_ref)

    # ---- query ------------------------------------------------------------
    def for_entity(self, entity_key: str) -> LineageRecord:
        iri = self._entity_iri.get(entity_key, "")
        return LineageRecord(
            entity_key=entity_key,
            ontology_iri=iri,
            ontology_label=self._iri_label.get(iri, ""),
            code_node_ids=tuple(sorted(self._iri_nodes.get(iri, set()))),
            shipments=tuple(self._shipments.get(entity_key, [])),
            drift=tuple(self._drift.get(entity_key, [])),
            remediations=tuple(self._remediations.get(entity_key, [])),
            memory_refs=tuple(self._memory.get(entity_key, [])),
        )

    def for_iri(self, ontology_iri: str) -> list[LineageRecord]:
        return [self.for_entity(ek) for ek in sorted(self._iri_entities.get(ontology_iri, set()))]

    def for_node(self, node_id: str) -> list[LineageRecord]:
        iri = self._node_iri.get(node_id)
        return self.for_iri(iri) if iri else []

    def for_trace(self, trace_id: str) -> list[LineageRecord]:
        return [self.for_entity(ek) for ek in sorted(self._trace_entities.get(trace_id, set()))]


__all__ = ["LineageIndex", "LineageRecord", "correlation_handles"]
