"""B99 / SSPN-234: reading sympy's own test runner (`bin/test`), which is neither pytest nor unittest.

The outputs are the last stored test runs of R2 (SSPN-222) instances, trimmed to the lines that matter. In
five of five sympy runs that ended red `failing_test_names` returned nothing and `executed_tests` returned
`None`, so the rescue answered "the output names no failing test" for a failure the output states plainly.
"""

from __future__ import annotations

import pytest

from orchestrator.sdlc import own_tests

# sympy-24066, run-tests-6.txt: one test of seven fails.
_ONE_FAILED = """\
============================= test process starts ==============================
executable:         /opt/miniconda3/envs/testbed/bin/python  (3.9.20-final-0) [CPython]
random seed:        76678195

sympy/physics/units/tests/test_dimensionless_function_args.py[7]
test_rc_time_constant_expression_is_dimensionless ok
test_exp_of_dimensionless_argument_added_to_number ok
test_exp_of_dimensionless_argument_alone ok
test_other_functions_with_dimensionless_arguments ok
test_function_with_dimensional_argument_keeps_dimension ok
test_non_function_branches_unchanged F
test_add_with_mismatched_dimensions_still_raises ok                       [FAIL]


________________________________________________________________________________
 sympy/physics/units/tests/test_dimensionless_function_args.py:test_non_function_branches_unchanged
Traceback (most recent call last):
  File "/testbed/sympy/physics/units/tests/test_dimensionless_function_args.py", line 65, in test_x
    assert dim == Dimension(time)
AssertionError

============= tests finished: 6 passed, 1 failed, in 0.31 seconds ==============
DO *NOT* COMMIT!
"""
_FILE_ONE = "sympy/physics/units/tests/test_dimensionless_function_args.py"
_NODE_ONE = f"{_FILE_ONE}::test_non_function_branches_unchanged"

# sympy-14248, run-tests-6.txt: two fail; the block header is padded with underscores.
_TWO_FAILED = """\
sympy/printing/tests/test_matadd_minus_printing.py[14]
test_pretty_matadd_minus F
test_pretty_matadd_unicode_minus F
test_printers_agree_on_sign_structure ok                                  [FAIL]


________________________________________________________________________________
_ sympy/printing/tests/test_matadd_minus_printing.py:test_pretty_matadd_minus __
Traceback (most recent call last):
AssertionError
________________________________________________________________________________
 sympy/printing/tests/test_matadd_minus_printing.py:test_pretty_matadd_unicode_minus
Traceback (most recent call last):
AssertionError

============= tests finished: 12 passed, 2 failed, in 0.06 seconds =============
"""
_FILE_TWO = "sympy/printing/tests/test_matadd_minus_printing.py"

# sympy-15875, run-tests-6.txt: a test the run did not write fails, and three are expected to fail.
_PREEXISTING = """\
sympy/core/tests/test_arit.py[85]
test_Add_is_zero F
test_divmod ok                                                            [FAIL]


________________________________________________________________________________
________________ sympy/core/tests/test_arit.py:test_Add_is_zero ________________
Traceback (most recent call last):
AssertionError

=== tests finished: 81 passed, 1 failed, 3 expected to fail, in 8.28 seconds ===
"""

# sympy-14248, run-tests-1.txt: nothing was collected.
_NOTHING = """\
============================= test process starts ==============================


================== tests finished: 0 passed, in 0.00 seconds ===================
"""

_AUTHORED_ONE = {_FILE_ONE: "def test_non_function_branches_unchanged():\n    pass\n"}


def test_a_failed_sympy_test_is_named_from_its_block_header() -> None:
    assert own_tests.failing_test_names(_ONE_FAILED) == (_NODE_ONE,)


def test_every_failed_sympy_test_is_named_once_in_order() -> None:
    assert own_tests.failing_test_names(_TWO_FAILED) == (
        f"{_FILE_TWO}::test_pretty_matadd_minus",
        f"{_FILE_TWO}::test_pretty_matadd_unicode_minus",
    )


def test_an_errored_sympy_test_without_a_block_header_is_named_from_its_line() -> None:
    """`E` is a test that raised; if the header were cut off the per-file line still names it."""
    output = (
        "=== test process starts ===\n\nsympy/a/tests/test_x.py[2] \n"
        "test_one ok\ntest_two E                                 [FAIL]\n"
    )

    assert own_tests.failing_test_names(output) == ("sympy/a/tests/test_x.py::test_two",)


