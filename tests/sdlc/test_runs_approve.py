"""`sdlc runs approve` records who said yes (SSPN-55, B12).

The approval used to say `decided_by: "cli"` whoever ran the command, so a PR that went out over
a refused ticket could not say who had decided that.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.sdlc.escalate import Approval, ApprovalStore
from orchestrator.sdlc.runstate import RunRecord, RunStore


@pytest.fixture
def parked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ApprovalStore:
    monkeypatch.setenv("SPINE_RUN_STATE", str(tmp_path / "runs"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    store = ApprovalStore(root=tmp_path / "runs" / "_approvals")
    store.save(Approval(approval_id="a1", run_id="r1", issue_key="", title="t", reason="r", raised_at=1))
    return store


def _git_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str | None) -> None:
    config = tmp_path / "gitconfig"
    config.write_text(f"[user]\n\tname = {name}\n" if name else "", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.chdir(tmp_path)


def test_the_decider_defaults_to_the_git_user(
    parked: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _git_name(monkeypatch, tmp_path, "Alice Example")

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "a1", "--note", "reviewed"])

    assert result.exit_code == 0, result.output
    decided = parked.load("a1")
    assert decided is not None and decided.decided_by == "Alice Example" and decided.note == "reviewed"


def test_by_overrides_the_git_user(
    parked: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _git_name(monkeypatch, tmp_path, "Alice Example")

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "a1", "--by", "bob"])

    assert result.exit_code == 0, result.output
    decided = parked.load("a1")
    assert decided is not None and decided.decided_by == "bob"


def test_with_no_name_to_record_it_refuses_rather_than_writing_cli(
    parked: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _git_name(monkeypatch, tmp_path, None)

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "a1"])

    assert result.exit_code == 2
    decided = parked.load("a1")
    assert decided is not None and decided.pending  # nothing was recorded


# ---- SSPN-228 (P4a): the answer to "what should happen to the test you wrote?" is a choice ----------


@pytest.fixture
def own_test_question(parked: ApprovalStore) -> ApprovalStore:
    parked.save(
        Approval(
            approval_id="q1",
            run_id="r1",
            issue_key="",
            title="own tests",
            reason="r",
            raised_at=2,
            kind="own_tests",
        )
    )
    return parked


@pytest.mark.parametrize(
    ("choice", "decision"),
    [("delete", "APPROVED"), ("suggest", "APPROVED"), ("release", "APPROVED"), ("stop", "REJECTED")],
)
def test_a_choice_is_recorded_beside_the_decision(
    own_test_question: ApprovalStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    choice: str,
    decision: str,
) -> None:
    _git_name(monkeypatch, tmp_path, "Alice Example")

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "q1", "--choice", choice])

    assert result.exit_code == 0, result.output
    decided = own_test_question.load("q1")
    assert decided is not None and decided.choice == choice and decided.decision == decision
    assert decided.decided_by == "Alice Example"


def test_explain_carries_the_persons_words_and_needs_them(
    own_test_question: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _git_name(monkeypatch, tmp_path, "Alice Example")

    bare = CliRunner().invoke(app, ["sdlc", "runs", "approve", "q1", "--choice", "explain"])
    assert bare.exit_code == 2
    still = own_test_question.load("q1")
    assert still is not None and still.pending

    said = CliRunner().invoke(
        app, ["sdlc", "runs", "approve", "q1", "--choice", "explain", "--note", "use unittest, not pytest"]
    )
    assert said.exit_code == 0, said.output
    decided = own_test_question.load("q1")
    assert decided is not None and decided.choice == "explain" and decided.note == "use unittest, not pytest"


def test_an_unknown_choice_is_refused_and_nothing_is_recorded(
    own_test_question: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _git_name(monkeypatch, tmp_path, "Alice Example")

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "q1", "--choice", "frobnicate"])

    assert result.exit_code == 2
    still = own_test_question.load("q1")
    assert still is not None and still.pending and still.choice == ""


def test_a_choice_is_only_for_the_own_test_question(
    parked: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The validity and design gates are yes/no; a choice on them would be silently ignored."""
    _git_name(monkeypatch, tmp_path, "Alice Example")

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "a1", "--choice", "delete"])

    assert result.exit_code == 2
    still = parked.load("a1")
    assert still is not None and still.pending


def test_an_approval_saved_before_choices_existed_still_loads(tmp_path: Path) -> None:
    store = ApprovalStore(root=tmp_path / "approvals")
    store.root.mkdir(parents=True)
    (store.root / "old.json").write_text(
        '{"approval_id": "old", "run_id": "r1", "issue_key": "", "title": "t", "reason": "r"}',
        encoding="utf-8",
    )

    loaded = store.load("old")

    assert loaded is not None and loaded.choice == ""


def test_the_interventions_report_reads_the_records_beside_the_run_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc.own_test_record import InterventionRecord, append_outcome, append_record

    monkeypatch.setenv("SPINE_RUN_STATE", str(tmp_path / "runs"))
    path = tmp_path / "runs" / "_interventions.jsonl"
    append_record(
        path,
        InterventionRecord(
            "r1", "r1-1", "", "w", "import_error", "e", ("t.py",), ("test_a",), "delete", "", "a", 1.0
        ),
    )
    append_outcome(path, run_id="r1", passed=True)

    result = CliRunner().invoke(app, ["sdlc", "runs", "interventions"])

    assert result.exit_code == 0, result.output
    assert "import_error" in result.output and "1 of 1" in result.output


def test_the_interventions_report_with_nothing_recorded_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SPINE_RUN_STATE", str(tmp_path / "runs"))

    result = CliRunner().invoke(app, ["sdlc", "runs", "interventions"])

    assert result.exit_code == 0 and "No questions" in result.output


# ---- B115: a rejection from the terminal does not strand a park whose process is gone (D87) --------


def _parked_run(tmp_path: Path, *, pid: int = 0) -> RunStore:
    import time

    runs = RunStore()  # the store the command reads: under SPINE_RUN_STATE
    runs.save(
        RunRecord(
            run_id="r1",
            source="s",
            status="parked",
            pid=pid,
            started_at=time.time(),
            heartbeat_at=time.time(),
            approval_id="a1",
        )
    )
    return runs


def test_rejecting_a_park_whose_process_exited_abandons_the_run(
    parked: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _git_name(monkeypatch, tmp_path, "Alice Example")
    runs = _parked_run(tmp_path)

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "a1", "--reject", "--note", "too big"])

    assert result.exit_code == 0, result.output
    record = runs.load("r1")
    assert record is not None and record.status == "abandoned"
    assert "Alice Example" in record.parked_reason and "too big" in record.parked_reason
    assert "Resume" not in result.output and "abandoned" in result.output


def test_approving_a_park_whose_process_exited_still_says_to_resume(
    parked: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _git_name(monkeypatch, tmp_path, "Alice Example")
    runs = _parked_run(tmp_path)

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "a1"])

    assert result.exit_code == 0, result.output
    record = runs.load("r1")
    assert record is not None and record.status == "parked" and "--resume r1" in result.output


def test_a_rejection_leaves_a_live_park_to_its_process(
    parked: ApprovalStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    _git_name(monkeypatch, tmp_path, "Alice Example")
    runs = _parked_run(tmp_path, pid=os.getpid())

    result = CliRunner().invoke(app, ["sdlc", "runs", "approve", "a1", "--reject", "--note", "no"])

    assert result.exit_code == 0, result.output
    record = runs.load("r1")
    assert record is not None and record.status == "parked"
