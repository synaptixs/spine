"""execute_remediations — drift → governed run (Spine Seam 3 execution, Phase 2 live)."""

from __future__ import annotations

from orchestrator.spine import (
    DriftReport,
    RemediationOutcome,
    RemediationTask,
    execute_remediations,
    infer_entity_iris,
)
from orchestrator.spine.drift import DriftFinding
from orchestrator.spine.ontology import OntologyRef

EK = "FraudDetector_v5::APAC::CardTransactions"
IRI = "ex:FraudDetector"


def _report(severity: str = "critical") -> DriftReport:
    return DriftReport(
        findings=(
            DriftFinding(
                entity_key=EK,
                severity=severity,
                metric_type="ece",
                message="calibration eroded",
                window_id="w1",
            ),
        )
    )


def _resolved() -> dict[str, OntologyRef]:
    return {"py:app.fraud.FraudDetector": OntologyRef(IRI, "Fraud Detector", 0.9, "exact")}


def test_infer_entity_iris_from_mappings() -> None:
    # entity component "FraudDetector" matches mapped label "Fraud Detector" → IRI
    assert infer_entity_iris(_report(), _resolved()) == {EK: IRI}


def test_infer_skips_unparseable_entities() -> None:
    rep = DriftReport(
        findings=(DriftFinding(entity_key="not-a-key", severity="critical", metric_type="ece", message="x"),)
    )
    assert infer_entity_iris(rep, _resolved()) == {}


async def test_execute_runs_each_task_via_runner() -> None:
    ran: list[RemediationTask] = []

    async def runner(task: RemediationTask) -> str:
        ran.append(task)
        return task.entity_key + "@branch"

    outcomes = await execute_remediations(
        _report(),
        runner=runner,
        entity_iris={EK: IRI},
        code_for_iri={IRI: ["py:app.fraud.FraudDetector"]},
    )
    assert len(ran) == 1 and ran[0].entity_key == EK
    assert ran[0].is_scoped  # scoped to the mapped code
    assert outcomes == [RemediationOutcome(EK, ran[0].title, ok=True, detail="ran", result=f"{EK}@branch")]


async def test_execute_is_best_effort_per_task() -> None:
    async def boom(task: RemediationTask) -> str:
        raise RuntimeError("codegen exploded")

    outcomes = await execute_remediations(
        _report(),
        runner=boom,
        entity_iris={EK: IRI},
        code_for_iri={},
    )
    assert len(outcomes) == 1 and outcomes[0].ok is False
    assert "exploded" in outcomes[0].detail


async def test_execute_respects_min_severity() -> None:
    ran: list[RemediationTask] = []

    async def runner(task: RemediationTask) -> None:
        ran.append(task)

    outcomes = await execute_remediations(
        _report(severity="warning"),
        runner=runner,
        entity_iris={EK: IRI},
        code_for_iri={},
        min_severity="critical",
    )
    assert outcomes == [] and ran == []
