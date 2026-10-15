"""What counts as "the same failure" (SSPN-164, B80).

The rule is only safe if it is strict: it may stop a run earlier, and it must never call a run that is
converging a stalled one. So the volatile parts of a runner's output are removed, nothing else is, and
the failing test ids are compared too where the runner names them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.refine_progress import (
    StallTracker,
    enabled,
    failure_signature,
    normalise_output,
)

# A real failing run from a recorded SWE-bench pilot (Django on Python 3.6): the test asked for
# `subprocess.run(text=True)`, which 3.6 does not have, and refine never got it to pass.
_RECORDED = Path(__file__).parent.parent / "fixtures" / "refine_progress" / "django_text_kwarg.txt"


class _Result:
    def __init__(self, output: str, problems: tuple[str, ...] = ()) -> None:
        self.output = output
        self.problems = problems


def test_durations_are_removed() -> None:
    assert normalise_output("Ran 8 tests in 0.006s") == normalise_output("Ran 8 tests in 0.368s")
    assert normalise_output("1 failed in 0.12s") == normalise_output("1 failed in 3.40s")


def test_addresses_temp_paths_and_pids_are_removed() -> None:
    a = "<Foo object at 0x7f3a2c1b>\nFileNotFoundError: /tmp/pytest-of-u/pytest-3/test_x0/f.txt\npid 4121"
    b = "<Foo object at 0x10a9d8e0>\nFileNotFoundError: /tmp/pytest-of-u/pytest-9/test_x0/f.txt\npid 98"
    assert normalise_output(a) == normalise_output(b)
    macos = "open /private/var/folders/ab/cd/T/tmpq1w2e3/x failed"
    assert "tmpq1w2e3" not in normalise_output(macos)


def test_nothing_that_can_tell_two_failures_apart_is_removed() -> None:
    assert normalise_output("E   assert 1 == 2") != normalise_output("E   assert 1 == 3")
    assert normalise_output("FAILED tests/a.py::test_x") != normalise_output("FAILED tests/a.py::test_y")
    assert normalise_output("line 12, in f") != normalise_output("line 13, in f")


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


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF "])
def test_the_off_switch(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SDLC_REFINE_PROGRESS", value)
    assert enabled() is False


def test_it_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_REFINE_PROGRESS", raising=False)
    assert enabled() is True
    monkeypatch.setenv("SDLC_REFINE_PROGRESS", "1")
    assert enabled() is True


def test_a_green_run_starts_the_count_again() -> None:
    """A failure that comes back after a green run is a new failure, even if it reads the same."""
    tracker = StallTracker()
    tracker.observe("tests", "s1")
    tracker.observe("tests", "s1")
    tracker.reset("tests")
    assert tracker.observe("tests", "s1") == 0 and not tracker.stalled("tests")


def test_a_number_that_is_the_point_of_an_assertion_is_not_removed() -> None:
    """Only durations a runner reports are removed. A test that asserts on a time is a different test
    when the numbers differ."""
    assert normalise_output("E   assert 1.5s == 2.5s") != normalise_output("E   assert 2.5s == 3.5s")
    assert normalise_output("E   assert took == 12 seconds") != normalise_output(
        "E   assert took == 13 seconds"
    )


def test_go_and_jest_style_durations_are_removed() -> None:
    go_a = "--- FAIL: TestX (0.00s)\nFAIL\nFAIL\tgithub.com/acme/pkg\t0.123s"
    go_b = "--- FAIL: TestX (0.02s)\nFAIL\nFAIL\tgithub.com/acme/pkg\t0.456s"
    assert normalise_output(go_a) == normalise_output(go_b)
    assert normalise_output("ok  \tgithub.com/acme/pkg\t0.123s") == normalise_output(
        "ok  \tgithub.com/acme/pkg\t9.870s"
    )
    assert normalise_output("  x does a thing (12 ms)\nTime: 1.2 s") == normalise_output(
        "  x does a thing (340 ms)\nTime: 8.9 s"
    )


def test_pytest_duration_lines_are_removed() -> None:
    assert normalise_output("0.01s call     tests/a.py::test_x") == normalise_output(
        "0.30s call     tests/a.py::test_x"
    )
