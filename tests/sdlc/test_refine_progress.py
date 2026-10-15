"""What counts as "the same failure" (SSPN-164, B80).

The rule is only safe if it is strict: it may stop a run earlier, and it must never call a run that is
converging a stalled one. So only the parts of an output that are never the same twice are removed, and only
in the exact line shapes a runner prints them in; the failing test ids are compared too where the runner
names them; and a run whose output cannot show that nothing changed is not counted at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc import testrunner
from orchestrator.sdlc.refine_progress import (
    RUNNER_OUTPUT_LIMIT,
    StallTracker,
    comparable,
    enabled,
    failure_signature,
    normalise_output,
)

# A real failing run from a recorded SWE-bench pilot (Django on Python 3.6): the test asked for
# `subprocess.run(text=True)`, which 3.6 does not have, and refine never got it to pass.
_RECORDED = Path(__file__).parent.parent / "fixtures" / "refine_progress" / "django_text_kwarg.txt"


class _Result:
    def __init__(self, output: str, problems: tuple[str, ...] = (), returncode: int = 1) -> None:
        self.output = output
        self.problems = problems
        self.returncode = returncode


# ---- what is removed: only what a runner prints, only where it prints it ---------------------------------


def test_a_unittest_duration_is_removed() -> None:
    assert normalise_output("Ran 8 tests in 0.006s") == normalise_output("Ran 8 tests in 0.368s")


def test_a_pytest_summary_duration_is_removed() -> None:
    for a, b in [
        ("1 failed in 0.12s", "1 failed in 3.40s"),
        (
            "=== 1 failed, 2 passed, 1 warning in 0.12s (0:00:01) ===",
            "=== 1 failed, 2 passed, 1 warning in 9.80s (0:00:09) ===",
        ),
        ("no tests ran in 0.01s", "no tests ran in 0.50s"),
        ("0.01s call     tests/a.py::test_x", "0.30s call     tests/a.py::test_x"),
    ]:
        assert normalise_output(a) == normalise_output(b), (a, b)


def test_go_and_jest_durations_are_removed() -> None:
    go_a = "--- FAIL: TestX (0.00s)\nFAIL\nFAIL\tgithub.com/acme/pkg\t0.123s"
    go_b = "--- FAIL: TestX (0.02s)\nFAIL\nFAIL\tgithub.com/acme/pkg\t0.456s"
    assert normalise_output(go_a) == normalise_output(go_b)
    assert normalise_output("ok  \tgithub.com/acme/pkg\t0.123s") == normalise_output(
        "ok  \tgithub.com/acme/pkg\t9.870s"
    )
    jest_a = "  ✓ does a thing (12 ms)\nTime:        1.2 s"
    jest_b = "  ✓ does a thing (340 ms)\nTime:        8.9 s"
    assert normalise_output(jest_a) == normalise_output(jest_b)


def test_an_object_address_after_at_is_removed() -> None:
    a = "<Foo object at 0x7f3a2c1b> and <function f at 0x10a9d8e0>"
    b = "<Foo object at 0x10a9d8e0> and <function f at 0x7f3a2c1b>"
    assert normalise_output(a) == normalise_output(b)


def test_the_unique_part_of_a_temporary_directory_is_removed() -> None:
    a = "FileNotFoundError: /tmp/pytest-of-u/pytest-3/test_x0/f.txt"
    b = "FileNotFoundError: /tmp/pytest-of-u/pytest-9/test_x0/f.txt"
    assert normalise_output(a) == normalise_output(b)
    assert normalise_output("open /private/var/folders/ab/cd/T/tmpq1w2e3r4/x failed") == normalise_output(
        "open /private/var/folders/zz/yy/T/tmp9z8y7x6w/x failed"
    )
    win_a = r"C:\Users\u\AppData\Local\Temp\pytest-of-u\pytest-3\t0\f.txt"
    win_b = r"C:\Users\u\AppData\Local\Temp\pytest-of-u\pytest-9\t0\f.txt"
    assert normalise_output(win_a) == normalise_output(win_b)


# ---- what must NOT be removed: it is what an assertion is about ------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # a duration that is part of the thing being asserted, not a runner's timing line
        (
            "E   assert 'cache expires in 30s' == 'cache expires in 60s'",
            "E   assert 'cache expires in 45s' == 'cache expires in 60s'",
        ),
        ("E   assert 'budget (200 ms)' == ''", "E   assert 'budget (210 ms)' == ''"),
        ("E   assert 1.5s == 2.5s", "E   assert 2.5s == 3.5s"),
        ("Time: 5 s", "Time: 7 s"),
        ("  does a thing (12 ms)", "  does a thing (13 ms)"),
        ("Ran 8 tests in 0.006s extra", "Ran 8 tests in 0.007s extra"),
        # a number that happens to look like an address or a pid
        ("E   assert color == 0xff8800", "E   assert color == 0xff8802"),
        ("worker pid 4", "worker pid 9"),
        # paths: what they point at tells two failures apart
        ("E   assert '/tmp/out_a.txt' == ''", "E   assert '/tmp/out_b.txt' == ''"),
        ("src/tmp/a.py:3: in f", "src/tmp/b.py:3: in f"),
        ("/tmp/pytest-of-u/pytest-3/test_a0/f.txt", "/tmp/pytest-of-u/pytest-3/test_b0/f.txt"),
        # ordinary assertion text
        ("E   assert 1 == 2", "E   assert 1 == 3"),
        ("FAILED tests/a.py::test_x", "FAILED tests/a.py::test_y"),
        ("line 12, in f", "line 13, in f"),
    ],
)
def test_text_that_tells_two_failures_apart_is_kept(a: str, b: str) -> None:
    assert normalise_output(a) != normalise_output(b)


# ---- the signature -----------------------------------------------------------------------------------------


def test_the_same_failure_has_the_same_signature() -> None:
    assert failure_signature(_Result("E   assert 1 == 2")) == failure_signature(_Result("E   assert 1 == 2"))


def test_a_different_assertion_is_a_different_signature() -> None:
    assert failure_signature(_Result("E   assert 1 == 2")) != failure_signature(_Result("E   assert 2 == 2"))


def test_the_failing_ids_are_part_of_the_signature_where_the_runner_names_them() -> None:
    same_text_a = _Result("2 failed", problems=("t.py::test_a",))
    same_text_b = _Result("2 failed", problems=("t.py::test_b",))
    assert failure_signature(same_text_a) != failure_signature(same_text_b)


def test_the_order_the_ids_are_listed_in_does_not_matter() -> None:
    forward = _Result("2 failed", problems=("t.py::test_a", "t.py::test_b"))
    backward = _Result("2 failed", problems=("t.py::test_b", "t.py::test_a"))
    assert failure_signature(forward) == failure_signature(backward)


def test_a_runner_that_names_nothing_still_has_a_signature() -> None:
    """Most runners (`SDLC_TEST_COMMAND`, Maven, Node, Go) leave `problems` empty or absent."""

    class _Bare:
        output = "boom"

    assert failure_signature(_Bare()) == failure_signature(_Result("boom"))


def test_a_recorded_failure_is_stable_across_a_different_duration() -> None:
    text = _RECORDED.read_text(encoding="utf-8")
    assert "Ran 8 tests in 0.006s" in text
    other = text.replace("Ran 8 tests in 0.006s", "Ran 8 tests in 0.011s")
    assert failure_signature(_Result(text)) == failure_signature(_Result(other))


def test_a_recorded_failure_is_not_the_same_as_the_run_that_passed() -> None:
    text = _RECORDED.read_text(encoding="utf-8")
    green = "Testing against Django installed in '/testbed/django'\n........\nRan 8 tests in 0.368s\n\nOK\n"
    assert failure_signature(_Result(text)) != failure_signature(_Result(green))


# ---- a run that cannot show that nothing changed is not counted ----------


def test_the_limit_is_the_one_the_runners_clip_to() -> None:
    """If a runner's limit moves, the check that a clipped output is not compared must move with it."""
    assert RUNNER_OUTPUT_LIMIT == testrunner._MAX_OUTPUT_CHARS


