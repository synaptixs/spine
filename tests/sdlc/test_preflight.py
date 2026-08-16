"""Preflight parity: the local gate equals the CI gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.preflight import (
    PreflightBaselineError,
    StubPreflightRunner,
    SubprocessPreflightRunner,
)


def _repo(tmp_path: Path, *, mypy: bool = True) -> Path:
    cfg = "[tool.ruff]\nline-length = 110\n"
    if mypy:
        cfg += "[tool.mypy]\nfiles = ['pkg.py']\n"
    (tmp_path / "pyproject.toml").write_text(cfg, encoding="utf-8")
    return tmp_path


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


# --- baseline diff -----------------------------------------------------------------
#
# `mergeable` must mean "this change is clean", not "this repo was already clean". Without
# these, the gate only works on repositories with a zero-finding backlog — which is Spine
# and very little else (ontomesh carries 3,378).


async def test_baseline_tolerates_pre_existing_findings(tmp_path: Path) -> None:
    """A repo that already fails its own bar still passes when nothing new is added."""
    root = _repo(tmp_path, mypy=False)
    (root / "dirty.py").write_text("import os\nimport sys\n", encoding="utf-8")  # 2x F401
    runner = SubprocessPreflightRunner()

    baseline = await runner.capture_baseline(path=str(root))
    assert baseline.total >= 2, baseline.describe()

    result = await runner.run(path=str(root), baseline=baseline)
    assert result.passed, result.output


async def test_baseline_still_catches_a_new_finding(tmp_path: Path) -> None:
    """The whole point: pre-existing noise is tolerated, new noise is not."""
    root = _repo(tmp_path, mypy=False)
    (root / "dirty.py").write_text("import os\n", encoding="utf-8")
    runner = SubprocessPreflightRunner()
    baseline = await runner.capture_baseline(path=str(root))

    (root / "added.py").write_text("import json\n", encoding="utf-8")  # a NEW F401
    result = await runner.run(path=str(root), baseline=baseline)
    assert not result.passed
    assert "NEW finding" in result.output and "F401" in result.output


async def test_baseline_ignores_pure_line_shifts(tmp_path: Path) -> None:
    """Inserting lines moves every finding below it; that must not read as new.

    This is why the diff keys on (path, code) and not on line number.
    """
    root = _repo(tmp_path, mypy=False)
    src = root / "dirty.py"
    src.write_text("import os\nimport sys\n", encoding="utf-8")
    runner = SubprocessPreflightRunner()
    baseline = await runner.capture_baseline(path=str(root))

    src.write_text("# a comment\n# another\n" + src.read_text(encoding="utf-8"), encoding="utf-8")
    result = await runner.run(path=str(root), baseline=baseline)
    assert result.passed, result.output


async def test_baseline_allows_fixing_existing_findings(tmp_path: Path) -> None:
    """Reducing the backlog is never a failure."""
    root = _repo(tmp_path, mypy=False)
    src = root / "dirty.py"
    src.write_text("import os\nimport sys\n", encoding="utf-8")
    runner = SubprocessPreflightRunner()
    baseline = await runner.capture_baseline(path=str(root))

    src.write_text("", encoding="utf-8")
    result = await runner.run(path=str(root), baseline=baseline)
    assert result.passed, result.output


async def test_unrunnable_tool_is_reported_not_counted_clean(tmp_path: Path) -> None:
    """mypy with no config never sees a file. That is 'excluded', not 'passed'."""
    root = _repo(tmp_path, mypy=False)
    (root / "x.py").write_text("X = 1\n", encoding="utf-8")
    baseline = await SubprocessPreflightRunner().capture_baseline(path=str(root))
    assert "mypy" in baseline.skipped
    assert "excluded" in baseline.describe()


async def test_missing_pyproject_is_a_hard_stop_for_baselines(tmp_path: Path) -> None:
    """`run` skips with a pass; capturing a baseline must refuse instead.

    A silent pass reads exactly like a real result, which is the failure mode that produced
    two sets of meaningless numbers on 2026-08-15.
    """
    with pytest.raises(PreflightBaselineError, match="pyproject"):
        await SubprocessPreflightRunner().capture_baseline(path=str(tmp_path))


async def test_baseline_is_absent_by_default(tmp_path: Path) -> None:
    """Production (worker.py) passes no baseline, so any finding still fails."""
    root = _repo(tmp_path, mypy=False)
    (root / "dirty.py").write_text("import os\n", encoding="utf-8")
    result = await SubprocessPreflightRunner().run(path=str(root))
    assert not result.passed
