"""DriftReport — parse + gate infodrift findings (Spine Phase 2, Seam 3)."""

from __future__ import annotations

from orchestrator.spine import DriftReport

# Shaped like infodrift's HealthReporter.full_report(as_json=False).
_PAYLOAD = {
    "entities": {
        "FraudDetector_v5::APAC::CardTransactions": {
            "model_version": "5",
            "baseline_id": "base@register",
            "report": {
                "window_id": "w-2026-06-24",
                "alerts": [
                    {
                        "severity": "critical",
                        "metric_type": "ece",
                        "entity_key": "FraudDetector_v5::APAC::CardTransactions",
                        "observed": 0.21,
                        "threshold": 0.07,
                        "message": "calibration eroded (ECE 3x baseline)",
                        "recommendation": "recalibrate scores; add a calibration monitor",
                    },
                    {
                        "severity": "warning",
                        "metric_type": "psi",
                        "feature": "amount",
                        "observed": 0.18,
                        "threshold": 0.1,
                        "message": "feature 'amount' distribution shifted",
                        "recommendation": "review amount preprocessing",
                    },
                ],
            },
        },
        "Quiet_v1::EU::Batch": {
            "model_version": "1",
            "report": {"window_id": "w-2026-06-24", "alerts": []},  # healthy → nothing
        },
    }
}


def test_parse_extracts_findings_with_context() -> None:
    report = DriftReport.from_infodrift(_PAYLOAD)
    assert len(report.findings) == 2
    ece = next(f for f in report.findings if f.metric_type == "ece")
    assert ece.entity_key == "FraudDetector_v5::APAC::CardTransactions"
    assert ece.severity == "critical" and ece.layer == "L2"
    assert ece.observed == 0.21 and ece.threshold == 0.07
    assert ece.window_id == "w-2026-06-24" and ece.model_version == "5"
    psi = next(f for f in report.findings if f.metric_type == "psi")
    assert psi.feature == "amount" and psi.layer == "L1"


def test_healthy_entity_contributes_nothing() -> None:
    report = DriftReport.from_infodrift(_PAYLOAD)
    assert all(f.entity_key != "Quiet_v1::EU::Batch" for f in report.findings)


def test_materiality_gate() -> None:
    report = DriftReport.from_infodrift(_PAYLOAD)
    assert len(report.material(min_severity="warning")) == 2
    assert len(report.material(min_severity="critical")) == 1
    grouped = report.by_entity(min_severity="critical")
    assert list(grouped) == ["FraudDetector_v5::APAC::CardTransactions"]
    assert len(grouped["FraudDetector_v5::APAC::CardTransactions"]) == 1


def test_empty_payload_is_safe() -> None:
    assert DriftReport.from_infodrift({}).findings == ()
