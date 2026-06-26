"""Preflight parity: the local gate equals the CI gate."""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.preflight import StubPreflightRunner, SubprocessPreflightRunner


async def test_no_pyproject_skips_with_pass(tmp_path: Path) -> None:
    result = await SubprocessPreflightRunner().run(path=str(tmp_path))
    assert result.passed and "skipped" in result.output


async def test_lint_failure_fails_with_output(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 110\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("import os\nimport sys\n", encoding="utf-8")  # unused imports
    result = await SubprocessPreflightRunner().run(path=str(tmp_path))
    assert not result.passed
    assert "ruff check failed" in result.output and "F401" in result.output


async def test_clean_tree_passes(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 110\n[tool.mypy]\nfiles = ['ok.py']\n", encoding="utf-8"
    )
    (tmp_path / "ok.py").write_text('"""Ok."""\n\nX: int = 1\n', encoding="utf-8")
    result = await SubprocessPreflightRunner().run(path=str(tmp_path))
    assert result.passed, result.output


async def test_stub_always_passes(tmp_path: Path) -> None:
    assert (await StubPreflightRunner().run(path=str(tmp_path))).passed
