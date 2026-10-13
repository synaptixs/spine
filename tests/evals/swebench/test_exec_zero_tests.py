"""B74 (SSPN-160): a run in which the runner executed zero tests is never green.

Two benchmark instances ended at the coverage stop after every in-loop test run had executed nothing
and been reported green: Django printed ``Ran 0 tests`` and exited 0 (the model's tests were plain
classes, which Django's unittest runner does not collect), and sympy's ``bin/test`` printed
``tests finished: 0 passed`` and exited 0. pytest returns 5 for "no tests collected"; these two do not,
so the refine loop never ran and the model was never told its tests were not being run.

The property is refuse-only: *a run whose runner itself reports that it executed zero tests never
exits 0.* It turns a false green into exit 5, as pytest does. It never turns a failure into a pass,
never touches a non-zero exit, and never fires on a run that executed tests and skipped them.

The two zero-test outputs and the passing Django output below are the real ones kept by the attempt
record in the pilot v2 books (``out/run-tests-<n>.txt``), not reconstructions. The sympy passing
output and the skipped variants are synthetic (no real sympy run in those books passed).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.evals.swebench import exec as sx
from tests.evals.swebench.test_exec import _Fake, _repo

IMAGE = "img@sha256:" + "a" * 64

# --- recorded (pilot v2, 453d8b5f) -----------------------------------------------------------

DJANGO_ZERO = (
    "Testing against Django installed in '/testbed/django'\n"
    "System check identified no issues (0 silenced).\n"
    "\n"
    "----------------------------------------------------------------------\n"
    "Ran 0 tests in 0.000s\n"
    "\n"
    "OK\n"
)

SYMPY_ZERO = (
    "============================= test process starts ==============================\n"
    "executable:         /opt/miniconda3/envs/testbed/bin/python  (3.9.20-final-0) [CPython]\n"
    "architecture:       64-bit\n"
    "cache:              no\n"
    "ground types:       python \n"
    "numpy:              None\n"
    "random seed:        69476447\n"
    "hash randomization: on (PYTHONHASHSEED=1723796322)\n"
    "\n"
    "\n"
    "================== tests finished: 0 passed, in 0.00 seconds ===================\n"
)

DJANGO_PASSING = (
    "Testing against Django installed in '/testbed/django'\n"
    "System check identified no issues (0 silenced).\n"
    "..........................................\n"
    "----------------------------------------------------------------------\n"
    "Ran 42 tests in 0.070s\n"
    "\n"
    "OK\n"
)

# A real passing run of a pytest-kind repository (xarray-6461, pilot v2). pytest reports
# 'no tests collected' by its own exit status 5.
PYTEST_PASSING = (
    ".................                                                        [100%]\n17 passed in 2.52s\n"
)

# --- synthetic variants ----------------------------------------------------------------------

SYMPY_PASSING = "================== tests finished: 3 passed, in 0.52 seconds ===================\n"
SYMPY_ALL_SKIPPED = "============ tests finished: 0 passed, 2 skipped, in 0.05 seconds ============\n"
DJANGO_ALL_SKIPPED = (
    "System check identified no issues (0 silenced).\n"
    "sss\n"
    "----------------------------------------------------------------------\n"
    "Ran 3 tests in 0.001s\n"
    "\n"
    "OK (skipped=3)\n"
)
DJANGO_ZERO_BUT_BROKEN = (
    "Traceback (most recent call last):\n"
    "ImportError: Failed to import test module: test_x\n"
    "\n"
    "----------------------------------------------------------------------\n"
    "Ran 0 tests in 0.000s\n"
    "\n"
    "FAILED (errors=1)\n"
)


def _run(tmp_path: Path, kind: str, rc: int, out: str) -> tuple[int, str]:
    # A fresh repository per call: a test may call this more than once.
    workdir = tmp_path / f"repo{len(list(tmp_path.glob('repo*')))}"
    workdir.mkdir()
    root, base = _repo(workdir)
    return sx.run(IMAGE, base, kind, ["tests/test_a.py"], cwd=root, runner=_Fake(rc=rc, out=out))


# --- the false greens, now refused -----------------------------------------------------------


def test_a_django_run_that_ran_zero_tests_is_not_green(tmp_path: Path) -> None:
    rc, out = _run(tmp_path, "django", 0, DJANGO_ZERO)

    assert rc == 5, "pytest's code for 'no tests collected'"
    assert out.startswith(DJANGO_ZERO), "the runner's own output is kept, whole and first"
    assert "0 tests" in out[len(DJANGO_ZERO) :], "the model is told why the status changed"


def test_a_sympy_run_that_ran_zero_tests_is_not_green(tmp_path: Path) -> None:
    rc, out = _run(tmp_path, "sympy", 0, SYMPY_ZERO)

    assert rc == 5
    assert out.startswith(SYMPY_ZERO)
    assert "0 tests" in out[len(SYMPY_ZERO) :]


def test_the_note_is_added_once(tmp_path: Path) -> None:
    _, out = _run(tmp_path, "django", 0, DJANGO_ZERO)

    assert out.count("exit status changed") == 1


# --- everything else is left exactly as it was (guards) --------------------------------------


@pytest.mark.parametrize(
    ("kind", "out"),
    [
        ("django", DJANGO_PASSING),
        ("django", DJANGO_ALL_SKIPPED),
        ("sympy", SYMPY_PASSING),
        ("sympy", SYMPY_ALL_SKIPPED),
    ],
    ids=["django-passing(real)", "django-all-skipped", "sympy-passing", "sympy-all-skipped"],
)
def test_a_run_that_executed_tests_stays_green(tmp_path: Path, kind: str, out: str) -> None:
    assert _run(tmp_path, kind, 0, out) == (0, out)


def test_a_non_zero_exit_is_never_changed(tmp_path: Path) -> None:
    """Zero tests *and* a failure (an import error) is a failure already; it must stay exit 1."""
    assert _run(tmp_path, "django", 1, DJANGO_ZERO_BUT_BROKEN) == (1, DJANGO_ZERO_BUT_BROKEN)


def test_a_failing_run_is_never_turned_into_a_pass(tmp_path: Path) -> None:
    out = "Ran 4 tests in 0.1s\n\nFAILED (failures=2)\n"
    assert _run(tmp_path, "django", 1, out) == (1, out)


def test_pytest_repositories_are_not_touched(tmp_path: Path) -> None:
    """pytest already returns 5 itself; its output is never pattern-matched, even if it looks similar."""
    assert _run(tmp_path, "pytest", 5, "no tests ran in 0.01s\n") == (5, "no tests ran in 0.01s\n")
    assert _run(tmp_path, "pytest", 0, DJANGO_ZERO) == (0, DJANGO_ZERO)


def test_the_harness_failure_codes_pass_through(tmp_path: Path) -> None:
    for code in (97, 98):
        assert _run(tmp_path, "django", code, DJANGO_ZERO) == (code, DJANGO_ZERO)


def test_an_empty_output_with_a_zero_exit_is_left_alone(tmp_path: Path) -> None:
    """No statement from the runner is not 'the runner said it ran nothing', for any kind."""
    for kind in ("django", "sympy", "pytest"):
        assert _run(tmp_path, kind, 0, "") == (0, "")


# --- what counts as "the runner says it ran nothing" -------------------------------------------


def test_a_real_passing_pytest_run_is_untouched(tmp_path: Path) -> None:
    assert _run(tmp_path, "pytest", 0, PYTEST_PASSING) == (0, PYTEST_PASSING)


def test_text_that_merely_contains_the_phrase_is_not_the_runners_summary(tmp_path: Path) -> None:
    """Mid-line, indented or longer-word `Ran 0 tests` is not the runner's own summary line.

    The stray text comes AFTER the real summary, where it would decide if the line anchor were missing,
    and the longer word stands alone, where it would decide if the word boundary were missing.
    """
    for out in (
        "Ran 3 tests in 0.1s\n\nOK\nfoo Ran 0 tests\n",
        "Ran 3 tests in 0.1s\n\nOK\n  Ran 0 tests\n",
        "Ran 0 testsuite\n",
    ):
        assert _run(tmp_path, "django", 0, out) == (0, out)


def test_only_the_runners_final_summary_decides(tmp_path: Path) -> None:
    """A test that prints a nested run's `Ran 0 tests` at the start of a line must not turn a real run
    into a failure: the runner's own summary is the last such line."""
    nested_then_real = "Ran 0 tests in 0.000s\n\nOK\n...\nRan 5 tests in 0.1s\n\nOK\n"
    assert _run(tmp_path, "django", 0, nested_then_real) == (0, nested_then_real)
    # ... and the other way round: a final summary of zero is zero, whatever a nested run printed before.
    real_then_zero = "Ran 5 tests in 0.1s\n\nOK\n...\nRan 0 tests in 0.000s\n\nOK\n"
    rc, out = _run(tmp_path, "django", 0, real_then_zero)
    assert rc == 5 and out.startswith(real_then_zero)


