"""Durable run state: resume instead of restart, one run per ticket, and find the dead ones.

The failures these guard against all happened for real. A crashed run minted a duplicate
ticket because nothing recorded that it had already created one; a killed run left a worktree
and a ticket stuck In Progress with nothing tying them together.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from orchestrator.sdlc.runstate import (
    STALE_AFTER_SECONDS,
    RunRecord,
    RunStore,
    default_state_dir,
    render_reap,
    render_runs,
)


def _store(tmp_path: Path) -> RunStore:
    return RunStore(root=tmp_path / "state")


def test_a_record_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(RunRecord(run_id="abc", source="jira://X-1", issue_key="X-1", started_at=time.time()))

    loaded = store.load("abc")

    assert loaded is not None
    assert (loaded.run_id, loaded.source, loaded.issue_key) == ("abc", "jira://X-1", "X-1")
    assert loaded.heartbeat_at > 0  # stamped on save, so staleness is measurable


def test_an_unknown_or_unreadable_record_reads_as_absent(tmp_path: Path) -> None:
    """A resume must fail with 'no such run', not with a JSON error from a half-written file."""
    store = _store(tmp_path)
    store.root.mkdir(parents=True)
    (store.root / "broken.json").write_text("{ not json", encoding="utf-8")

    assert store.load("missing") is None
    assert store.load("broken") is None


def test_a_write_is_atomic(tmp_path: Path) -> None:
    """A kill mid-save must leave the previous record readable, never a truncated one."""
    store = _store(tmp_path)
    record = RunRecord(run_id="abc", source="s", phase="design")
    store.save(record)
    record.phase = "implement"
    store.save(record)

    assert store.load("abc") is not None
    assert store.load("abc").phase == "implement"  # type: ignore[union-attr]
    assert not list(store.root.glob("*.tmp"))  # no debris left behind


def test_a_ticket_is_held_by_at_most_one_live_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(RunRecord(run_id="live", source="s", issue_key="X-1", status="running", pid=os.getpid()))

    held = store.active_for_issue("X-1")

    assert held is not None and held.run_id == "live"
    assert store.active_for_issue("X-2") is None


def test_a_finished_run_does_not_hold_its_ticket(tmp_path: Path) -> None:
    """Otherwise a ticket could never be worked twice — a fix and its follow-up would collide."""
    store = _store(tmp_path)
    store.save(RunRecord(run_id="old", source="s", issue_key="X-1", status="done"))

    assert store.active_for_issue("X-1") is None


def test_a_parked_run_keeps_holding_its_ticket(tmp_path: Path) -> None:
    """Parked means waiting for a human, not finished — starting a second run would race it."""
    store = _store(tmp_path)
    store.save(RunRecord(run_id="p", source="s", issue_key="X-1", status="parked", pid=999999))

    held = store.active_for_issue("X-1")

    assert held is not None and held.run_id == "p"


def test_a_run_whose_process_is_gone_is_reapable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(
        RunRecord(
            run_id="dead",
            source="s",
            issue_key="X-9",
            status="running",
            worktree="/tmp/ws",
            branch="feat/x",
            pid=999_999,  # not a live pid
        )
    )

    stale = store.stale()

    assert [r.run_id for r in stale] == ["dead"]
    report = render_reap(stale)
    assert "X-9" in report and "/tmp/ws" in report and "feat/x" in report


def test_a_silent_run_goes_stale_even_if_the_pid_is_reused(tmp_path: Path) -> None:
    """A pid is not proof — pids get reused — so the heartbeat is what decides."""
    store = _store(tmp_path)
    record = RunRecord(run_id="quiet", source="s", status="running", pid=os.getpid())
    store.save(record)
    record.heartbeat_at = time.time() - (STALE_AFTER_SECONDS + 1)
    store.save(record)
    # save() re-stamps the heartbeat, so write the aged record directly.
    path = store.path_for("quiet")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f'"heartbeat_at": {store.load("quiet").heartbeat_at}',  # type: ignore[union-attr]
            f'"heartbeat_at": {time.time() - STALE_AFTER_SECONDS - 1}',
        ),
        encoding="utf-8",
    )

    assert [r.run_id for r in store.stale()] == ["quiet"]


def test_the_state_dir_is_outside_the_repo(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("SPINE_RUN_STATE", raising=False)
    assert Path.cwd() not in default_state_dir().parents


def test_the_listing_says_who_is_still_driving(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save(RunRecord(run_id="a", source="s", status="running", pid=os.getpid(), issue_key="X-1"))
    store.save(RunRecord(run_id="b", source="s", status="parked", issue_key="X-2", spent_usd=1.5))

    table = render_runs(store.all())

    assert "X-1" in table and "X-2" in table
    assert "$1.50" in table
    assert render_runs([]) == "No runs recorded."
