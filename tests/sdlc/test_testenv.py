"""Isolated test env: missing-module parsing, auto-heal gating, dep resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.testenv import (
    LocalTestEnvironment,
    VenvTestEnvironment,
    make_test_environment,
    parse_missing_module,
    run_with_autoheal,
)
from orchestrator.sdlc.testrunner import TestRunResult


class _StubRunner:
    """Returns scripted results in order (repeats the last)."""

    def __init__(self, results: list[TestRunResult]) -> None:
        self._results = results
        self.calls = 0

    async def run(self, *, path: str) -> TestRunResult:
        r = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return r


class _StubEnv:
    def __init__(self, *, declared: set[str] | None = None, install_ok: bool = True) -> None:
        self.declared = declared or set()
        self._ok = install_ok
        self.installed: list[list[str]] = []

    @property
    def python(self) -> str:
        return "py"

    async def ensure(self, worktree: Any) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        self.installed.append(list(packages))
        return self._ok

    def describe(self) -> str:
        return "stub"


def _fail(output: str) -> TestRunResult:
    return TestRunResult(passed=False, returncode=2, output=output)


_PASS = TestRunResult(passed=True, returncode=0, output="1 passed")


class TestParseMissingModule:
    def test_simple(self) -> None:
        assert parse_missing_module("E   ModuleNotFoundError: No module named 'requests'") == "requests"

    def test_dotted_takes_top_level(self) -> None:
        assert parse_missing_module("No module named 'a.b.c'") == "a"

    def test_underscore_module(self) -> None:
        assert parse_missing_module("No module named 'pytest_mock'") == "pytest_mock"

    def test_no_match(self) -> None:
        assert parse_missing_module("E   assert 1 == 2") is None


async def test_autoheal_installs_safe_package_then_passes() -> None:
    runner = _StubRunner([_fail("No module named 'requests'"), _PASS])
    env = _StubEnv()
    result = await run_with_autoheal(runner, env, "/wt")
    assert result.passed
    assert env.installed == [["requests"]]


async def test_autoheal_maps_module_to_package() -> None:
    runner = _StubRunner([_fail("No module named 'pytest_mock'"), _PASS])
    env = _StubEnv()
    await run_with_autoheal(runner, env, "/wt")
    assert env.installed == [["pytest-mock"]]  # module pytest_mock → dist pytest-mock


async def test_real_failure_does_not_install() -> None:
    runner = _StubRunner([_fail("E   assert 1 == 2")])
    env = _StubEnv()
    result = await run_with_autoheal(runner, env, "/wt")
    assert not result.passed and env.installed == []


async def test_unlisted_package_blocked_without_optin() -> None:
    runner = _StubRunner([_fail("No module named 'frobnicate'")])
    env = _StubEnv()  # not declared, not in the safe set
    result = await run_with_autoheal(runner, env, "/wt")
    assert not result.passed and env.installed == []  # supply-chain gate held


async def test_unlisted_allowed_with_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_AUTOHEAL_UNLISTED", "1")
    runner = _StubRunner([_fail("No module named 'frobnicate'"), _PASS])
    env = _StubEnv()
    await run_with_autoheal(runner, env, "/wt")
    assert env.installed == [["frobnicate"]]


async def test_declared_package_installs() -> None:
    runner = _StubRunner([_fail("No module named 'mylib'"), _PASS])
    env = _StubEnv(declared={"mylib"})
    await run_with_autoheal(runner, env, "/wt")
    assert env.installed == [["mylib"]]


async def test_same_missing_module_installed_at_most_once() -> None:
    runner = _StubRunner([_fail("No module named 'requests'")])  # never recovers
    env = _StubEnv()
    result = await run_with_autoheal(runner, env, "/wt")
    assert not result.passed and env.installed == [["requests"]]  # tried once, then gave up


def test_make_test_environment_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_TEST_ISOLATION", "local")
    assert isinstance(make_test_environment(), LocalTestEnvironment)


def test_make_test_environment_default_is_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_TEST_ISOLATION", raising=False)
    assert isinstance(make_test_environment(), VenvTestEnvironment)


def test_local_env_install_is_noop() -> None:
    import asyncio

    assert asyncio.run(LocalTestEnvironment().install(["requests"])) is False


def test_project_dependencies_parsed_from_pyproject(tmp_path: Path) -> None:
    from orchestrator.sdlc.testenv import _project_dependencies

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["requests>=2", "httpx"]\n'
    )
    assert _project_dependencies(tmp_path) == ["requests>=2", "httpx"]


# --- Java (2b) ---------------------------------------------------------------


def test_make_test_environment_java() -> None:
    from orchestrator.sdlc.testenv import JavaToolEnvironment, make_test_environment

    assert isinstance(make_test_environment("java"), JavaToolEnvironment)


def test_make_test_runner_picks_maven_for_java() -> None:
    from orchestrator.sdlc.testenv import JavaToolEnvironment, make_test_runner
    from orchestrator.sdlc.testrunner import MavenTestRunner, SubprocessTestRunner

    assert isinstance(make_test_runner("java", JavaToolEnvironment()), MavenTestRunner)
    assert isinstance(make_test_runner("python", LocalTestEnvironment()), SubprocessTestRunner)


def test_java_env_install_is_noop_and_python_unavailable() -> None:
    import asyncio

    from orchestrator.sdlc.testenv import JavaToolEnvironment

    env = JavaToolEnvironment()
    assert asyncio.run(env.install(["junit"])) is False
    with pytest.raises(RuntimeError):
        _ = env.python


class _FakeProc:
    def __init__(self, rc: int, out: bytes) -> None:
        self.returncode = rc
        self._out = out

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        pass


async def test_maven_runner_passes_on_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import MavenTestRunner

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        return _FakeProc(0, b"BUILD SUCCESS")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await MavenTestRunner().run(path="/wt")
    assert result.passed and result.returncode == 0


async def test_maven_runner_fails_and_captures_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import MavenTestRunner

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        return _FakeProc(1, b"BUILD FAILURE\n[ERROR] WidgetTest.score failed")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await MavenTestRunner().run(path="/wt")
    assert not result.passed and "BUILD FAILURE" in result.output


# --- TypeScript (2b) ---------------------------------------------------------


def test_make_test_environment_typescript_threads_package_manager() -> None:
    from orchestrator.sdlc.testenv import NodeToolEnvironment, make_test_environment

    env = make_test_environment("typescript", build_tool="pnpm")
    assert isinstance(env, NodeToolEnvironment) and env.package_manager == "pnpm"
    default_env = make_test_environment("typescript")  # default package manager
    assert isinstance(default_env, NodeToolEnvironment) and default_env.package_manager == "npm"


def test_make_test_runner_picks_node_for_typescript() -> None:
    from orchestrator.sdlc.testenv import NodeToolEnvironment, make_test_runner
    from orchestrator.sdlc.testrunner import NodeTestRunner

    runner = make_test_runner("typescript", NodeToolEnvironment("yarn"))
    assert isinstance(runner, NodeTestRunner) and runner._pm == "yarn"  # pm from the env


def test_node_env_install_is_noop_and_python_unavailable() -> None:
    import asyncio

    from orchestrator.sdlc.testenv import NodeToolEnvironment

    env = NodeToolEnvironment()
    assert asyncio.run(env.install(["vitest"])) is False  # deps come from package.json
    with pytest.raises(RuntimeError):
        _ = env.python


def test_node_toolchain_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc import testenv

    def fake_which(present: set[str]) -> Any:
        return lambda name: f"/usr/bin/{name}" if name in present else None

    monkeypatch.setattr("orchestrator.sdlc.testenv.shutil.which", fake_which({"node", "pnpm"}))
    assert testenv.node_toolchain_available("pnpm") is True
    monkeypatch.setattr("orchestrator.sdlc.testenv.shutil.which", fake_which({"npm"}))  # node missing
    assert testenv.node_toolchain_available("npm") is False


async def test_node_env_ensure_runs_install(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc import testenv
    from orchestrator.sdlc.testenv import NodeToolEnvironment

    calls: list[tuple[object, ...]] = []

    async def fake_run_in(cwd: object, *argv: str) -> int:
        calls.append((cwd, *argv))
        return 0

    monkeypatch.setattr(testenv, "_run_in", fake_run_in)
    await NodeToolEnvironment("pnpm").ensure("/wt")
    assert calls == [(Path("/wt"), "pnpm", "install")]


async def test_node_runner_passes_on_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import NodeTestRunner

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        return _FakeProc(0, b"Test Files  1 passed (1)")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await NodeTestRunner().run(path="/wt")
    assert result.passed and result.returncode == 0


async def test_node_runner_fails_and_captures_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import NodeTestRunner

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        return _FakeProc(1, b"FAIL src/account.test.ts > deposit")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await NodeTestRunner().run(path="/wt")
    assert not result.passed and "FAIL" in result.output


# --- C# (1.2) ----------------------------------------------------------------


def test_make_test_environment_csharp() -> None:
    from orchestrator.sdlc.testenv import DotnetToolEnvironment, make_test_environment

    assert isinstance(make_test_environment("csharp"), DotnetToolEnvironment)


def test_make_test_runner_picks_dotnet_for_csharp() -> None:
    from orchestrator.sdlc.testenv import DotnetToolEnvironment, make_test_runner
    from orchestrator.sdlc.testrunner import DotnetTestRunner

    assert isinstance(make_test_runner("csharp", DotnetToolEnvironment()), DotnetTestRunner)


def test_dotnet_env_install_is_noop_and_python_unavailable() -> None:
    import asyncio

    from orchestrator.sdlc.testenv import DotnetToolEnvironment

    env = DotnetToolEnvironment()
    assert asyncio.run(env.install(["xunit"])) is False  # deps come from .csproj
    with pytest.raises(RuntimeError):
        _ = env.python


def test_dotnet_toolchain_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc import testenv

    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which",
        lambda name: "/usr/bin/dotnet" if name == "dotnet" else None,
    )
    assert testenv.dotnet_toolchain_available() is True
    monkeypatch.setattr("orchestrator.sdlc.testenv.shutil.which", lambda name: None)
    assert testenv.dotnet_toolchain_available() is False


def test_detect_dotnet_tfm(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc import testenv

    class _Proc:
        stdout = "10.0.301\n"

    monkeypatch.setattr("orchestrator.sdlc.testenv.shutil.which", lambda _n: "/usr/bin/dotnet")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Proc())
    assert testenv.detect_dotnet_tfm() == "net10.0"  # net{major}.0 from the SDK version


def test_detect_dotnet_tfm_falls_back_without_dotnet(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc import testenv

    monkeypatch.setattr("orchestrator.sdlc.testenv.shutil.which", lambda _n: None)
    assert testenv.detect_dotnet_tfm(default="net8.0") == "net8.0"


async def test_dotnet_runner_passes_on_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import DotnetTestRunner

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        return _FakeProc(0, b"Passed!  - Failed: 0, Passed: 3")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await DotnetTestRunner().run(path="/wt")
    assert result.passed and result.returncode == 0


async def test_dotnet_runner_fails_and_captures_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import DotnetTestRunner

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        return _FakeProc(1, b"error CS0103: The name 'Foo' does not exist")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await DotnetTestRunner().run(path="/wt")
    assert not result.passed and "CS0103" in result.output


async def test_dotnet_runner_targets_a_nested_solution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A monorepo's solution is often nested (not at the worktree root); the runner
    # must point `dotnet test` at it instead of failing to find a project at root.
    sln_dir = tmp_path / "backend" / "App"
    sln_dir.mkdir(parents=True)
    (sln_dir / "App.sln").write_text("")
    captured: list[tuple[object, ...]] = []

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        captured.append(a)
        return _FakeProc(0, b"Passed!")

    from orchestrator.sdlc.testrunner import DotnetTestRunner

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    await DotnetTestRunner().run(path=str(tmp_path))
    argv = captured[0]
    assert "test" in argv and str(sln_dir / "App.sln") in argv  # targeted the nested .sln


async def test_dotnet_runner_no_target_runs_at_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No solution/project (or several) → no path argument; dotnet resolves at root.
    captured: list[tuple[object, ...]] = []

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        captured.append(a)
        return _FakeProc(0, b"Passed!")

    from orchestrator.sdlc.testrunner import DotnetTestRunner

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    await DotnetTestRunner().run(path=str(tmp_path))
    assert captured[0] == ("dotnet", "test", "--nologo")


# --- C (2.2) -----------------------------------------------------------------


def test_make_test_environment_c() -> None:
    from orchestrator.sdlc.testenv import CToolEnvironment, make_test_environment

    assert isinstance(make_test_environment("c"), CToolEnvironment)


def test_make_test_runner_picks_ctest_for_c() -> None:
    from orchestrator.sdlc.testenv import CToolEnvironment, make_test_runner
    from orchestrator.sdlc.testrunner import CTestRunner

    assert isinstance(make_test_runner("c", CToolEnvironment()), CTestRunner)


def test_c_env_install_is_noop_and_python_unavailable() -> None:
    import asyncio

    from orchestrator.sdlc.testenv import CToolEnvironment

    env = CToolEnvironment()
    assert asyncio.run(env.install(["cmocka"])) is False  # C deps are system/CMake
    with pytest.raises(RuntimeError):
        _ = env.python


def test_c_toolchain_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc import testenv

    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"cmake", "clang"} else None,
    )
    assert testenv.c_toolchain_available() is True  # cmake + a compiler
    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which",
        lambda name: "/usr/bin/cmake" if name == "cmake" else None,  # no compiler
    )
    assert testenv.c_toolchain_available() is False


async def test_ctest_runner_passes_when_all_steps_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import CTestRunner

    calls: list[tuple[object, ...]] = []

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        calls.append(a)
        return _FakeProc(0, b"100% tests passed")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await CTestRunner().run(path="/wt")
    assert result.passed and result.returncode == 0
    # configure → build → test (three steps), starting with cmake -S/-B.
    assert len(calls) == 3 and calls[0][:2] == ("cmake", "-S")


async def test_ctest_runner_short_circuits_on_compile_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.testrunner import CTestRunner

    calls: list[tuple[object, ...]] = []

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        calls.append(a)
        rc = 0 if a[:2] == ("cmake", "-S") else 1  # configure ok, build fails
        return _FakeProc(rc, b"error: expected ';'")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await CTestRunner().run(path="/wt")
    assert not result.passed and "error:" in result.output
    assert len(calls) == 2  # stopped after the failing build; ctest skipped


# --- C with Meson (2.4) ------------------------------------------------------


def test_make_test_environment_c_carries_build_tool() -> None:
    from orchestrator.sdlc.testenv import CToolEnvironment, make_test_environment

    env = make_test_environment("c", build_tool="meson")
    assert isinstance(env, CToolEnvironment) and env.build_tool == "meson"
    default = make_test_environment("c")
    assert isinstance(default, CToolEnvironment) and default.build_tool == "cmake"  # default


def test_make_test_runner_picks_meson_when_build_tool_is_meson() -> None:
    from orchestrator.sdlc.testenv import CToolEnvironment, make_test_runner
    from orchestrator.sdlc.testrunner import CTestRunner, MesonTestRunner

    assert isinstance(make_test_runner("c", CToolEnvironment("meson")), MesonTestRunner)
    assert isinstance(make_test_runner("c", CToolEnvironment("cmake")), CTestRunner)


def test_meson_toolchain_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc import testenv

    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"meson", "ninja", "clang"} else None,
    )
    assert testenv.meson_toolchain_available() is True
    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"meson", "clang"} else None,  # no ninja
    )
    assert testenv.meson_toolchain_available() is False


async def test_meson_runner_configures_then_tests(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from orchestrator.sdlc.testrunner import MesonTestRunner

    calls: list[tuple[object, ...]] = []

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        calls.append(a)
        return _FakeProc(0, b"Ok: 1")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await MesonTestRunner().run(path=str(tmp_path))  # no build/ → setup + test
    assert result.passed
    assert calls[0][:2] == ("meson", "setup") and calls[1][:2] == ("meson", "test")


async def test_meson_runner_fails_on_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from orchestrator.sdlc.testrunner import MesonTestRunner

    calls: list[tuple[object, ...]] = []

    async def fake_exec(*a: object, **k: object) -> _FakeProc:
        calls.append(a)
        return _FakeProc(1, b"meson.build:3: ERROR: Unknown function")

    monkeypatch.setattr("orchestrator.sdlc.testrunner.asyncio.create_subprocess_exec", fake_exec)
    result = await MesonTestRunner().run(path=str(tmp_path))
    assert not result.passed and "ERROR" in result.output
    assert len(calls) == 1  # setup failed → test skipped


# --- C++ (3.2) ---------------------------------------------------------------


def test_make_test_environment_and_runner_for_cpp() -> None:
    from orchestrator.sdlc.testenv import CToolEnvironment, make_test_environment, make_test_runner
    from orchestrator.sdlc.testrunner import CTestRunner, MesonTestRunner

    assert isinstance(make_test_environment("cpp"), CToolEnvironment)
    assert isinstance(make_test_runner("cpp", CToolEnvironment("cmake")), CTestRunner)
    assert isinstance(make_test_runner("cpp", CToolEnvironment("meson")), MesonTestRunner)


def test_cpp_toolchain_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc import testenv

    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"cmake", "clang++"} else None,
    )
    assert testenv.cpp_toolchain_available() is True  # cmake + a C++ compiler
    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.shutil.which",
        lambda name: "/usr/bin/cmake" if name == "cmake" else None,  # no C++ compiler
    )
    assert testenv.cpp_toolchain_available() is False
