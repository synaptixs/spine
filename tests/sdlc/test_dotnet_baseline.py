"""The .NET runner names what failed, so a run can tell old failures from its own (B73).

`DotnetTestRunner` filled no `TestRunResult.problems` and set no `names_problems`, so
`feature_runner` never took a pre-change baseline for C#: failures the repository already had
were indistinguishable from the ones the change caused, and refine edited unrelated files to clear
them. Names below are invented; none is a field repository's.
"""

from __future__ import annotations

from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.baseline import (
    BaselineAwareRunner,
    check_baseline,
    environment_blocker,
    midrun_blocker,
    names_problems,
    take_baseline,
)
from orchestrator.sdlc.contracts import TestRunResult
from orchestrator.sdlc.testrunner import DotnetTestRunner

_WARNING = "/w/WebApp/F1.cs(1,5): warning CS8618: Non-nullable property 'P1' must contain a non-null value."


class _Proc:
    def __init__(self, rc: int, out: str) -> None:
        self.returncode = rc
        self._out = out.encode()

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        pass


def _stub(monkeypatch: pytest.MonkeyPatch, rc: int, out: str) -> None:
    async def fake_exec(*_a: object, **_k: object) -> _Proc:
        return _Proc(rc, out)

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)


def test_the_dotnet_runner_takes_part_in_the_baseline() -> None:
    assert names_problems(DotnetTestRunner()) is True


