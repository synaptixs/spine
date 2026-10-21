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
