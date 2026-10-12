"""``SDLC_TEST_COMMAND`` — run the generated tests through a caller-supplied argv.

The default Python runner is ``python -m pytest`` from an interpreter Spine builds itself. A
repository whose suite needs another interpreter (an old Django on Python 3.5) or another
runner (``tests/runtests.py``) cannot be driven that way, so a caller can supply the command —
typically a ``docker exec`` into an image that already has the project's environment.

Most tests here use a real temporary git repository: the runner selects its files from
``git status``, and the coverage probe stashes a production file between two runs, so a fake
would prove nothing about either.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc import feature_runner, testpaths
from orchestrator.sdlc.feature_runner import FeatureRunError, _check_test_command, _typecheck_the_change
from orchestrator.sdlc.testenv import LocalTestEnvironment, VenvTestEnvironment, run_with_autoheal
from orchestrator.sdlc.testrunner import (
    CommandTestRunner,
    SubprocessTestRunner,
    custom_test_command,
    parse_test_command,
)
from orchestrator.sdlc.toolchains import _python_environment, _python_runner

PRINT_ARGS = [sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))"]


def _git(root: Path, *args: str) -> None:
    # A repo-local identity: CI has none, and `stash` makes commits too.
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True
    )


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    # The probes under test run plain `git stash`, which needs an identity; CI has none.
    _git(tmp_path, "config", "user.email", "t@e")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "prod.py").write_text("X = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


# --- parsing the setting ------------------------------------------------------------------


def test_parse_accepts_a_json_array_of_strings() -> None:
    assert parse_test_command('["docker", "exec", "c", "{files}"]') == ["docker", "exec", "c", "{files}"]


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"a": 1}',
        '"just a string"',
        "[]",
        '["ok", 3]',
        '["ok", ""]',
        '["run", "{files}", "{files}"]',
        '["run", "--files={files}"]',
    ],
)
def test_parse_rejects_anything_else_naming_the_setting(raw: str) -> None:
    with pytest.raises(ValueError, match="SDLC_TEST_COMMAND"):
        parse_test_command(raw)


def test_custom_test_command_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_TEST_COMMAND", raising=False)
    assert custom_test_command() is None
    monkeypatch.setenv("SDLC_TEST_COMMAND", "   ")
    assert custom_test_command() is None
    monkeypatch.setenv("SDLC_TEST_COMMAND", '["echo", "hi"]')
    assert custom_test_command() == ["echo", "hi"]
    monkeypatch.setenv("SDLC_TEST_COMMAND", "{broken")
    with pytest.raises(ValueError, match="SDLC_TEST_COMMAND"):
        custom_test_command()


# --- selecting the files ------------------------------------------------------------------


async def test_the_command_runs_as_argv_with_files_expanded_in_place(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_b.py").write_text("", encoding="utf-8")
    (root / "prod.py").write_text("X = 2\n", encoding="utf-8")  # modified, but not a test

    result = await CommandTestRunner([*PRINT_ARGS, "before", "{files}", "after"]).run(path=str(root))

    assert result.passed is True and result.returncode == 0
    assert json.loads(result.output) == ["before", "tests/test_a.py", "tests/test_b.py", "after"]


async def test_there_is_no_shell_so_odd_names_arrive_literally(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "tests").mkdir()
    marker = root / "pwned"
    odd = ["tests/test a;b.py", "tests/test_$(touch pwned).py", "tests/test_`touch pwned`.py"]
    for name in odd:
        (root / name).write_text("", encoding="utf-8")

    result = await CommandTestRunner([*PRINT_ARGS, "{files}"]).run(path=str(root))

    assert sorted(json.loads(result.output)) == sorted(odd)
    assert not marker.exists()


async def test_an_empty_selection_fails_and_never_runs_the_command(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "prod.py").write_text("X = 2\n", encoding="utf-8")  # a change, but no test file
    calls: list[tuple[str, ...]] = []

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        calls.append(argv)
        return 0, "ran"

    result = await CommandTestRunner([*PRINT_ARGS, "{files}"], capture=capture).run(path=str(root))

    assert result.passed is False and result.returncode == 5
    assert "No changed test files" in result.output
    assert calls == []  # not a fallback to the whole suite


async def test_a_command_without_the_token_runs_even_when_nothing_changed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    result = await CommandTestRunner([*PRINT_ARGS, "fixed"]).run(path=str(root))
    assert result.passed is True
    assert json.loads(result.output) == ["fixed"]


async def test_the_selection_survives_the_coverage_probes_stash(tmp_path: Path) -> None:
    """The probe stashes a *production* file and re-runs; the new test file must still be there."""
    root = _repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_new.py").write_text("", encoding="utf-8")
    (root / "prod.py").write_text("X = 2\n", encoding="utf-8")
    before = await testpaths.changed_test_files(root)

    _git(root, "stash", "push", "--include-untracked", "--quiet", "--", "prod.py")
    after = await testpaths.changed_test_files(root)

    assert before == after == ["tests/test_new.py"]


async def test_a_review_fix_after_the_build_is_committed_still_runs_the_builds_tests(
    tmp_path: Path,
) -> None:
    """`autorun` commits the build, then the review loop re-runs *the same runner* after each fix.

    Selecting from `git status` alone finds nothing once the build is committed, so a reviewer's
    production-only fix looked like "no tests" (exit 5) and was discarded as having broken them.
    """
    root = _repo(tmp_path)
    runner = CommandTestRunner([*PRINT_ARGS, "{files}"])
    await runner.pin_base(str(root))  # the loop pins this before the build writes anything
    (root / "tests").mkdir()
    (root / "tests" / "test_new.py").write_text("", encoding="utf-8")
    (root / "prod.py").write_text("X = 2\n", encoding="utf-8")
    first = await runner.run(path=str(root))
    assert json.loads(first.output) == ["tests/test_new.py"]

    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the build")
    (root / "prod.py").write_text("X = 3\n", encoding="utf-8")  # the reviewer's fix: production only

    second = await runner.run(path=str(root))

    assert second.passed is True and second.returncode == 0
    assert json.loads(second.output) == ["tests/test_new.py"]


async def test_the_base_is_pinned_on_first_use_when_nobody_pinned_it(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    runner = CommandTestRunner([*PRINT_ARGS, "{files}"])
    (root / "tests").mkdir()
    (root / "tests" / "test_new.py").write_text("", encoding="utf-8")
    await runner.run(path=str(root))

    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "the build")
    after = await runner.run(path=str(root))

    assert json.loads(after.output) == ["tests/test_new.py"]


async def test_the_selection_since_a_base_includes_what_was_committed_after_it(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "later")
    (root / "tests" / "test_b.py").write_text("", encoding="utf-8")  # untracked

    assert await testpaths.changed_test_files(root, base=base) == ["tests/test_a.py", "tests/test_b.py"]
    assert await testpaths.changed_test_files(root) == ["tests/test_b.py"]  # since HEAD, as before


async def test_a_base_that_does_not_exist_is_an_error_never_a_full_run(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    calls: list[tuple[str, ...]] = []

    async def capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
        calls.append(argv)
        return 0, "ran"

    runner = CommandTestRunner([*PRINT_ARGS, "{files}"], base="deadbeef" * 5, capture=capture)
    result = await runner.run(path=str(root))

    assert result.passed is False and result.returncode == 2
    assert calls == []


async def test_only_python_files_are_selected_for_a_python_command(tmp_path: Path) -> None:
    """One fixture under `tests/` made `pytest {files}` exit 4 ("file not found") on every run."""
    root = _repo(tmp_path)
    (root / "tests" / "data").mkdir(parents=True)
    (root / "tests" / "data" / "sample.json").write_text("{}", encoding="utf-8")
    (root / "tests" / "test_ok.py").write_text("", encoding="utf-8")

    result = await CommandTestRunner([*PRINT_ARGS, "{files}"]).run(path=str(root))

    assert json.loads(result.output) == ["tests/test_ok.py"]


async def test_a_change_with_only_fixtures_is_an_empty_selection(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "sample.json").write_text("{}", encoding="utf-8")

    result = await CommandTestRunner([*PRINT_ARGS, "{files}"]).run(path=str(root))

    assert result.passed is False and result.returncode == 5


async def test_a_name_starting_with_a_dash_is_not_taken_for_an_option(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "-x_test.py").write_text("", encoding="utf-8")

    result = await CommandTestRunner([*PRINT_ARGS, "{files}"]).run(path=str(root))

    assert json.loads(result.output) == ["./-x_test.py"]


async def test_a_staged_rename_selects_the_new_name_and_a_deletion_selects_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    (root / "tests" / "test_c.py").write_text("def test_c():\n    pass\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "tests")

    _git(root, "mv", "tests/test_a.py", "tests/test_b.py")
    _git(root, "rm", "-q", "tests/test_c.py")

    assert await testpaths.changed_test_files(root) == ["tests/test_b.py"]


# --- how a run ends -----------------------------------------------------------------------


async def test_a_failing_command_fails_with_its_return_code_and_a_clipped_tail(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    noisy = [sys.executable, "-c", "import sys; print('x' * 9000 + 'TAIL'); sys.exit(3)"]
    result = await CommandTestRunner(noisy).run(path=str(root))

    assert result.passed is False and result.returncode == 3
    assert len(result.output) <= 4000 and result.output.rstrip().endswith("TAIL")


async def test_a_timeout_is_reported_like_every_other_runner(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    result = await CommandTestRunner(slow, timeout=0.3).run(path=str(root))

    assert result.passed is False and result.returncode == -1
    assert "timed out" in result.output


async def test_credentials_never_reach_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sekrit")
    monkeypatch.setenv("SDLC_TEST_COMMAND", '["x"]')
    probe = [sys.executable, "-c", "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'absent'))"]
    result = await CommandTestRunner(probe).run(path=str(root))

    assert result.output.strip() == "absent"


def test_the_runner_names_no_problems_so_no_whole_suite_baseline_is_taken() -> None:
    from orchestrator.sdlc.baseline import names_problems

    assert names_problems(CommandTestRunner(["x"])) is False
    assert names_problems(SubprocessTestRunner()) is True  # the default is unchanged


# --- wiring -------------------------------------------------------------------------------


def test_the_python_toolchain_uses_the_command_only_when_it_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_TEST_COMMAND", raising=False)
    assert isinstance(_python_runner(LocalTestEnvironment()), SubprocessTestRunner)

    monkeypatch.setenv("SDLC_TEST_COMMAND", '["echo", "{files}"]')
    runner = _python_runner(LocalTestEnvironment())
    assert isinstance(runner, CommandTestRunner)


def test_a_command_implies_local_isolation_and_the_default_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SDLC_TEST_COMMAND", raising=False)
    monkeypatch.delenv("SDLC_TEST_ISOLATION", raising=False)
    assert isinstance(_python_environment(""), VenvTestEnvironment)

    monkeypatch.setenv("SDLC_TEST_COMMAND", '["echo"]')
    assert isinstance(_python_environment(""), LocalTestEnvironment)


class _EnvThatMustNotBeUsed:
    @property
    def python(self) -> str:
        raise AssertionError("the host interpreter must not be consulted")

    async def install(self, packages: list[str]) -> bool:
        raise AssertionError("nothing may be installed into the host environment")


async def test_the_in_loop_type_check_is_skipped_with_a_logged_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDLC_TEST_COMMAND", '["echo"]')
    emitted: list[str] = []

    out = await _typecheck_the_change(tmp_path, _EnvThatMustNotBeUsed(), ["a.py"], emitted.append)

    assert out == ""
    assert any("SDLC_TEST_COMMAND" in line for line in emitted)


async def test_the_in_loop_type_check_still_consults_the_environment_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SDLC_TEST_COMMAND", raising=False)
    with pytest.raises(AssertionError, match="host interpreter"):
        await _typecheck_the_change(tmp_path, _EnvThatMustNotBeUsed(), ["a.py"], lambda _line: None)


def test_pytest_is_not_required_of_the_host_when_a_command_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Toolchain:
        requires_pytest = True

    monkeypatch.delenv("SDLC_TEST_COMMAND", raising=False)
    assert feature_runner._needs_pytest("python", _Toolchain()) is True
    monkeypatch.setenv("SDLC_TEST_COMMAND", '["echo"]')
    assert feature_runner._needs_pytest("python", _Toolchain()) is False


def test_a_bad_setting_stops_the_run_with_exit_code_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_TEST_COMMAND", "{broken")
    with pytest.raises(FeatureRunError) as caught:
        _check_test_command()
    assert caught.value.code == 2 and "SDLC_TEST_COMMAND" in str(caught.value)

    monkeypatch.setenv("SDLC_TEST_COMMAND", '["echo"]')
    assert _check_test_command() == ["echo"]
    monkeypatch.delenv("SDLC_TEST_COMMAND")
    assert _check_test_command() is None


def test_the_test_path_rule_moved_without_a_second_copy() -> None:
    assert feature_runner._is_test_path is testpaths.is_test_path
    cases: dict[str, Any] = {
        "tests/sdlc/test_thing.py": True,
        "src/pkg/thing_test.py": True,
        "src/orchestrator/mcp/contract.py": False,
        "src/latest/protest.py": False,
    }
    for rel, expected in cases.items():
        assert testpaths.is_test_path(rel) is expected, rel


async def test_a_command_runner_is_not_auto_healed_by_installing_into_the_host() -> None:
    """A module missing inside the container cannot be fixed by installing it on the host, and
    the log line "installing X into the project env" would say it was."""
    from types import SimpleNamespace

    class _Failing:
        autoheal = False

        async def run(self, *, path: str) -> Any:
            return SimpleNamespace(
                passed=False, returncode=1, output="ModuleNotFoundError: No module named 'requests'"
            )

    class _Env(LocalTestEnvironment):
        async def install(self, packages: list[str]) -> bool:
            raise AssertionError("must not install into the host for a command runner")

    emitted: list[str] = []
    result = await run_with_autoheal(_Failing(), _Env(), "/tmp/x", emit=emitted.append)

    assert result.passed is False
    assert emitted == []
    assert CommandTestRunner(["x"]).autoheal is False


# --- the real probes, driven by this runner -----------------------------------------------

#: Passes only while `prod.py` holds the change (`X = 2`); a command that ignores {files}.
_SEES_THE_CHANGE = [
    sys.executable,
    "-c",
    "import sys; sys.exit(0 if 'X = 2' in open('prod.py').read() else 1)",
    "{files}",
]


def _change_with_a_test(root: Path) -> list[str]:
    (root / "tests").mkdir()
    (root / "tests" / "test_new.py").write_text("", encoding="utf-8")
    (root / "prod.py").write_text("X = 2\n", encoding="utf-8")
    return ["prod.py", "tests/test_new.py"]


async def test_the_coverage_probe_finds_a_file_nothing_exercises(tmp_path: Path) -> None:
    """`_files_no_test_exercises` stashes each production file and re-runs the tests; if they
    still pass, nothing tests that file. With this runner the re-run must still select the test."""
    from orchestrator.sdlc.feature_runner import _files_no_test_exercises

    root = _repo(tmp_path)
    files = _change_with_a_test(root)
    notes: list[str] = []

    exercised = await _files_no_test_exercises(root, files, CommandTestRunner(_SEES_THE_CHANGE), notes.append)
    assert exercised == []  # without the change the command fails, so something exercises it

    blind = CommandTestRunner([sys.executable, "-c", "pass", "{files}"])  # passes whatever the code is
    unexercised = await _files_no_test_exercises(root, files, blind, notes.append)
    assert unexercised == ["prod.py"]
    assert (root / "prod.py").read_text(encoding="utf-8") == "X = 2\n"  # the stash was popped


async def test_the_prove_the_tests_probe_works_with_this_runner(tmp_path: Path) -> None:
    from orchestrator.sdlc.feature_runner import _prove_the_tests_test_something

    root = _repo(tmp_path)
    files = _change_with_a_test(root)
    notes: list[str] = []

    await _prove_the_tests_test_something(root, files, CommandTestRunner(_SEES_THE_CHANGE), notes.append)
    assert any("fail without the change" in line for line in notes), notes

    notes.clear()
    blind = CommandTestRunner([sys.executable, "-c", "pass", "{files}"])
    await _prove_the_tests_test_something(root, files, blind, notes.append)
    assert any("pass without the change" in line for line in notes), notes