async def test_failing_tests_are_named_as_problems(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = "\n".join(
        [
            _WARNING,
            "  Failed Acme.Widgets.Tests.ParserTests.Rejects [3 ms]",
            "  Failed Acme.Widgets.Tests.ParserTests.Empty [1 ms]",
            "Failed!  - Failed:     2, Passed:    40, Skipped:     0, Total:    42",
        ]
    )
    _stub(monkeypatch, 1, out)

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert not result.passed and result.returncode == 1
    assert result.problems == (
        "Acme.Widgets.Tests.ParserTests.Rejects",
        "Acme.Widgets.Tests.ParserTests.Empty",
    )


async def test_a_build_that_failed_names_no_tests(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exit 1 is also a failed build; none of its failures is a test, so none may be a problem."""
    out = "\n".join(
        [
            f"{tmp_path}/WebApp/A.cs(2,7): error CS1003: Syntax error, ',' expected",
            "  Failed Acme.Widgets.Tests.ParserTests.Stale [1 ms]",  # left over from a log; no test ran
        ]
    )
    _stub(monkeypatch, 1, out)

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert not result.passed
    assert result.problems == ()


async def test_a_green_run_names_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub(monkeypatch, 0, "Passed!  - Failed: 0, Passed: 12")

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert result.passed and result.problems == ()


async def test_a_timeout_is_reported_and_names_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_exec(*_a: object, **_k: object) -> _Proc:
        return _Proc(0, "")

    async def late(aw: Coroutine[Any, Any, Any], timeout: float) -> object:
        aw.close()  # never awaited by a timeout; close it so the stub leaves no warning
        raise TimeoutError

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.wait_for", late)

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert not result.passed and result.returncode == -1
    assert "timed out" in result.output
    assert result.problems == ()


async def test_a_failure_nothing_explains_reaches_refine_as_unparsed_not_as_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, "\n".join([_WARNING] * 200 + ["Something unexpected happened."]))

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert result.output.startswith("UNPARSED FAILURE")
    assert "warning CS" not in result.output


async def test_a_restore_failure_leads_the_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cause = (
        f"{tmp_path}/WebApp/App.csproj : error NU1301: Failed to retrieve information about "
        f"'Acme.Widgets' from remote source 'https://feed.example/index.json'. [{tmp_path}/s.sln]"
    )
    _stub(
        monkeypatch,
        1,
        "\n".join([cause, "  Failed to restore " + str(tmp_path) + "/WebApp/App.csproj (in 6.82 sec)."]),
    )

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert result.output.splitlines()[1].strip().startswith("WebApp/App.csproj: error NU1301")
    assert result.problems == ()


def test_a_dotnet_test_name_is_not_mistaken_for_an_uncollectable_python_file(tmp_path: Path) -> None:
    """`is_collection_error` means "no `::`", and a .NET name has none.

    `environment_blocker` additionally needs Python's "No module named"; this pins that a run
    whose problems are .NET test names is never read as a missing dependency.
    """
    result = TestRunResult(
        False,
        1,
        "  Failed Acme.Widgets.Tests.ParserTests.Rejects [3 ms]",
        ("Acme.Widgets.Tests.ParserTests.Rejects",),
    )

    assert environment_blocker(result, [], tmp_path) == ""


# --- why the run failed (TestRunResult.failure) ----------------------------------------------


@pytest.mark.parametrize(
    ("rc", "out", "failure"),
    [
        (1, "{p}/WebApp/A.cs(2,7): error CS1003: Syntax error, ',' expected", "build"),
        (1, "{p}/WebApp/App.csproj : error NU1301: Failed to retrieve information about 'X'", "feed"),
        (1, "{p}/WebApp/App.csproj : error NU1101: Unable to find package Acme.Widgets", "restore"),
        (
            1,
            "{p}/WebApp/App.csproj : error NETSDK1045: The current .NET SDK does not support .NET 8.0",
            "restore",
        ),
        (1, "You must install or update .NET to run this application.", "sdk"),
        (
            1,
            "MSBUILD : error MSB1003: Specify a project or solution file. The directory has none.",
            "project",
        ),
        (1, "  Failed Acme.Widgets.Tests.ParserTests.Rejects [3 ms]", ""),
        (0, "Passed!  - Failed: 0, Passed: 12", ""),
    ],
)
async def test_the_runner_says_why_a_run_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rc: int, out: str, failure: str
) -> None:
    _stub(monkeypatch, rc, out.format(p=tmp_path))

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert result.failure == failure


async def test_a_timeout_is_a_failure_of_its_own(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_exec(*_a: object, **_k: object) -> _Proc:
        return _Proc(0, "")

    async def late(aw: Coroutine[Any, Any, Any], timeout: float) -> object:
        aw.close()
        raise TimeoutError

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.wait_for", late)

    assert (await DotnetTestRunner().run(path=str(tmp_path))).failure == "timeout"


# --- what refine is shown once a baseline is known (D7) ---------------------------------------

_OLD = "Acme.Widgets.Tests.ParserTests.Old"
_NEW = "Acme.Widgets.Tests.ParserTests.New"
_TWO_FAILURES = "\n".join(
    [
        _WARNING,
        f"  Failed {_OLD} [3 ms]",
        "  Error Message:",
        "   Assert.Equal() Failure: old and broken",
        "",
        f"  Failed {_NEW} [2 ms]",
        "  Error Message:",
        "   Assert.Equal() Failure: the change broke this",
        "Failed!  - Failed:     2, Passed:    40, Skipped:     0, Total:    42",
    ]
)


#: Only the failure the baseline had; the summary agrees with it, as a finished run's always does.
_ONE_FAILURE = "\n".join(
    [*_TWO_FAILURES.splitlines()[:5], "Failed!  - Failed:     1, Passed:    41, Skipped:     0, Total:    42"]
)


async def test_refine_is_not_shown_a_failure_the_baseline_already_had(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, _TWO_FAILURES)
    runner = DotnetTestRunner()
    runner.know_baseline(frozenset({_OLD}))

    result = await runner.run(path=str(tmp_path))

    assert result.problems == (_OLD, _NEW)  # the judge still sees both
    assert "old and broken" not in result.output and _OLD not in result.output
    assert "the change broke this" in result.output
    assert result.output.startswith("FAILED TESTS (1)")


async def test_a_run_whose_every_failure_is_old_is_not_called_unparsed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, _ONE_FAILURE)
    runner = DotnetTestRunner()
    runner.know_baseline(frozenset({_OLD}))

    result = await runner.run(path=str(tmp_path))

    assert not result.output.startswith("UNPARSED")


async def test_old_failures_do_not_count_against_a_run_that_added_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, _ONE_FAILURE)
    judged = BaselineAwareRunner(DotnetTestRunner(), frozenset({_OLD}))

    result = await judged.run(path=str(tmp_path))

    assert result.passed
    assert "predates this change" in result.output


async def test_a_new_failure_still_fails_the_run_and_the_old_one_is_out_of_refines_sight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, _TWO_FAILURES)
    judged = BaselineAwareRunner(DotnetTestRunner(), frozenset({_OLD}))

    result = await judged.run(path=str(tmp_path))

    assert not result.passed
    assert "NOT YOURS" in result.output and "the change broke this" in result.output
    assert "old and broken" not in result.output


async def test_the_wrapper_keeps_why_a_run_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stub(monkeypatch, 1, _TWO_FAILURES)
    inner = DotnetTestRunner()

    class _Failing:
        names_problems = True

        async def run(self, *, path: str) -> TestRunResult:
            base = await inner.run(path=path)
            return TestRunResult(base.passed, base.returncode, base.output, base.problems, "feed")

    result = await BaselineAwareRunner(_Failing(), frozenset({_OLD})).run(path=str(tmp_path))

    assert result.failure == "feed"


# --- the baseline itself (D2, D4, D8) ---------------------------------------------------------


class _Once:
    """A runner that returns one canned result."""

    names_problems = True

    def __init__(self, result: TestRunResult) -> None:
        self._result = result

    async def run(self, *, path: str) -> TestRunResult:
        return self._result


def _res(
    passed: bool, rc: int, output: str = "", problems: tuple[str, ...] = (), failure: str = ""
) -> TestRunResult:
    return TestRunResult(passed, rc, output, problems, failure)


async def test_a_green_baseline_is_a_clean_slate() -> None:
    check = await check_baseline(_Once(_res(True, 0)), "/w", lambda _m: None)

    assert check.problems == frozenset() and check.stop == ""


async def test_a_baseline_of_failing_tests_is_recorded() -> None:
    check = await check_baseline(_Once(_res(False, 1, "x", (_OLD,))), "/w", lambda _m: None)

    assert check.problems == frozenset({_OLD}) and check.stop == ""


async def test_a_red_build_before_any_change_stops_the_run() -> None:
    out = (
        "COMPILER ERRORS, in the order the build reported them:\n"
        "  WebApp/A.cs(2,7): error CS1003: Syntax error, ',' expected\n\n"
        "--- end of the build output ---\nBuild FAILED."
    )

    check = await check_baseline(_Once(_res(False, 1, out, (), "build")), "/w", lambda _m: None)

    assert check.problems is None
    assert check.stop.startswith("VERDICT: BLOCKED")
    assert "build is red before any change" in check.stop
    assert "WebApp/A.cs(2,7): error CS1003" in check.stop
    assert "SDLC_TEST_BASELINE=0" in check.stop


@pytest.mark.parametrize("failure", ["restore", "feed", "sdk"])
async def test_an_environment_failure_before_any_change_stops_the_run(failure: str) -> None:
    out = "COMPILER ERRORS:\n  WebApp/App.csproj: error NU1301: Failed to retrieve information about 'X'\n"

    check = await check_baseline(_Once(_res(False, 1, out, (), failure)), "/w", lambda _m: None)

    assert check.problems is None
    assert check.stop.startswith("VERDICT: BLOCKED")
    assert "environment" in check.stop and "NU1301" in check.stop
    assert "SDLC_TEST_BASELINE=0" in check.stop


async def test_a_baseline_that_timed_out_is_not_a_stop() -> None:
    lines: list[str] = []

    check = await check_baseline(
        _Once(_res(False, -1, "dotnet test run timed out", (), "timeout")), "/w", lines.append
    )

    assert check.problems is None and check.stop == ""
    assert any("timed out" in line or "could not read" in line for line in lines)


async def test_an_unreadable_baseline_is_not_a_stop() -> None:
    check = await check_baseline(_Once(_res(False, 2, "???", ())), "/w", lambda _m: None)

    assert check.problems is None and check.stop == ""


async def test_take_baseline_keeps_its_contract() -> None:
    assert await take_baseline(_Once(_res(False, 1, "x", (_OLD,))), "/w", lambda _m: None) == frozenset(
        {_OLD}
    )
    assert await take_baseline(_Once(_res(False, 1, "x", (), "build")), "/w", lambda _m: None) is None


# --- mid-run: only what the change cannot have caused (D10) -----------------------------------


@pytest.mark.parametrize("failure", ["feed", "sdk", "timeout"])
def test_these_failures_stop_a_run_that_is_under_way(failure: str) -> None:
    assert midrun_blocker(_res(False, 1, "NU1301 ...", (), failure))


@pytest.mark.parametrize("failure", ["", "build", "restore"])
def test_these_go_to_refine_because_the_change_may_have_caused_them(failure: str) -> None:
    """A PackageReference the model added can fail to resolve; refine can edit the .csproj."""
    assert midrun_blocker(_res(False, 1, "x", (), failure)) == ""


def test_a_passing_run_is_never_a_blocker() -> None:
    assert midrun_blocker(_res(True, 0, "", (), "feed")) == ""


# --- real output (B73 review) ------------------------------------------------------------------

_REAL = Path(__file__).parent.parent / "fixtures" / "dotnet"


def _real(name: str) -> str:
    return (_REAL / name).read_text(encoding="utf-8")


async def test_a_crashed_test_host_is_never_judged_passed_on_a_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One old failure, then a test that kills the host: exit 1, one `Failed` block, no verdict."""
    _stub(monkeypatch, 1, _real("xunit_host_crash.txt"))

    judged = BaselineAwareRunner(DotnetTestRunner(), frozenset({"OldTests.Sum"}))
    result = await judged.run(path=str(tmp_path))

    assert not result.passed  # the crashed test never reported
    assert result.failure == "aborted"
    assert result.problems == ()  # an aborted run cannot say what failed
    assert "TEST RUN ABORTED" in result.output and "Test host process crashed" in result.output


async def test_a_baseline_taken_from_a_crashed_run_is_unreadable_not_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, _real("xunit_host_crash.txt"))

    check = await check_baseline(DotnetTestRunner(), str(tmp_path), lambda _m: None)

    assert check.problems is None and check.stop == ""


async def test_a_summary_that_disagrees_with_the_named_failures_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = "\n".join(
        [
            "  Failed Acme.Widgets.Tests.ParserTests.One [3 ms]",
            "  Failed Acme.Widgets.Tests.ParserTests.Two [3 ms]",
            "Failed!  - Failed:     3, Passed:    40, Skipped:     0, Total:    43",
        ]
    )
    _stub(monkeypatch, 1, out)

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert result.problems == ()  # three failed, two named: someone is missing


async def test_a_run_with_no_summary_at_all_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, "  Failed Acme.Widgets.Tests.ParserTests.One [3 ms]\n")

    assert (await DotnetTestRunner().run(path=str(tmp_path))).problems == ()


