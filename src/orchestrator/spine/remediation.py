"""Drift → governed remediation — the headline of Seam 3 (Phase 2).

Turns material drift findings into **RemediationTask**s: each is a concrete,
*scoped*, *guardrailed*, *provenance-carrying* request the orchestrator can run as a
governed (human-gated) build — not a silent change. This is the teeth the critique
asked for: drift doesn't just leave a memory, it becomes a reviewed fix.

What this module builds is the *task* (spec + guardrails + scope + provenance).
Executing it (handing the task to a governed orchestrator run) is the live wiring
step, kept separate so this stays deterministic and testable.

The scope is the spine in action: a finding's ``entity_key`` → its ontology IRI →
the code nodes confirmed-mapped to that IRI (Phase 0). The fix is bounded to that
code; the provenance records the whole chain.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from orchestrator.spine.drift import DriftFinding, DriftReport
from orchestrator.spine.mapper import MappingLedger


@dataclass(frozen=True)
class RemediationTask:
    """A governed, scoped remediation request derived from drift.

    ``spec`` is an orchestrator codegen spec (title / summary / acceptance_criteria).
    ``guardrails`` are the constraints the governed run must honor (ontology/SHACL
    constraints + human-gate + scope-limit). ``provenance`` is the lineage:
    entity_key → window → ontology IRI → code node ids → the findings that triggered it.
    """

    entity_key: str
    title: str
    spec: dict[str, Any]
    guardrails: tuple[str, ...] = field(default_factory=tuple)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def code_node_ids(self) -> list[str]:
        return list(self.provenance.get("code_node_ids") or [])

    @property
    def is_scoped(self) -> bool:
        """True when the fix is bounded to known code (the spine resolved)."""
        return bool(self.code_node_ids)


def code_for_iri_from_ledger(ledger: MappingLedger) -> dict[str, list[str]]:
    """Invert confirmed mappings into ``ontology_iri -> [node_id]`` (the code scope)."""
    out: dict[str, list[str]] = {}
    for node_id, ref in ledger.resolved().items():
        out.setdefault(ref.ontology_iri, []).append(node_id)
    return out


def _build_spec(entity_key: str, findings: list[DriftFinding]) -> dict[str, Any]:
    window = findings[0].window_id if findings else ""
    criteria: list[str] = []
    for f in findings:
        layer = f" [{f.layer}]" if f.layer else ""
        criteria.append(
            f.recommendation or f"Address {f.metric_type}{layer} drift on {entity_key}: {f.message}"
        )
    criteria.append("Keep changes scoped to the code mapped to this entity; do not broaden beyond it.")
    return {
        "title": f"Remediate drift: {entity_key}",
        "summary": (
            f"{len(findings)} drift finding(s) on {entity_key} (window {window}). "
            "Strengthen the affected behavior and/or its monitoring/guardrails."
        ),
        "acceptance_criteria": criteria,
        "entity_key": entity_key,
    }


def plan_remediations(
    report: DriftReport,
    *,
    entity_iris: Mapping[str, str],
    code_for_iri: Mapping[str, list[str]],
    guardrails_for_iri: Mapping[str, list[str]] | None = None,
    min_severity: str = "warning",
) -> list[RemediationTask]:
    """Material drift → one governed ``RemediationTask`` per affected entity.

    - ``entity_iris``: ``entity_key -> ontology_iri`` (ontomesh-minted in production).
    - ``code_for_iri``: ``ontology_iri -> [node_id]`` (from confirmed Phase-0 mappings;
      see ``code_for_iri_from_ledger``).
    - ``guardrails_for_iri``: ontology/SHACL constraints per class (optional).

    A finding whose entity has no resolved code scope still produces a task (never
    silently dropped) — flagged ``is_scoped == False`` with a guardrail to map it first.
    """
    guardrails_for_iri = guardrails_for_iri or {}
    tasks: list[RemediationTask] = []
    for entity_key, findings in report.by_entity(min_severity=min_severity).items():
        iri = entity_iris.get(entity_key, "")
        code_node_ids = list(code_for_iri.get(iri, [])) if iri else []

        guardrails = list(guardrails_for_iri.get(iri, []))
        guardrails.append("Human approval required before merge (governed run).")
        if code_node_ids:
            guardrails.append(f"Limit changes to: {', '.join(code_node_ids)}.")
        else:
            guardrails.append(
                "Code scope unresolved — confirm a code↔ontology mapping for this entity first."
            )

        provenance = {
            "entity_key": entity_key,
            "window_id": findings[0].window_id if findings else "",
            "ontology_iri": iri,
            "code_node_ids": code_node_ids,
            "findings": [
                {
                    "metric_type": f.metric_type,
                    "severity": f.severity,
                    "layer": f.layer,
                    "observed": f.observed,
                    "threshold": f.threshold,
                }
                for f in findings
            ],
        }
        tasks.append(
            RemediationTask(
                entity_key=entity_key,
                title=f"Remediate drift: {entity_key}",
                spec=_build_spec(entity_key, findings),
                guardrails=tuple(guardrails),
                provenance=provenance,
            )
        )
    return tasks


__all__ = ["RemediationTask", "code_for_iri_from_ledger", "plan_remediations"]