def test_the_count_is_read_from_the_tests_finished_line() -> None:
    assert own_tests.executed_tests(_ONE_FAILED) == 7
    assert own_tests.executed_tests(_TWO_FAILED) == 14
    assert own_tests.executed_tests(_PREEXISTING) == 85  # an expected failure ran
    assert own_tests.executed_tests(_NOTHING) == 0


def test_exceptions_and_skips_are_counted_the_way_the_runner_reports_them() -> None:
    output = "=== tests finished: 5 passed, 1 failed, 2 skipped, 1 exceptions, in 0.50 seconds ===\n"

    assert own_tests.executed_tests(output) == 7  # a skip did not run; an exception did


def test_a_sympy_run_whose_only_red_test_is_the_runs_own_is_rescuable() -> None:
    verdict = own_tests.classify(output=_ONE_FAILED, problems=(), authored=_AUTHORED_ONE, own_test_count=6)

    assert verdict.own_only and verdict.rescuable and verdict.ran
    assert verdict.files == (_FILE_ONE,)
    assert verdict.existing_executed  # 7 ran and 6 were the run's: one existing test ran alongside


def test_a_sympy_failure_in_a_test_the_run_did_not_write_is_still_refused() -> None:
    """sympy-15875: `test_Add_is_zero` is pre-existing, so the rescue must keep refusing it (monotone)."""
    verdict = own_tests.classify(output=_PREEXISTING, problems=(), authored=_AUTHORED_ONE, own_test_count=1)

    assert not verdict.own_only and not verdict.rescuable
    assert "did not write" in verdict.reason


def test_a_sympy_run_that_collected_nothing_names_nothing_and_is_never_rescued() -> None:
    assert own_tests.failing_test_names(_NOTHING) == ()
    verdict = own_tests.classify(output=_NOTHING, problems=(), authored=_AUTHORED_ONE, own_test_count=1)

    assert not verdict.rescuable
    assert "names no failing test" in verdict.reason


@pytest.mark.parametrize(
    "plain",
    [
        "FAILED tests/test_a.py::test_x - assert 1 == 2\n=== 1 failed, 2 passed in 0.10s ===\n",
        "FAIL: test_x (pkg.tests.T)\n\nRan 3 tests in 0.01s\n\nFAILED (failures=1)\n",
    ],
    ids=["pytest", "unittest"],
)
def test_the_other_runners_are_read_as_before(plain: str) -> None:
    """The sympy reader must not claim a name or a count in output that is not sympy's."""
    assert not any("::test_" in n and n.startswith("sympy") for n in own_tests.failing_test_names(plain))
    assert own_tests.executed_tests(plain) in (3,)


def test_a_line_that_merely_ends_in_f_is_not_a_failed_test() -> None:
    """`name F` is only a test line under a sympy file header; prose and code lines that end in F are not."""
    output = "Traceback (most recent call last):\n  x = compute_value F\nThe suite finished F\n"

    assert own_tests.failing_test_names(output) == ()


def test_a_failure_block_alone_names_the_test_when_the_per_file_lines_are_missing() -> None:
    """A long run's file lines can be cut off above the failure blocks; the blocks are the primary source."""
    output = (
        "=== test process starts ===\n\n"
        "_ sympy/a/tests/test_x.py:test_two __\nTraceback (most recent call last):\nAssertionError\n\n"
        "=== tests finished: 1 passed, 1 failed, in 0.10 seconds ===\n"
    )

    assert own_tests.failing_test_names(output) == ("sympy/a/tests/test_x.py::test_two",)


def test_a_test_line_before_any_file_header_is_not_attributed_to_a_file() -> None:
    output = (
        "=== test process starts ===\n\ntest_orphan F\n"
        "=== tests finished: 0 passed, 1 failed, in 0.1 seconds ===\n"
    )

    assert own_tests.failing_test_names(output) == ()


def test_a_path_and_test_name_in_output_that_is_not_sympys_names_nothing() -> None:
    """The block shape alone (`path.py:test_x`) also appears in other tools' output; without the runner's own
    opening or closing line it is not read as a failure."""
    output = "tests/test_a.py:test_x\ntests/test_a.py:test_y F\n"

    assert own_tests.failing_test_names(output) == ()