async def test_failing_tests_that_print_compiler_errors_are_tests_not_a_red_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, _real("xunit_diagnostic_text.txt"))

    result = await DotnetTestRunner().run(path=str(tmp_path))
    check = await check_baseline(DotnetTestRunner(), str(tmp_path), lambda _m: None)

    assert result.failure == ""
    assert result.problems == ("DiagTests.Plain", "DiagTests.Snippet")
    assert check.stop == ""  # a healthy repo is not BLOCKED
    assert check.problems == frozenset({"DiagTests.Plain", "DiagTests.Snippet"})


async def test_real_output_is_judged_with_old_failures_left_out_of_sight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, _real("xunit_failures.txt"))
    runner = DotnetTestRunner()
    runner.know_baseline(frozenset({"OldTests.Sum", "OldTests.Prints"}))

    result = await runner.run(path=str(tmp_path))

    assert len(result.problems) == 5
    assert result.output.startswith("FAILED TESTS (3)")
    assert "OldTests.Sum" not in result.output and "OldTests.Prints" not in result.output


async def test_no_project_to_build_is_a_baseline_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub(monkeypatch, 1, "MSBUILD : error MSB1003: Specify a project or solution file. None found.")

    check = await check_baseline(DotnetTestRunner(), str(tmp_path), lambda _m: None)

    assert check.stop.startswith("VERDICT: BLOCKED") and "MSB1003" in check.stop


def test_an_aborted_run_is_refines_to_judge_not_a_reason_to_stop() -> None:
    """The model may have written the test that crashed the host; refine can fix that."""
    assert midrun_blocker(_res(False, 1, "x", (), "aborted")) == ""


async def test_the_older_vstest_summary_is_read_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`Total tests: N / Passed: / Failed:` is what older SDKs print instead of `Failed!  - Failed: N`."""
    out = "\n".join(
        [
            "  Failed Acme.Widgets.Tests.ParserTests.One [3 ms]",
            "  Error Message:",
            "   boom",
            "",
            "Total tests: 3",
            "     Passed: 2",
            "     Failed: 1",
            " Total time: 1.2 Seconds",
            "Test Run Failed.",
        ]
    )
    _stub(monkeypatch, 1, out)

    result = await DotnetTestRunner().run(path=str(tmp_path))

    assert result.problems == ("Acme.Widgets.Tests.ParserTests.One",)
