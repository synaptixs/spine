"""Where runs, approvals and run artifacts live (SSPN-407, G4 D125).

They lived under ``$TMPDIR/spine-runs``, which macOS clears on a restart: the run, its park and every
test log went with it. The default is now the user's state directory. The two overrides still win, and a
run left under the old base is still read, never moved.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.sdlc import editor_builds
from orchestrator.sdlc.autorun import default_artifacts_dir
from orchestrator.sdlc.escalate import (
    Approval,
    ApprovalStore,
    UndeliverableDecisionError,
    decide,
    default_approval_dir,
)
from orchestrator.sdlc.run_base import default_run_base, legacy_run_base
from orchestrator.sdlc.runstate import RunRecord, RunStore, default_state_dir


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No override set, a home and a temp dir of the test's own."""
    for name in ("SPINE_RUN_STATE", "SPINE_RUN_ARTIFACTS", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    return home


def test_the_default_survives_a_restart_it_is_not_the_temp_dir(home: Path) -> None:
    base = default_run_base()
    assert base == home / ".local" / "state" / "spine" / "runs"
    assert Path(tempfile.gettempdir()) not in base.parents
    assert default_state_dir() == base / "_state"
    assert default_approval_dir() == base / "_approvals"
    assert default_artifacts_dir("abc123") == base / "abc123"


def test_xdg_state_home_is_honoured(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_run_base() == tmp_path / "state" / "spine" / "runs"


def test_the_overrides_still_win(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_RUN_STATE", str(tmp_path / "s"))
    monkeypatch.setenv("SPINE_RUN_ARTIFACTS", str(tmp_path / "a"))
    assert default_state_dir() == tmp_path / "s" / "_state"
    assert default_approval_dir() == tmp_path / "s" / "_approvals"
    assert default_artifacts_dir("abc123") == tmp_path / "a" / "abc123"
    assert legacy_run_base() is None, "an explicit store reads nothing else"


def test_a_run_under_the_old_temp_base_is_still_read_and_new_writes_go_to_the_new_base(home: Path) -> None:
    old = Path(tempfile.gettempdir()) / "spine-runs"
    RunStore(root=old / "_state").save(RunRecord(run_id="old1", source="s", status="parked"))
    approval = Approval(approval_id="ap1", run_id="old1", issue_key="", title="t", reason="r")
    ApprovalStore(root=old / "_approvals").save(approval)
    assert legacy_run_base() == old

    runs = RunStore()
    assert runs.load("old1") is not None
    assert [r.run_id for r in runs.all()] == ["old1"]
    approvals = ApprovalStore(root=default_approval_dir())
    assert approvals.load("ap1") is not None and [a.approval_id for a in approvals.all()] == ["ap1"]

    record = runs.load("old1")
    assert record is not None
    record.status = "abandoned"
    runs.save(record)
    assert (default_state_dir() / "old1.json").is_file(), "written to the new base"
    assert [(r.run_id, r.status) for r in RunStore().all()] == [("old1", "abandoned")], "the new copy wins"


def test_a_store_given_its_own_root_reads_nothing_else(home: Path, tmp_path: Path) -> None:
    old = Path(tempfile.gettempdir()) / "spine-runs"
    RunStore(root=old / "_state").save(RunRecord(run_id="old1", source="s"))
    assert RunStore(root=tmp_path / "mine").all() == []
    assert ApprovalStore(root=tmp_path / "mine-a").all() == []


# ---- a decision an older Spine waits on cannot reach it (SSPN-407 review) ---------------------------


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _old_park(repo: Path, *, pid: int) -> Path:
    """A run of an older Spine parked under the old temp base, with the approval it polls for there."""
    old = Path(tempfile.gettempdir()) / "spine-runs"
    RunStore(root=old / "_state").save(
        RunRecord(run_id="old1", source="s", status="parked", pid=pid, repo=str(repo))
    )
    ApprovalStore(root=old / "_approvals").save(
        Approval(approval_id="ap1", run_id="old1", issue_key="", title="t", reason="r", kind="budget")
    )
    return old


def test_a_decision_an_older_live_spine_waits_on_is_refused_not_lost(home: Path, tmp_path: Path) -> None:
    """The older process polls the old base; a decision saved to the new one would never reach it."""
    old = _old_park(tmp_path, pid=os.getpid())

    with pytest.raises(UndeliverableDecisionError, match="older Spine") as refused:
        decide("ap1", approved=True, store=ApprovalStore(root=default_approval_dir()), by="Pat Dev")

    assert "wait for it to stop" in str(refused.value)
    assert not (default_approval_dir() / "ap1.json").exists(), "nothing written to the new base"
    left = ApprovalStore(root=old / "_approvals").load("ap1")
    assert left is not None and left.pending


def test_a_decision_on_an_old_approval_whose_run_stopped_is_recorded(home: Path, tmp_path: Path) -> None:
    _old_park(tmp_path, pid=_dead_pid())

    decided = decide("ap1", approved=True, store=ApprovalStore(root=default_approval_dir()), by="Pat Dev")

    assert decided.decision == "APPROVED"
    assert (default_approval_dir() / "ap1.json").is_file()


def test_the_editor_and_the_cli_say_why_instead_of_failing(home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _old_park(repo, pid=os.getpid())

    out = editor_builds.decide_build(
        repo, "old1", "ap1", approve=True, expected_digest="", note="", by="Pat Dev"
    )
    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "ap1", "--by", "Pat Dev"])

    assert "older Spine" in out["error"], out
    assert result.exit_code == 2 and "older Spine" in result.output, result.output


def test_cancel_still_rejects_what_an_older_spines_run_waited_on(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel ends the process first, so nobody is left to read the rejection: it is not refused."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _old_park(repo, pid=os.getpid())
    monkeypatch.setattr(editor_builds, "_stop", lambda _pid: None)

    out = editor_builds.cancel_build(repo, "old1", by="Pat Dev")

    assert out["status"] == "abandoned", out
    decided = ApprovalStore(root=default_approval_dir()).load("ap1")
    assert decided is not None and decided.decision == "REJECTED"
