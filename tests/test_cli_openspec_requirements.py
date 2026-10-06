"""`openspec draft --idea`, `openspec check`, `openspec answer` — through the real command line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app

_IDEA = "Let finance export invoices as CSV"
_CHANGE = "let-finance-export-invoices-as-csv"


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _json(output: str) -> dict[str, object]:
    return json.loads(output[output.index("{") : output.rindex("}") + 1])  # type: ignore[no-any-return]


def _draft(runner: CliRunner, root: Path) -> None:
    result = runner.invoke(app, ["openspec", "draft", "--idea", _IDEA, "--out", str(root)])
    assert result.exit_code == 0, result.output


def test_draft_needs_exactly_one_of_source_or_idea(runner: CliRunner, tmp_path: Path) -> None:
    assert runner.invoke(app, ["openspec", "draft", "--out", str(tmp_path)]).exit_code == 2
    both = runner.invoke(
        app, ["openspec", "draft", "--idea", "x", "--source", "file://a.md", "--out", str(tmp_path)]
    )
    assert both.exit_code == 2


def test_draft_idea_writes_a_skeleton_with_no_model(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["openspec", "draft", "--idea", _IDEA, "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = _json(result.stdout)
    assert payload["drafted"][0]["change_id"] == _CHANGE  # type: ignore[index]
    assert len(payload["drafted"][0]["questions"]) == 4  # type: ignore[index]
    assert (tmp_path / "changes" / _CHANGE / "proposal.md").is_file()


def test_draft_idea_never_clobbers_a_change_a_person_has_edited(runner: CliRunner, tmp_path: Path) -> None:
    _draft(runner, tmp_path)
    proposal = tmp_path / "changes" / _CHANGE / "proposal.md"
    proposal.write_text("edited by a person\n")
    again = runner.invoke(app, ["openspec", "draft", "--idea", _IDEA, "--out", str(tmp_path)])
    assert again.exit_code == 0 and proposal.read_text() == "edited by a person\n"


def test_check_fails_the_gate_with_exit_1_and_says_it_is_ungrounded(
    runner: CliRunner, tmp_path: Path
) -> None:
    _draft(runner, tmp_path)
    result = runner.invoke(app, ["openspec", "check", _CHANGE, "--root", str(tmp_path)])
    assert result.exit_code == 1
    payload = _json(result.stdout)
    assert payload["passes"] is False
    assert payload["code"]["grounding"] == "ungrounded"  # type: ignore[index]
    assert payload["open_items"][0]["rule"] == "problem_stated"  # type: ignore[index]


def test_answer_then_check_passes_with_exit_0(runner: CliRunner, tmp_path: Path) -> None:
    _draft(runner, tmp_path)
    answers = tmp_path / "answers.yaml"
    answers.write_text(
        "answers:\n"
        "  - question: What problem does this solve?\n    answer: Finance cannot reconcile invoices.\n"
        "  - question: Who has this problem?\n    answer: Finance operations\n"
        "  - question: How will we know it worked? Describe an outcome someone can observe.\n"
        "    answer: Month-end closes within 1 day.\n"
        "  - question: What is explicitly out of scope?\n    defer_to: '@finance-lead'\n"
    )
    done = runner.invoke(
        app, ["openspec", "answer", _CHANGE, "--root", str(tmp_path), "--answers", str(answers)]
    )
    assert done.exit_code == 0, done.output
    checked = runner.invoke(app, ["openspec", "check", _CHANGE, "--root", str(tmp_path)])
    assert checked.exit_code == 0, checked.output
    assert _json(checked.stdout)["passes"] is True


def test_answer_one_question_from_the_command_line(runner: CliRunner, tmp_path: Path) -> None:
    _draft(runner, tmp_path)
    result = runner.invoke(
        app,
        [
            "openspec",
            "answer",
            _CHANGE,
            "--root",
            str(tmp_path),
            "--question",
            "What problem does this solve?",
            "--answer",
            "Reconciling",
        ],
    )
    assert result.exit_code == 0, result.output
    md = (tmp_path / "changes" / _CHANGE / "proposal.md").read_text()
    assert "**Answer** (user · cli ·" in md and "### Problem\nReconciling" in md


def test_answer_with_an_unknown_question_exits_2_and_writes_nothing(
    runner: CliRunner, tmp_path: Path
) -> None:
    _draft(runner, tmp_path)
    proposal = tmp_path / "changes" / _CHANGE / "proposal.md"
    before = proposal.read_text()
    result = runner.invoke(
        app,
        [
            "openspec",
            "answer",
            _CHANGE,
            "--root",
            str(tmp_path),
            "--question",
            "Which colour?",
            "--answer",
            "blue",
        ],
    )
    assert result.exit_code == 2 and "Open questions" in result.output
    assert proposal.read_text() == before


def test_answer_needs_a_question_or_a_file(runner: CliRunner, tmp_path: Path) -> None:
    _draft(runner, tmp_path)
    assert runner.invoke(app, ["openspec", "answer", _CHANGE, "--root", str(tmp_path)]).exit_code == 2


def test_check_on_a_missing_change_exits_2(runner: CliRunner, tmp_path: Path) -> None:
    assert runner.invoke(app, ["openspec", "check", "ghost", "--root", str(tmp_path)]).exit_code == 2


def test_check_against_a_repository_reports_criteria_naming_existing_code(
    runner: CliRunner, tmp_path: Path
) -> None:
    """The code half: a criterion that names a function the repo defines is reported as naming
    code that exists — evidence to confirm before building it, never a verdict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "export.py").write_text("def export_invoices_csv(rows):\n    return ','.join(rows)\n")
    root = tmp_path / "openspec"
    change = root / "changes" / "exp"
    (change / "specs" / "cap").mkdir(parents=True)
    (change / "proposal.md").write_text(
        "# Proposal: Export\n\n## Why\n### Problem\nFinance cannot reconcile invoices.\n"
        "### Users\n- Finance\n"
        "### Outcome\nCloses within 1 day.\n\n## What Changes\nDo it.\n### Non-goals\n- PDF\n"
    )
    (change / "specs" / "cap" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: Export\nThe system SHALL call `export_invoices_csv`.\n\n"
        "#### Scenario: ok\n- WHEN run\n- THEN a CSV is returned\n"
    )
    result = runner.invoke(app, ["openspec", "check", "exp", str(repo), "--root", str(root)])
    assert result.exit_code == 0, result.output
    code = _json(result.stdout)["code"]
    assert code["grounding"] in ("grounded", "untrusted")  # type: ignore[index]
    assert any("export_invoices_csv" in json.dumps(c) for c in code["criteria_naming_existing_code"])  # type: ignore[index]
