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
