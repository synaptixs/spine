"""`pkg accuracy` at the command layer.

The library functions are tested elsewhere. What is tested here is the wiring: a flag that
is declared but never dispatched, or an oracle that reports a regression and still exits 0,
is invisible to every library test and breaks the one contract CI depends on — the exit code.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small repo with one resolvable call and one call through a parameter."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(
        "def helper() -> int:\n"
        "    return 1\n"
        "\n"
        "\n"
        "def caller() -> int:\n"
        "    return helper()\n"
        "\n"
        "\n"
        "def through(cb) -> int:\n"
        "    return cb()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_invention_oracle_reports_and_exits_zero(runner: CliRunner, repo: Path) -> None:
    """A low score is a finding, not a build failure. Only `--check` gates.

    The count is now zero, and that is the point: the fixture's `through(cb)` used to make the
    Python front-end emit `py:cb`, and it no longer does. The oracle stays because C still
    invents for the equivalent shape and because Python could regress — a guard is worth
    keeping precisely when it is reporting nothing.
    """
    result = runner.invoke(app, ["pkg", "accuracy", "--oracle", "invention", str(repo)])
    assert result.exit_code == 0, result.output
    assert "invented CALLS edges — 0" in result.output
    assert "py:cb" not in result.output, "a call through a parameter must no longer be invented"


def test_the_parity_oracle_separates_shortfall_from_surplus(runner: CliRunner, repo: Path) -> None:
    """Never one ratio: a doubly-mounted router legitimately yields more nodes than
    decorators, so a combined figure would hide both halves."""
    result = runner.invoke(app, ["pkg", "accuracy", "--oracle", "parity", str(repo)])
    assert result.exit_code == 0, result.output
    assert "shortfall" in result.output
    assert "surplus" in result.output


def test_the_runtime_oracle_is_not_run_by_default(runner: CliRunner, repo: Path) -> None:
    """It executes the repository's code. Nothing may reach it implicitly."""
    result = runner.invoke(app, ["pkg", "accuracy", "--oracle", "parity", str(repo)])
    assert "test suite" not in result.output.lower()
    assert result.exit_code == 0


def test_an_unknown_oracle_fails_and_names_the_known_ones(runner: CliRunner) -> None:
    result = runner.invoke(app, ["pkg", "accuracy", "--oracle", "telepathy"])
    assert result.exit_code == 1
    assert "corpus, runtime, parity, invention" in result.output


def test_the_sampler_is_only_offered_with_the_invention_oracle(runner: CliRunner, repo: Path) -> None:
    """`--sample` lists facts for human review; it must not silently do nothing elsewhere."""
    result = runner.invoke(
        app, ["pkg", "accuracy", "--oracle", "invention", str(repo), "--sample", "2", "--kind", "CALLS"]
    )
    assert result.exit_code == 0, result.output
    assert "sampled CALLS edge(s) for review" in result.output
    assert "deterministic for this commit" in result.output


def test_an_unknown_edge_kind_for_the_sampler_fails(runner: CliRunner, repo: Path) -> None:
    result = runner.invoke(
        app, ["pkg", "accuracy", "--oracle", "invention", str(repo), "--sample", "1", "--kind", "WAT"]
    )
    assert result.exit_code == 1
    assert "unknown edge kind" in result.output


def test_check_without_a_baseline_fails_with_an_actionable_message(runner: CliRunner, repo: Path) -> None:
    """A missing baseline is a setup error, not a passing build."""
    result = runner.invoke(app, ["pkg", "accuracy", "--check", str(repo)])
    assert result.exit_code == 1
    assert "--scoreboard" in result.output, "the message must say how to create one"


def test_check_passes_against_a_baseline_it_just_wrote(runner: CliRunner, repo: Path) -> None:
    """The gate's core contract: today's numbers are baselined IN, so `--check` is green.

    Also proves a scoreboard works on a repo with **no corpus at all** — most repositories
    have none, and parity and invention need only the source.
    """
    written = runner.invoke(app, ["pkg", "accuracy", "--scoreboard", str(repo)])
    assert written.exit_code == 0, written.output

    result = runner.invoke(app, ["pkg", "accuracy", "--check", str(repo)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_the_json_report_is_machine_readable(runner: CliRunner, repo: Path) -> None:
    import json

    result = runner.invoke(app, ["pkg", "accuracy", "--oracle", "invention", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["oracle"] == "invention"
    assert payload["invented"] == 0
    assert payload["rate"] == 0.0
    assert payload["unexamined"] == 0
    assert payload["examples"] == []
