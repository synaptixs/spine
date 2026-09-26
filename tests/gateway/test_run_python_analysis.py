"""Tests for run_python_analysis against the local-subprocess sandbox.

The E2B path is exercised only when E2B_API_KEY is set in the environment;
see test_e2b_path_loads_when_key_set below for the dispatch test.
"""

from __future__ import annotations

import os

import pytest

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools.run_python_analysis import (
    E2BSandbox,
    LocalSubprocessSandbox,
    RunPythonAnalysisHandler,
    default_sandbox,
)


def _ctx() -> InvocationContext:
    return InvocationContext(
        tool_id="tool.run_python_analysis",
        tool_version="0.1.0",
        trace_id="t-1",
        actor="dev",
    )


async def test_local_sandbox_executes_print() -> None:
    handler = RunPythonAnalysisHandler(sandbox=LocalSubprocessSandbox())
    out = await handler({"code": "print('hello from sandbox')"}, _ctx())
    assert out["exit_code"] == 0
    assert "hello from sandbox" in out["stdout"]
    assert out["backend"] == "local_subprocess"
    assert out["truncated"] is False


async def test_local_sandbox_captures_stderr_and_nonzero_exit() -> None:
    handler = RunPythonAnalysisHandler(sandbox=LocalSubprocessSandbox())
    out = await handler({"code": "import sys; sys.stderr.write('boom\\n'); sys.exit(2)"}, _ctx())
    assert out["exit_code"] == 2
    assert "boom" in out["stderr"]


async def test_local_sandbox_times_out_long_running_code() -> None:
    handler = RunPythonAnalysisHandler(sandbox=LocalSubprocessSandbox())
    out = await handler(
        {"code": "while True:\n    pass", "time_limit_seconds": 1},
        _ctx(),
    )
    assert out["exit_code"] != 0
    assert "timeout" in out["stderr"].lower()


# A busy loop spends CPU seconds as fast as wall seconds, so `RLIMIT_CPU` and the wall-clock
# timeout expire together and either can win. The test above failed about one run in six on
# whichever lost; these pin each outcome instead of hoping for one.


async def test_the_cpu_limit_winning_the_race_still_reads_as_a_timeout() -> None:
    """SIGXCPU before the wall clock used to return exit -24 with an empty stderr."""
    handler = RunPythonAnalysisHandler(sandbox=LocalSubprocessSandbox())
    out = await handler(
        {"code": "import os, signal\nos.kill(os.getpid(), signal.SIGXCPU)", "time_limit_seconds": 5},
        _ctx(),
    )
    assert out["exit_code"] == 124
    assert "timeout" in out["stderr"] and "CPU time" in out["stderr"]


async def test_a_child_already_gone_when_the_timeout_fires_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CPU limit reaped the child a moment before `kill()`: that raised ProcessLookupError."""
    import asyncio

    class _Gone:
        returncode = -24

        async def communicate(self) -> tuple[bytes, bytes]:
            raise TimeoutError  # what `wait_for` raises when the limit expires

        def kill(self) -> None:
            raise ProcessLookupError

        async def wait(self) -> int:
            return -24

    async def _spawn(*a: object, **k: object) -> _Gone:
        return _Gone()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    out = await RunPythonAnalysisHandler(sandbox=LocalSubprocessSandbox())(
        {"code": "while True:\n    pass", "time_limit_seconds": 1}, _ctx()
    )
    assert out["exit_code"] == 124
    assert "timeout" in out["stderr"]


async def test_a_sigkill_before_the_time_limit_is_not_called_a_timeout() -> None:
    """SIGKILL has other senders (the OOM killer); it is a timeout only once the limit passed."""
    handler = RunPythonAnalysisHandler(sandbox=LocalSubprocessSandbox())
    out = await handler(
        {"code": "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)", "time_limit_seconds": 5},
        _ctx(),
    )
    assert out["exit_code"] == -9
    assert "timeout" not in out["stderr"]


async def test_empty_code_rejected() -> None:
    handler = RunPythonAnalysisHandler(sandbox=LocalSubprocessSandbox())
    with pytest.raises(ValueError, match="non-empty"):
        await handler({"code": "   "}, _ctx())


def test_default_sandbox_picks_local_without_e2b_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    assert isinstance(default_sandbox(), LocalSubprocessSandbox)


def test_default_sandbox_picks_e2b_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "fake")
    sandbox = default_sandbox()
    assert isinstance(sandbox, E2BSandbox)


@pytest.mark.skipif(not os.getenv("E2B_API_KEY"), reason="E2B_API_KEY not set")
async def test_e2b_path_loads_when_key_set() -> None:  # pragma: no cover — opt-in
    sandbox = E2BSandbox(os.environ["E2B_API_KEY"])
    handler = RunPythonAnalysisHandler(sandbox=sandbox)
    out = await handler({"code": "print(1 + 1)"}, _ctx())
    assert out["backend"] == "e2b"
    assert "2" in out["stdout"]
