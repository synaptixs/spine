"""Persistent intake cache: round-trip + extract-once-then-reuse semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.intake.cache import (
    analyze_cached,
    cache_path,
    complete_by_pr,
    load_cached_plan,
    load_progress,
    save_plan,
    set_progress,
)
from orchestrator.intake.gaps import GapFinding, GapSeverity
from orchestrator.intake.intents import Intent
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import FeatureSpec

_SOURCE = "confluence://1234567890"


def _plan() -> BacklogPlan:
    return BacklogPlan(
        documents=[SourceDocument(id="1234567890", title="Reqs", body="text", labels=("a", "b"))],
        intents=[Intent(id="intent-x", title="X", description="do x", acceptance_criteria=["c1"])],
        gaps=[
            GapFinding(
                rule_id="nfrs_missing", intent_id="intent-x", severity=GapSeverity.WARNING, message="m"
            )
        ],
        specs=[FeatureSpec(intent_id="intent-x", title="X", acceptance_criteria=["c1"])],
        blocked=False,
        truncated=False,
    )


class _CountingService:
    """Stands in for BacklogService; counts how often analyze() is invoked."""

    def __init__(self, plan: BacklogPlan) -> None:
        self._plan = plan
        self.calls = 0

    async def analyze(self, root_id: str) -> BacklogPlan:
        self.calls += 1
        return self._plan


def test_round_trip_preserves_the_plan(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(), tmp_path)
    loaded = load_cached_plan(_SOURCE, tmp_path)
    assert loaded is not None
    assert [i.id for i in loaded.intents] == ["intent-x"]
    assert [s.intent_id for s in loaded.specs] == ["intent-x"]
    assert loaded.gaps[0].severity is GapSeverity.WARNING  # enum survives, not bare str
    assert loaded.documents[0].labels == ("a", "b")  # tuple restored


def test_miss_returns_none(tmp_path: Path) -> None:
    assert load_cached_plan("confluence://nope", tmp_path) is None


def test_version_mismatch_is_ignored(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(), tmp_path)
    p = cache_path(_SOURCE, tmp_path)
    p.write_text(p.read_text().replace('"version": 1', '"version": 999'))
    assert load_cached_plan(_SOURCE, tmp_path) is None


async def test_analyze_cached_extracts_once_then_reuses(tmp_path: Path) -> None:
    svc = _CountingService(_plan())
    first = await analyze_cached(svc, _SOURCE, cache_dir=tmp_path)  # type: ignore[arg-type]
    second = await analyze_cached(svc, _SOURCE, cache_dir=tmp_path)  # type: ignore[arg-type]
    assert svc.calls == 1  # second run served from cache — no re-extract
    assert [i.id for i in first.intents] == [i.id for i in second.intents]


async def test_refresh_forces_reextract(tmp_path: Path) -> None:
    svc = _CountingService(_plan())
    await analyze_cached(svc, _SOURCE, cache_dir=tmp_path)  # type: ignore[arg-type]
    await analyze_cached(svc, _SOURCE, cache_dir=tmp_path, refresh=True)  # type: ignore[arg-type]
    assert svc.calls == 2  # --refresh re-extracts even with a warm cache


def test_cache_path_honors_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_CACHE_DIR", str(tmp_path / "custom"))
    assert cache_path(_SOURCE).parent == tmp_path / "custom"


def test_set_progress_round_trips(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(), tmp_path)
    set_progress(
        _SOURCE,
        "intent-x",
        status="in_progress",
        issue_key="PROJ-9",
        pr_url="http://pr/9",
        cache_dir=tmp_path,
    )
    prog = load_progress(_SOURCE, tmp_path)
    assert prog["intent-x"] == {"status": "in_progress", "issue_key": "PROJ-9", "pr_url": "http://pr/9"}


def test_set_progress_noop_without_cache(tmp_path: Path) -> None:
    set_progress(_SOURCE, "intent-x", status="done", cache_dir=tmp_path)  # no cache file yet
    assert load_progress(_SOURCE, tmp_path) == {}


def test_refresh_preserves_progress_for_surviving_intents(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(), tmp_path)
    set_progress(_SOURCE, "intent-x", status="done", issue_key="PROJ-1", cache_dir=tmp_path)
    # a re-extract (same deterministic intent) must keep the done marker
    save_plan(_SOURCE, _plan(), tmp_path)
    assert load_progress(_SOURCE, tmp_path)["intent-x"]["status"] == "done"


def test_refresh_drops_progress_for_vanished_intents(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(), tmp_path)
    set_progress(_SOURCE, "intent-x", status="done", cache_dir=tmp_path)
    # re-extract yields a different intent set — stale progress is dropped
    empty = BacklogPlan(intents=[Intent(id="intent-other", title="Other")])
    save_plan(_SOURCE, empty, tmp_path)
    assert "intent-x" not in load_progress(_SOURCE, tmp_path)


def test_complete_by_pr_marks_done(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(), tmp_path)
    set_progress(_SOURCE, "intent-x", status="in_progress", pr_url="http://pr/42", cache_dir=tmp_path)
    matched = complete_by_pr("http://pr/42", tmp_path)
    assert matched is not None
    source, plan = matched
    assert source == _SOURCE and [i.id for i in plan.intents] == ["intent-x"]
    assert load_progress(_SOURCE, tmp_path)["intent-x"]["status"] == "done"


def test_complete_by_pr_unmatched_returns_none(tmp_path: Path) -> None:
    save_plan(_SOURCE, _plan(), tmp_path)
    assert complete_by_pr("http://pr/nope", tmp_path) is None
