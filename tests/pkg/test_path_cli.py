from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mod.py").write_text(
        "def helper() -> int:\n"
        "    return 1\n"
        "\n"
        "\n"
        "def caller() -> int:\n"
        "    return helper()\n"
        "\n"
        "\n"
        "def isolated() -> int:\n"
        "    return 2\n",
        encoding="utf-8",
    )
    return tmp_path


def test_path_renders_a_grounded_forward_call_chain(runner: CliRunner, repo: Path) -> None:
    result = runner.invoke(app, ["pkg", "path", "caller", "helper", "--path", str(repo)])

    assert result.exit_code == 0, result.output
    assert "1 extracted hop(s)" in result.output
    assert "CALLS" in result.output
    assert "py:app.mod.caller" in result.output
    assert "py:app.mod.helper" in result.output
    assert "Caveat: Paths use extracted static facts only" in result.output


def test_path_defaults_the_repository_to_the_current_directory(
    runner: CliRunner, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["pkg", "path", "caller", "helper"])

    assert result.exit_code == 0, result.output
    assert "py:app.mod.caller" in result.output


def test_path_json_is_machine_readable_and_includes_the_contract(runner: CliRunner, repo: Path) -> None:
    result = runner.invoke(app, ["pkg", "path", "caller", "helper", "--path", str(repo), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["found"] is True
    assert payload["distance"] == 1
    assert payload["direction"] == "forward"
    assert payload["source"]["id"] == "py:app.mod.caller"
    assert payload["target"]["id"] == "py:app.mod.helper"
    assert payload["hops"][0]["kind"] == "CALLS"
    assert payload["hops"][0]["at"] == "app/mod.py:6"
    assert payload["hops"][0]["reversed"] is False
    assert "no runtime relationship" in payload["caveat"]


def test_path_refresh_reextracts_successfully(runner: CliRunner, repo: Path) -> None:
    result = runner.invoke(app, ["pkg", "path", "caller", "helper", "--path", str(repo), "--refresh"])

    assert result.exit_code == 0, result.output
    assert "py:app.mod.helper" in result.output


def test_path_includes_document_mentions_only_when_explicit(runner: CliRunner, repo: Path) -> None:
    (repo / "README.md").write_text("# Guide\n\nUse `helper` for the value.\n", encoding="utf-8")

    omitted = runner.invoke(app, ["pkg", "path", "README.md#guide", "helper", "--path", str(repo), "--json"])
    included = runner.invoke(
        app,
        [
            "pkg",
            "path",
            "README.md#guide",
            "helper",
            "--path",
            str(repo),
            "--kind",
            "mentions",
            "--json",
        ],
    )

    assert omitted.exit_code == 1
    assert json.loads(omitted.output)["found"] is False
    assert included.exit_code == 0, included.output
    assert json.loads(included.output)["hops"][0]["kind"] == "MENTIONS"


def test_path_reverse_renders_stored_orientation_with_traversal_note(runner: CliRunner, repo: Path) -> None:
    result = runner.invoke(
        app,
        ["pkg", "path", "helper", "caller", "--path", str(repo), "--direction", "reverse"],
    )

    assert result.exit_code == 0, result.output
    assert "py:app.mod.caller --CALLS--> py:app.mod.helper (traversed reverse)" in result.output
    assert "CALLS (reverse)" not in result.output


def test_path_reports_no_extracted_path_with_a_nonzero_exit(runner: CliRunner, repo: Path) -> None:
    result = runner.invoke(app, ["pkg", "path", "caller", "isolated", "--path", str(repo), "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["found"] is False
    assert payload["distance"] is None
    assert payload["hops"] == []


def test_path_keeps_structural_edges_opt_in(runner: CliRunner, repo: Path) -> None:
    omitted = runner.invoke(app, ["pkg", "path", "py:app.mod", "caller", "--path", str(repo)])
    included = runner.invoke(
        app,
        ["pkg", "path", "py:app.mod", "caller", "--path", str(repo), "--include-structural"],
    )

    assert omitted.exit_code == 1, omitted.output
    assert included.exit_code == 0, included.output
    assert "CONTAINS" in included.output


def test_path_warns_when_structural_flag_is_ignored_with_explicit_kinds(
    runner: CliRunner, repo: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "pkg",
            "path",
            "caller",
            "helper",
            "--path",
            str(repo),
            "--kind",
            "calls",
            "--include-structural",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "--include-structural is ignored" in result.output


def test_path_rejects_invalid_direction_edge_kind_and_serves(runner: CliRunner, repo: Path) -> None:
    bad_direction = runner.invoke(
        app,
        ["pkg", "path", "caller", "helper", "--path", str(repo), "--direction", "sideways"],
    )
    bad_kind = runner.invoke(
        app, ["pkg", "path", "caller", "helper", "--path", str(repo), "--kind", "telepathy"]
    )
    serves = runner.invoke(app, ["pkg", "path", "caller", "helper", "--path", str(repo), "--kind", "serves"])

    assert bad_direction.exit_code == 2
    assert "--direction must be one of" in bad_direction.output
    assert bad_kind.exit_code == 2
    assert "Unknown edge kind" in bad_kind.output
    assert serves.exit_code == 2
    assert "SERVES is not supported" in serves.output


def test_path_refuses_an_ambiguous_short_name(runner: CliRunner, repo: Path) -> None:
    package = repo / "app"
    (package / "other.py").write_text("def helper() -> int:\n    return 3\n", encoding="utf-8")

    result = runner.invoke(app, ["pkg", "path", "helper", "caller", "--path", str(repo)])

    assert result.exit_code == 2
    assert "Ambiguous source 'helper'" in result.output
    assert "py:app.mod.helper" in result.output
    assert "py:app.other.helper" in result.output
