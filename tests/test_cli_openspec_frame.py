"""P9a of SSPN-297 (SSPN-319): ``orchestrator openspec frame`` — through the real command line.

``frame "<idea>"`` drafts the framing change; ``frame --from <framing> --capability …`` drafts one
change per capability once the framing passes ``check``. Exit 0 written · 1 the framing does not
pass yet · 2 an input the command refuses. No model is called."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app

_IDEA = "A small web app where a household shares a chore rota"
_FRAMING = "a-small-web-app-where-a-household-shares-framing"
_PRODUCT = "a-small-web-app-where-a-household-shares"
_ANSWERS = (
    "answers:\n"
    "  - question: What problem does this solve?\n"
    "    answer: Households keep the rota in a group chat, so chores are forgotten.\n"
    "  - question: Who has this problem?\n"
    "    answer: Adults sharing a home; two to six people\n"
    "  - question: How will we know it worked? Describe an outcome someone can observe.\n"
    "    answer: Each member sees whose turn each chore is and gets a reminder on the day.\n"
    "  - question: What is explicitly out of scope?\n"
    "    answer: Payments or expense splitting; native mobile apps\n"
)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _json(output: str) -> dict[str, object]:
    return json.loads(output[output.index("{") : output.rindex("}") + 1])  # type: ignore[no-any-return]


def _frame(runner: CliRunner, root: Path, *, answered: bool = True) -> None:
    out = runner.invoke(app, ["openspec", "frame", _IDEA, "--root", str(root)])
    assert out.exit_code == 0, out.output
    if answered:
        answers = root / "answers.yaml"
        answers.write_text(_ANSWERS)
        done = runner.invoke(
            app, ["openspec", "answer", _FRAMING, "--root", str(root), "--answers", str(answers)]
        )
        assert done.exit_code == 0, done.output


def test_frame_drafts_the_framing_change_and_says_what_to_answer(runner: CliRunner, tmp_path: Path) -> None:
    out = runner.invoke(app, ["openspec", "frame", _IDEA, "--root", str(tmp_path)])
    assert out.exit_code == 0, out.output
    assert _json(out.stdout)["framing"] == _FRAMING
    assert (tmp_path / "changes" / _FRAMING / "proposal.md").is_file()
    assert "openspec answer" in out.stderr


def test_capabilities_before_the_framing_passes_is_exit_1_and_writes_nothing(
    runner: CliRunner, tmp_path: Path
) -> None:
    _frame(runner, tmp_path, answered=False)
    out = runner.invoke(
        app,
        ["openspec", "frame", "--from", _FRAMING, "--capability", "Assign chores", "--root", str(tmp_path)],
    )
    assert out.exit_code == 1, out.output
    assert sorted(p.name for p in (tmp_path / "changes").iterdir()) == [_FRAMING]


def test_capabilities_from_a_framing_that_passes_are_drafted(runner: CliRunner, tmp_path: Path) -> None:
    _frame(runner, tmp_path)
    args = ["openspec", "frame", "--from", _FRAMING, "--root", str(tmp_path)]
    out = runner.invoke(
        app, [*args, "--capability", "Assign chores in turn", "--capability", "Send a reminder"]
    )
    assert out.exit_code == 0, out.output
    assert _json(out.stdout)["drafted"] == [
        f"{_PRODUCT}-assign-chores-in-turn",
        f"{_PRODUCT}-send-a-reminder",
    ]
    check = runner.invoke(app, ["openspec", "check", f"{_PRODUCT}-send-a-reminder", "--root", str(tmp_path)])
    assert check.exit_code == 1  # its own outcome is still open


@pytest.mark.parametrize(
    "args",
    [
        ["openspec", "frame"],  # neither an idea nor --from
        ["openspec", "frame", _IDEA, "--from", _FRAMING],  # both
        ["openspec", "frame", _IDEA, "--capability", "Assign chores"],  # --capability needs --from
        ["openspec", "frame", "--from", _FRAMING],  # --from needs a capability
    ],
)
def test_inputs_the_command_cannot_take_are_exit_2(
    runner: CliRunner, tmp_path: Path, args: list[str]
) -> None:
    _frame(runner, tmp_path)
    out = runner.invoke(app, [*args, "--root", str(tmp_path)])
    assert out.exit_code == 2, out.output


def test_a_bad_input_is_exit_2_even_before_the_framing_passes(runner: CliRunner, tmp_path: Path) -> None:
    # The input is checked first: a framing not yet answered does not turn a usage error into exit 1.
    _frame(runner, tmp_path, answered=False)
    out = runner.invoke(app, ["openspec", "frame", "--from", _FRAMING, "--root", str(tmp_path)])
    assert out.exit_code == 2, out.output
    assert "at least one capability" in out.stderr


def test_too_many_capabilities_is_exit_2_and_says_how_many(runner: CliRunner, tmp_path: Path) -> None:
    _frame(runner, tmp_path)
    caps = [a for n in range(1, 10) for a in ("--capability", f"Capability number {n}")]
    out = runner.invoke(app, ["openspec", "frame", "--from", _FRAMING, *caps, "--root", str(tmp_path)])
    assert out.exit_code == 2, out.output
    assert "9 capabilities" in out.stderr