def test_an_ordinary_failing_run_is_comparable() -> None:
    assert comparable(_Result("E   assert 1 == 2"))


@pytest.mark.parametrize(
    "message", ["test run timed out", "maven test run timed out", "node test run timed out"]
)
def test_a_run_that_timed_out_is_not_comparable(message: str) -> None:
    """Every runner reports a timeout with its own fixed text, so two of them read alike whatever the
    edits did, and a hang is something refine can fix."""
    assert not comparable(_Result(message, returncode=-1))


def test_an_output_clipped_to_the_runner_limit_is_not_comparable() -> None:
    """The runner keeps the last N characters: an earlier failure may have been fixed out of sight."""
    assert not comparable(_Result("x" * RUNNER_OUTPUT_LIMIT))
    assert comparable(_Result("x" * (RUNNER_OUTPUT_LIMIT - 1)))


def test_a_result_with_no_returncode_or_output_does_not_raise() -> None:
    class _Bare:
        pass

    assert comparable(_Bare()) is True


# ---- the tracker ----------------------------------------


def test_two_repeats_in_a_row_are_a_stall() -> None:
    tracker = StallTracker()
    assert tracker.observe("tests", "s1") == 0 and not tracker.stalled("tests")  # first sighting
    assert tracker.observe("tests", "s1") == 1 and not tracker.stalled("tests")  # one repeat
    assert tracker.observe("tests", "s1") == 2 and tracker.stalled("tests")  # two in a row


def test_a_different_failure_starts_the_count_again() -> None:
    tracker = StallTracker()
    tracker.observe("tests", "s1")
    tracker.observe("tests", "s1")
    assert tracker.observe("tests", "s2") == 0 and not tracker.stalled("tests")
    assert tracker.observe("tests", "s2") == 1


def test_each_kind_is_counted_on_its_own() -> None:
    tracker = StallTracker()
    tracker.observe("tests", "s1")
    tracker.observe("tests", "s1")
    tracker.observe("types", "s1")
    assert tracker.observe("tests", "s1") == 2 and tracker.stalled("tests")
    assert not tracker.stalled("types")


def test_a_green_run_starts_the_count_again() -> None:
    """A failure that comes back after a green run is a new failure, even if it reads the same."""
    tracker = StallTracker()
    tracker.observe("tests", "s1")
    tracker.observe("tests", "s1")
    tracker.reset("tests")
    assert tracker.observe("tests", "s1") == 0 and not tracker.stalled("tests")


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF "])
def test_the_off_switch(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SDLC_REFINE_PROGRESS", value)
    assert enabled() is False


def test_it_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_REFINE_PROGRESS", raising=False)
    assert enabled() is True
    monkeypatch.setenv("SDLC_REFINE_PROGRESS", "1")
    assert enabled() is True
