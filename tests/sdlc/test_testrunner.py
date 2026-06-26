"""Unit tests for the SDLC test-run seam.

``StubTestRunner`` is exercised purely in-memory; ``SubprocessTestRunner``
actually shells out to ``pytest`` in a tmp dir (skipped if pytest isn't on the
path) to prove the real default reports pass/fail from a genuine test run.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.testrunner import StubTestRunner, SubprocessTestRunner


async def test_stub_scripts_outcomes_then_repeats_last() -> None:
    runner = StubTestRunner([False, True])
    first = await runner.run(path="/tmp/ws")
    second = await runner.run(path="/tmp/ws")
    third = await runner.run(path="/tmp/ws")  # exhausted → repeats last (True)

    assert first.passed is False
    assert first.returncode == 1
    assert second.passed is True
    assert third.passed is True


async def test_stub_defaults_to_pass() -> None:
    runner = StubTestRunner()
    result = await runner.run(path="/tmp/ws")
    assert result.passed is True
    assert result.returncode == 0


# These run under pytest, so ``sys.executable -m pytest`` is always available.


async def test_subprocess_runner_passes_on_green_test(tmp_path: Path) -> None:
    (tmp_path / "test_ok.py").write_text("def test_ok() -> None:\n    assert 1 + 1 == 2\n", encoding="utf-8")
    result = await SubprocessTestRunner().run(path=str(tmp_path))
    assert result.passed is True
    assert result.returncode == 0


async def test_subprocess_runner_fails_on_red_test(tmp_path: Path) -> None:
    (tmp_path / "test_bad.py").write_text("def test_bad() -> None:\n    assert False\n", encoding="utf-8")
    result = await SubprocessTestRunner().run(path=str(tmp_path))
    assert result.passed is False
    assert result.returncode != 0
    assert "test_bad" in result.output


async def test_subprocess_runner_treats_no_tests_as_failure(tmp_path: Path) -> None:
    """An empty worktree (pytest exit 5) must not look green."""
    result = await SubprocessTestRunner().run(path=str(tmp_path))
    assert result.passed is False
