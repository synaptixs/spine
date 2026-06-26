"""Integration tests for the calibration history repo + planner integration."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.registry.calibration import CalibrationHistoryRepo

pytestmark = pytest.mark.integration


async def test_no_history_returns_none(session: AsyncSession) -> None:
    repo = CalibrationHistoryRepo(session)
    assert await repo.stats_for("agent.x", "0.1.0") is None


async def test_records_and_aggregates(session: AsyncSession) -> None:
    repo = CalibrationHistoryRepo(session)
    rows = [
        ("t1", 0.9, "pass"),
        ("t2", 0.8, "pass"),
        ("t3", 0.7, "fail"),
        ("t4", 0.85, "warn"),
        ("t5", 0.95, "pass"),
    ]
    for task_id, conf, outcome in rows:
        await repo.record(
            template_id="agent.research",
            template_version="0.1.0",
            task_id=task_id,
            claimed_confidence=conf,
            verifier_outcome=outcome,
        )
    await session.commit()

    stats = await repo.stats_for("agent.research", "0.1.0")
    assert stats is not None
    assert stats.sample_count == 5
    # 3 of 5 passed.
    assert stats.pass_rate == pytest.approx(0.6)
    # Mean of (0.9, 0.8, 0.7, 0.85, 0.95) = 0.84.
    assert stats.mean_confidence == pytest.approx(0.84, abs=1e-3)
    # Calibration gap: 0.84 - 0.6 = +0.24 (agent over-states by 24 pts).
    assert stats.calibration_gap == pytest.approx(0.24, abs=1e-3)


async def test_bulk_fetch_returns_only_known_candidates(session: AsyncSession) -> None:
    repo = CalibrationHistoryRepo(session)
    await repo.record(
        template_id="agent.a",
        template_version="0.1.0",
        task_id="t1",
        claimed_confidence=0.8,
        verifier_outcome="pass",
    )
    await session.commit()
    stats = await repo.stats_for_candidates([("agent.a", "0.1.0"), ("agent.never_seen", "0.1.0")])
    assert set(stats) == {("agent.a", "0.1.0")}
