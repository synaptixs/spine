"""The Drift Report contract — production feedback, normalized (Phase 2, Seam 3).

Adapts infodrift's ``HealthReporter.full_report(as_json=True)`` — a per-entity
dump of L1/L2/L3 monitors — into a small, stable set of ``DriftFinding``s keyed by
the **entity key**. The alerts infodrift already raises (``severity``,
``metric_type``, ``message``, ``recommendation``, ``observed``/``threshold``) are
the material signal; we normalize them and gate on severity so only findings worth
acting on flow downstream.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# infodrift severities, ranked. Materiality gates on this order.
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}

# Map infodrift metric_type → the drift layer (L1 evidence / L2 reliability /
# L3 effectiveness), for human-readable provenance. Unknown → "".
_LAYER = {
    "psi": "L1",
    "divergence": "L1",
    "log_template": "L1",
    "log_templates": "L1",
    "prf": "L2",
    "per_class_prf": "L2",
    "recall_drift": "L2",
    "recall": "L2",
    "ece": "L2",
    "calibration": "L2",
    "brier": "L2",
    "tpr_fpr": "L3",
    "ttd": "L3",
}


def _rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity.lower(), 0)


@dataclass(frozen=True)
class DriftFinding:
    """One normalized drift signal on one deployment unit."""

    entity_key: str
    severity: str
    metric_type: str
    message: str
    recommendation: str = ""
    feature: str | None = None
    observed: float | None = None
    threshold: float | None = None
    window_id: str = ""
    model_version: str | None = None

    @property
    def layer(self) -> str:
        return _LAYER.get(self.metric_type.lower(), "")

    @property
    def is_material(self) -> bool:
        return _rank(self.severity) >= _SEVERITY_RANK["warning"]


@dataclass(frozen=True)
class DriftReport:
    """All findings from one infodrift report, across entities."""

    findings: tuple[DriftFinding, ...] = field(default_factory=tuple)

    def material(self, *, min_severity: str = "warning") -> list[DriftFinding]:
        floor = _rank(min_severity)
        return [f for f in self.findings if _rank(f.severity) >= floor]

    def by_entity(self, *, min_severity: str = "warning") -> dict[str, list[DriftFinding]]:
        grouped: dict[str, list[DriftFinding]] = {}
        for f in self.material(min_severity=min_severity):
            grouped.setdefault(f.entity_key, []).append(f)
        return grouped

    @classmethod
    def from_infodrift(cls, payload: Mapping[str, Any]) -> DriftReport:
        """Parse infodrift's ``full_report`` dict into normalized findings.

        Robust to missing pieces — an entity with no alerts contributes nothing.
        """
        findings: list[DriftFinding] = []
        for entity_key, ent in (payload.get("entities") or {}).items():
            report = ent.get("report") or {}
            window_id = str(report.get("window_id", ""))
            model_version = ent.get("model_version")
            for alert in report.get("alerts") or []:
                findings.append(
                    DriftFinding(
                        entity_key=str(alert.get("entity_key") or entity_key),
                        severity=str(alert.get("severity", "warning")),
                        metric_type=str(alert.get("metric_type", "")),
                        message=str(alert.get("message", "")),
                        recommendation=str(alert.get("recommendation", "")),
                        feature=alert.get("feature"),
                        observed=_as_float(alert.get("observed")),
                        threshold=_as_float(alert.get("threshold")),
                        window_id=str(alert.get("window_id") or window_id),
                        model_version=alert.get("model_version") or model_version,
                    )
                )
        return cls(findings=tuple(findings))


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["DriftFinding", "DriftReport"]
