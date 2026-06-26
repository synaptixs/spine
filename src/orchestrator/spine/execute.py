"""Execute remediations — drift → governed run (Seam 3 execution, Phase 2 → live).

Closes the loop: take a drift report, plan scoped remediation tasks (Phase 2), and
run each one. The *runner* is injected — this module stays free of any orchestrator
import, so it's pure orchestration logic (testable with a fake runner). The
sdlc/CLI side supplies the real runner that launches a governed
``run_feature(spec=task.spec, …)`` (intake-skipped) build.

``infer_entity_iris`` derives ``entity_key → ontology_iri`` from the persisted
mappings, so the planner can scope each task to the right code with no extra config.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from orchestrator.spine.drift import DriftReport
from orchestrator.spine.entity_key import EntityKey
from orchestrator.spine.ontology import OntologyRef
from orchestrator.spine.remediation import RemediationTask, plan_remediations

# A runner takes one planned task and launches the (governed) build for it.
RemediationRunner = Callable[[RemediationTask], Awaitable[Any]]


@dataclass(frozen=True)
class RemediationOutcome:
    """Result of attempting one remediation."""

    entity_key: str
    title: str
    ok: bool
    detail: str = ""
    result: Any = None


def infer_entity_iris(report: DriftReport, resolved: Mapping[str, OntologyRef]) -> dict[str, str]:
    """Derive ``entity_key → ontology_iri`` by matching the entity's component to a
    mapped ontology label (spaces stripped), e.g. ``FraudDetector`` ↔ "Fraud Detector".
    """
    label_to_iri = {ref.label.replace(" ", ""): ref.ontology_iri for ref in resolved.values() if ref.label}
    out: dict[str, str] = {}
    for entity_key in {f.entity_key for f in report.findings}:
        if not EntityKey.is_valid(entity_key):
            continue
        component = EntityKey.parse(entity_key).component
        iri = label_to_iri.get(component)
        if iri:
            out[entity_key] = iri
    return out


async def execute_remediations(
    report: DriftReport,
    *,
    runner: RemediationRunner,
    entity_iris: Mapping[str, str],
    code_for_iri: Mapping[str, list[str]],
    guardrails_for_iri: Mapping[str, list[str]] | None = None,
    min_severity: str = "warning",
) -> list[RemediationOutcome]:
    """Plan remediations for ``report`` and run each via ``runner``.

    Best-effort per task — one failing run doesn't stop the others; the failure is
    captured in its ``RemediationOutcome``. The planning (scope + guardrails +
    provenance) is Phase 2; this adds the *execution*.
    """
    tasks = plan_remediations(
        report,
        entity_iris=entity_iris,
        code_for_iri=code_for_iri,
        guardrails_for_iri=guardrails_for_iri,
        min_severity=min_severity,
    )
    outcomes: list[RemediationOutcome] = []
    for task in tasks:
        try:
            result = await runner(task)
            outcomes.append(
                RemediationOutcome(task.entity_key, task.title, ok=True, detail="ran", result=result)
            )
        except Exception as exc:  # noqa: BLE001 — one bad run must not sink the batch
            outcomes.append(RemediationOutcome(task.entity_key, task.title, ok=False, detail=str(exc)[:200]))
    return outcomes


__all__ = ["RemediationOutcome", "RemediationRunner", "execute_remediations", "infer_entity_iris"]
