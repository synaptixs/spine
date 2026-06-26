"""Calibration history repository + aggregation.

Sprint 11.6. Records one row per terminal verifier outcome and aggregates
per ``(template_id, template_version)`` so the planner can rank candidates
by historical calibration:

- ``sample_count``       — how many runs we have
- ``pass_rate``          — fraction of runs whose terminal verifier passed
- ``mean_confidence``    — average claimed confidence across runs
- ``calibration_gap``    — mean_confidence − pass_rate, signed.
  Positive = the agent over-states; negative = under-states.

The planner consumes a ``dict[(template_id, version), CalibrationStats]``
and threads the per-candidate stats into its catalogue prompt.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.registry.db.models import CalibrationHistoryRow


@dataclass(frozen=True)
class CalibrationStats:
    template_id: str
    template_version: str
    sample_count: int
    pass_rate: float
    mean_confidence: float

    @property
    def calibration_gap(self) -> float:
        return round(self.mean_confidence - self.pass_rate, 4)

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "template_id": self.template_id,
            "template_version": self.template_version,
            "sample_count": self.sample_count,
            "pass_rate": round(self.pass_rate, 4),
            "mean_confidence": round(self.mean_confidence, 4),
            "calibration_gap": self.calibration_gap,
        }


class CalibrationHistoryRepo:
    """Async repository for calibration_history rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        template_id: str,
        template_version: str,
        task_id: str,
        claimed_confidence: float,
        verifier_outcome: str,
        trace_id: str | None = None,
    ) -> None:
        row = CalibrationHistoryRow(
            template_id=template_id,
            template_version=template_version,
            task_id=task_id,
            claimed_confidence=float(claimed_confidence),
            verifier_outcome=verifier_outcome,
            trace_id=trace_id,
        )
        self._session.add(row)
        await self._session.flush()

    async def stats_for(self, template_id: str, template_version: str) -> CalibrationStats | None:
        is_pass = case((CalibrationHistoryRow.verifier_outcome == "pass", 1), else_=0)
        stmt = (
            select(
                func.count(CalibrationHistoryRow.pk).label("n"),
                func.avg(CalibrationHistoryRow.claimed_confidence).label("mean_conf"),
                func.sum(is_pass.cast(Integer)).label("passes"),
            )
            .where(CalibrationHistoryRow.template_id == template_id)
            .where(CalibrationHistoryRow.template_version == template_version)
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None or not row.n:
            return None
        mean_conf = float(row.mean_conf or 0.0)
        pass_rate = float(row.passes or 0) / float(row.n)
        return CalibrationStats(
            template_id=template_id,
            template_version=template_version,
            sample_count=int(row.n),
            pass_rate=pass_rate,
            mean_confidence=mean_conf,
        )

    async def stats_for_candidates(
        self, candidates: list[tuple[str, str]]
    ) -> dict[tuple[str, str], CalibrationStats]:
        """Bulk-fetch stats for every (template_id, version) we're considering."""
        if not candidates:
            return {}
        out: dict[tuple[str, str], CalibrationStats] = {}
        # One query per candidate keeps the logic simple; the candidate set is
        # small (rarely > 10) and these reads aren't on the hot path.
        for template_id, template_version in candidates:
            stats = await self.stats_for(template_id, template_version)
            if stats is not None:
                out[(template_id, template_version)] = stats
        return out
