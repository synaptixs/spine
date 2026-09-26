"""A run is judged by the failures it caused, not the ones the repository already had (CB-764).

Four pre-existing test files in a SAM repository could not import in a fresh environment
(`boto3`, `langchain`, `DB_HOST`). pytest stopped at collection, every run reported rc=2, and the
refine loop spent six attempts editing a scraper whose own tests passed 4/4 when run alone.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.sdlc.baseline import (
    BaselineAwareRunner,
    baseline_enabled,
    environment_blocker,
    names_problems,
    take_baseline,
)
from orchestrator.sdlc.contracts import TestRunResult
from orchestrator.sdlc.diagnostics import missing_modules, pytest_problems
from orchestrator.sdlc.testrunner import SubprocessTestRunner


def _cb764(root: Path) -> None:
    """The field's shape: an existing tests/ dir whose files need what a fresh venv lacks."""
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_ask_cannabee.py").write_text("import langchain_not_installed\n", encoding="utf-8")
    (tests / "test_check_email.py").write_text(
        "import os\nHOST = os.environ['DB_HOST_NEVER_SET']\n", encoding="utf-8"
    )
    (tests / "test_healthy.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")


# --- parsing ---------------------------------------------------------------------------------


def test_pytest_problems_reads_failures_and_collection_errors() -> None:
    output = "\n".join(
        [
            "=========================== short test summary info ============================",
            "FAILED tests/test_new.py::test_bad - AssertionError: nope",
            "FAILED tests/test_new.py::test_p[x y] - assert 0",
            "ERROR tests/test_env.py - KeyError: 'DB_HOST'",
            "FAILED tests/test_new.py::test_bad - AssertionError: nope",
            "2 failed, 2 passed, 1 error in 0.49s",
        ]
    )

    assert pytest_problems(output) == (
        "tests/test_new.py::test_bad",
        "tests/test_new.py::test_p[x y]",
        "tests/test_env.py",
    )


def test_missing_modules_are_top_level_and_unique() -> None:
    out = "No module named 'boto3'\nNo module named 'langchain.chains'\nNo module named 'boto3'"
    assert missing_modules(out) == ["boto3", "langchain"]


# --- the real runner, on a real suite --------------------------------------------------------


async def test_one_unimportable_file_no_longer_stops_the_suite(tmp_path: Path) -> None:
    _cb764(tmp_path)
    runner = SubprocessTestRunner(sys.executable)

    result = await runner.run(path=str(tmp_path))

    assert names_problems(runner)
    assert result.returncode == 1  # tests ran; before --continue-on-collection-errors it was 2
    assert set(result.problems) == {"tests/test_ask_cannabee.py", "tests/test_check_email.py"}
    assert "1 passed" in result.output  # the healthy test was not held hostage


async def test_the_baseline_passes_a_change_that_adds_only_passing_tests(tmp_path: Path) -> None:
    _cb764(tmp_path)
    said: list[str] = []
    inner = SubprocessTestRunner(sys.executable)

    before = await take_baseline(inner, str(tmp_path), said.append)
    (tmp_path / "tests" / "test_ny_state_licence_scraper.py").write_text(
        "def test_scrapes():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    result = await BaselineAwareRunner(inner, before or frozenset()).run(path=str(tmp_path))

    assert before == frozenset({"tests/test_ask_cannabee.py", "tests/test_check_email.py"})
    assert "2 failure(s) predate this change" in said[0]
    assert result.passed
    assert result.output.startswith("[baseline] every failure predates this change")


async def test_a_new_failure_still_fails_and_refine_is_told_which_are_not_its(tmp_path: Path) -> None:
    _cb764(tmp_path)
    inner = SubprocessTestRunner(sys.executable)
    before = await take_baseline(inner, str(tmp_path), lambda _m: None)
    (tmp_path / "tests" / "test_new.py").write_text("def test_new():\n    assert 1 == 2\n", encoding="utf-8")

    result = await BaselineAwareRunner(inner, before or frozenset()).run(path=str(tmp_path))

    assert not result.passed
    first, second = result.output.splitlines()[:2]
    assert first.startswith("[baseline] NOT YOURS") and "tests/test_ask_cannabee.py" in first
    assert second == "[baseline] caused by this change: tests/test_new.py::test_new"


# --- when the baseline cannot be trusted -----------------------------------------------------


class _Scripted:
    names_problems = True

    def __init__(self, *results: Any) -> None:
        self._results = list(results)

    async def run(self, *, path: str) -> Any:
        return self._results.pop(0)


@pytest.mark.parametrize(
    "result",
    [
        TestRunResult(passed=False, returncode=2, output="Interrupted", problems=("tests/a.py",)),
        TestRunResult(passed=False, returncode=5, output="no tests ran"),
        TestRunResult(passed=False, returncode=1, output="E  something unparseable"),
        SimpleNamespace(passed=False, returncode=1, output="an old-style result with no problems field"),
    ],
)
async def test_an_unreadable_baseline_is_none_not_empty(result: Any) -> None:
    said: list[str] = []
    assert await take_baseline(_Scripted(result), "/wt", said.append) is None
    assert "judging every failure" in said[0]


async def test_a_green_baseline_is_empty() -> None:
    assert (
        await take_baseline(_Scripted(TestRunResult(True, 0, "3 passed")), "/wt", lambda _m: None)
        == frozenset()
    )


async def test_an_interrupted_run_is_never_passed_on_a_baseline() -> None:
    """rc=2 means the summary is incomplete: what it names is not all that failed."""
    interrupted = TestRunResult(passed=False, returncode=2, output="Interrupted", problems=("tests/old.py",))
    wrapped = BaselineAwareRunner(_Scripted(interrupted), frozenset({"tests/old.py"}))

    assert not (await wrapped.run(path="/wt")).passed


def test_the_baseline_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    assert baseline_enabled()
    monkeypatch.setenv("SDLC_TEST_BASELINE", "0")
    assert not baseline_enabled()


# --- the environment stop --------------------------------------------------------------------

_BOTO = "E   ModuleNotFoundError: No module named 'boto3'\nERROR tests/test_api.py - ModuleNotFoundError"


def _collection_error(*files: str, output: str = _BOTO) -> TestRunResult:
    return TestRunResult(passed=False, returncode=1, output=output, problems=files)


def test_unimportable_old_files_needing_a_dependency_are_an_environment_problem(tmp_path: Path) -> None:
    blocked = environment_blocker(
        _collection_error("tests/test_api.py"), [str(tmp_path / "src/x.py")], tmp_path
    )
    assert "tests/test_api.py" in blocked and "boto3" in blocked and "SDLC_AUTOHEAL_UNLISTED" in blocked


def test_a_file_this_run_wrote_is_never_an_environment_problem(tmp_path: Path) -> None:
    authored = [str(tmp_path / "tests" / "test_api.py")]
    assert environment_blocker(_collection_error("tests/test_api.py"), authored, tmp_path) == ""


def test_a_missing_module_the_repo_owns_was_broken_by_the_change(tmp_path: Path) -> None:
    (tmp_path / "src" / "boto3").mkdir(parents=True)
    assert environment_blocker(_collection_error("tests/test_api.py"), [], tmp_path) == ""


def test_an_ordinary_test_failure_is_never_an_environment_problem(tmp_path: Path) -> None:
    failed = _collection_error("tests/test_api.py", "tests/test_api.py::test_x")
    assert environment_blocker(failed, [], tmp_path) == ""
