"""The shared `--repos` loader, and the one thing it parameterises.

Three commands spelled "repo config → merged graph → store" by hand and had drifted: a
different error prefix each, a different return shape each, and the NOT REPRODUCIBLE warning
in exactly one. `_merged_store` collapses them.

The prefix is the part worth a test. `RepoConfigError` is the most common thing a user sees
from these commands, and a shared helper is exactly how every one of them starts saying the
same wrong name — so each command asserts its own, and the assertion is that they *differ*.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app

#: `kind` is absent, which `load_repo_config` refuses at load rather than half-applying.
BAD_CONFIG = """\
repos:
  web: ./web
  billing: ./billing
joins:
  - consumer: web
    provider: billing
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def bad_config(tmp_path: Path) -> Path:
    for name in ("web", "billing"):
        (tmp_path / name).mkdir()
    config = tmp_path / "repos.yaml"
    config.write_text(BAD_CONFIG, encoding="utf-8")
    return config


@pytest.mark.parametrize(
    ("argv", "prefix"),
    [
        (["investigate", ".", "--title", "x", "--text", "y", "--repos"], "investigate: "),
        (["pkg", "extract", "--repos"], "pkg extract: "),
        (["pkg", "joins", "--check", "--config"], "pkg joins: "),
    ],
)
def test_each_command_names_itself_in_a_repo_config_error(
    runner: CliRunner, bad_config: Path, argv: list[str], prefix: str
) -> None:
    """A shared loader must not give three commands one name for their errors."""
    result = runner.invoke(app, [*argv, str(bad_config)])
    assert result.exit_code == 1
    assert result.output.startswith(prefix), result.output
    assert "expected one of" in result.output


def test_the_prefixes_are_actually_distinct(runner: CliRunner, bad_config: Path) -> None:
    """Guards the failure mode directly: one constant would pass every test above but one."""
    seen = set()
    for argv in (
        ["investigate", ".", "--title", "x", "--text", "y", "--repos"],
        ["pkg", "extract", "--repos"],
        ["pkg", "joins", "--check", "--config"],
    ):
        result = runner.invoke(app, [*argv, str(bad_config)])
        seen.add(result.output.split(":", 1)[0])
    assert seen == {"investigate", "pkg extract", "pkg joins"}