def test_only_sympys_final_summary_decides(tmp_path: Path) -> None:
    nested = "tests finished: 0 passed, in 0.00 seconds\n...\n" + SYMPY_PASSING
    assert _run(tmp_path, "sympy", 0, nested) == (0, nested)
    rc, out = _run(tmp_path, "sympy", 0, SYMPY_PASSING + "...\n" + SYMPY_ZERO)
    assert rc == 5 and out.startswith(SYMPY_PASSING)


def test_one_test_is_still_a_test(tmp_path: Path) -> None:
    out = "Ran 1 test in 0.001s\n\nOK\n"
    assert _run(tmp_path, "django", 0, out) == (0, out)


def test_a_timeout_is_never_rewritten(tmp_path: Path) -> None:
    assert _run(tmp_path, "django", 124, DJANGO_ZERO) == (124, DJANGO_ZERO)


def test_main_exits_5_and_prints_the_note_for_a_zero_test_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command line entry point, with only the `docker` call faked, carries the changed status."""
    root, base = _repo(tmp_path)
    monkeypatch.chdir(root)
    real = subprocess.run

    def fake(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv and argv[0] == "docker":
            return subprocess.CompletedProcess(argv, 0, stdout=DJANGO_ZERO.encode(), stderr=b"")
        return real(argv, *args, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(subprocess, "run", fake)

    code = sx.main(["--image", IMAGE, "--base", base, "--kind", "django", "tests/test_a.py"])

    assert code == 5
    printed = capsys.readouterr().out
    assert printed.startswith(DJANGO_ZERO) and "exit status changed" in printed
