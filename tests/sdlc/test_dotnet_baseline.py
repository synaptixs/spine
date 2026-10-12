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

from orchestrator.sdlc.baseline import environment_blocker, names_problems
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
