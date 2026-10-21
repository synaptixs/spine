"""SSPN-227: telling the model's own failing tests apart from everything else a runner reports.

The inputs are real shapes: the unittest lines are what Django's runner printed in the P6 runs
(django-14155, django-14349), the pytest lines are what pytest and scikit-learn-11578 print. Nothing
here runs a test; the module under test reads text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc import own_tests

_UNITTEST_FAIL = """\
Testing against Django installed in '/testbed/django'
Found 8 tests.
.......F
======================================================================
FAIL: test_func_attribute_keeps_the_original_partial (test_partial_repr.PartialReprTests)
----------------------------------------------------------------------
AssertionError: Tuples differ: ((None, 'runtime'), {'name': 'value'}) != (('bound', 'runtime'), {})

----------------------------------------------------------------------
Ran 8 tests in 0.002s

FAILED (failures=1)
"""

_UNITTEST_IMPORT_FAILURE = """\
Found 21 tests.
E....................
======================================================================
ERROR: test_url_validator_unsafe_chars (unittest.loader._FailedTest)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_url_validator_unsafe_chars
ModuleNotFoundError: No module named 'pytest'

----------------------------------------------------------------------
Ran 21 tests in 0.065s

FAILED (errors=1)
"""

_PYTEST_FAIL = """\
=========================== short test summary info ============================
FAILED tests/test_scores.py::test_scores_match_predict_proba - IndexError: too many indices
========================= 1 failed, 5 passed, 1 warning in 0.80s ==============
"""

_AUTHORED_UNITTEST = {
    "tests/test_partial_repr.py": (
        "class PartialReprTests(SimpleTestCase):\n"
        "    def test_repr_of_partial(self):\n        pass\n\n"
        "    def test_func_attribute_keeps_the_original_partial(self):\n        pass\n"
    )
}


def test_a_unittest_failure_line_names_the_test() -> None:
    assert own_tests.failing_test_names(_UNITTEST_FAIL) == ("test_func_attribute_keeps_the_original_partial",)


def test_a_unittest_import_failure_names_the_module_it_could_not_import() -> None:
    assert own_tests.failing_test_names(_UNITTEST_IMPORT_FAILURE) == ("test_url_validator_unsafe_chars",)


def test_pytest_names_come_from_the_runners_problems_when_it_has_them() -> None:
    names = own_tests.failing_test_names(
        "", problems=("tests/test_scores.py::test_scores_match_predict_proba",)
    )

    assert names == ("tests/test_scores.py::test_scores_match_predict_proba",)


def test_pytest_names_are_read_from_the_summary_when_the_runner_gave_none() -> None:
    names = own_tests.failing_test_names(_PYTEST_FAIL)

    assert names == ("tests/test_scores.py::test_scores_match_predict_proba",)


def test_output_that_names_nothing_names_nothing() -> None:
    assert own_tests.failing_test_names("E   assert 1 == 2") == ()


@pytest.mark.parametrize(
    ("output", "executed"),
    [
        (_UNITTEST_FAIL, 8),
        (_UNITTEST_IMPORT_FAILURE, 21),
        (_PYTEST_FAIL, 6),  # 1 failed + 5 passed; a warning is not a test
        ("Ran 0 tests in 0.000s\n\nNO TESTS RAN", 0),
        ("something no runner prints", None),
    ],
)
def test_how_many_tests_a_run_executed(output: str, executed: int | None) -> None:
    assert own_tests.executed_tests(output) == executed


def test_a_failure_in_a_test_the_run_wrote_is_its_own() -> None:
    verdict = own_tests.classify(
        output=_UNITTEST_FAIL, problems=(), authored=_AUTHORED_UNITTEST, own_test_count=2
    )

    assert verdict.own_only
    assert verdict.files == ("tests/test_partial_repr.py",)
    assert verdict.names == ("test_func_attribute_keeps_the_original_partial",)
    assert verdict.existing_executed  # 8 ran, 2 of them the run's own
    assert verdict.rescuable


def test_a_module_the_run_wrote_that_cannot_be_imported_is_its_own() -> None:
    authored = {"tests/test_url_validator_unsafe_chars.py": "import pytest\n\ndef test_it():\n    pass\n"}

    verdict = own_tests.classify(
        output=_UNITTEST_IMPORT_FAILURE, problems=(), authored=authored, own_test_count=1
    )

    assert verdict.own_only
    assert verdict.files == ("tests/test_url_validator_unsafe_chars.py",)
    assert verdict.rescuable  # 21 ran, one of them the run's own


def test_a_pytest_node_id_in_a_file_the_run_wrote_is_its_own() -> None:
    authored = {"tests/test_scores.py": "def test_scores_match_predict_proba():\n    pass\n"}

    verdict = own_tests.classify(output=_PYTEST_FAIL, problems=(), authored=authored, own_test_count=1)

    assert verdict.own_only and verdict.files == ("tests/test_scores.py",)
    assert verdict.rescuable


def test_one_failing_test_the_run_did_not_write_makes_the_failure_not_its_own() -> None:
    own_line = next(
        ln for ln in _UNITTEST_FAIL.splitlines() if ln.startswith("FAIL:")
    )  # the FAIL line of the run's own test
    output = _UNITTEST_FAIL.replace(
        own_line, own_line + "\nFAIL: test_resolve_a_named_url (urlpatterns_reverse.tests.ResolverTests)"
    )

    verdict = own_tests.classify(output=output, problems=(), authored=_AUTHORED_UNITTEST, own_test_count=2)

    assert not verdict.own_only and not verdict.rescuable
    assert "test_resolve_a_named_url" in verdict.reason


def test_a_name_found_only_in_production_code_is_not_the_runs_own_test() -> None:
    # `authored` is every path the run wrote, production included; only a test path may vouch for a name.
    authored = {"src/helpers.py": "def test_func_attribute_keeps_the_original_partial():\n    pass\n"}

    verdict = own_tests.classify(output=_UNITTEST_FAIL, problems=(), authored=authored, own_test_count=1)

    assert not verdict.own_only
    assert verdict.files == ()


def test_output_that_names_no_failing_test_is_never_its_own() -> None:
    verdict = own_tests.classify(
        output="E   assert 1 == 2\n1 failed in 0.01s",
        problems=(),
        authored={"tests/test_x.py": "def test_x():\n    pass\n"},
        own_test_count=1,
    )

    assert not verdict.own_only and not verdict.rescuable
    assert "names no failing test" in verdict.reason


def test_a_run_in_which_only_the_runs_own_tests_executed_is_rescuable_but_not_supported() -> None:
    """D10: the benchmark's command runs only the files the run wrote, so no pre-existing test ran at all.
    That is no reason to refuse (7 of the 11 such stored runs resolved); it is a reason to say so."""
    output = _UNITTEST_FAIL.replace("Ran 8 tests", "Ran 2 tests")

    verdict = own_tests.classify(output=output, problems=(), authored=_AUTHORED_UNITTEST, own_test_count=2)

    assert verdict.own_only and verdict.rescuable
    assert not verdict.existing_executed  # the label reads this: nothing existing supports the change
    assert verdict.reason == ""


def test_strict_mode_restores_the_rule_that_an_existing_test_must_have_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SDLC_OWN_TEST_RESCUE", "strict")
    output = _UNITTEST_FAIL.replace("Ran 8 tests", "Ran 2 tests")

    verdict = own_tests.classify(output=output, problems=(), authored=_AUTHORED_UNITTEST, own_test_count=2)

    assert own_tests.enabled() and own_tests.strict()
    assert verdict.own_only and not verdict.rescuable
    assert "no existing test" in verdict.reason


def test_strict_is_not_the_default_and_zero_is_still_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_OWN_TEST_RESCUE", raising=False)
    assert not own_tests.strict()
    monkeypatch.setenv("SDLC_OWN_TEST_RESCUE", "0")
    assert not own_tests.enabled() and not own_tests.strict()


@pytest.mark.parametrize("output", ["Ran 0 tests in 0.000s\n\nNO TESTS RAN", "no summary line at all"])
def test_a_run_that_executed_nothing_or_cannot_say_is_never_rescuable(output: str) -> None:
    verdict = own_tests.classify(
        output=_UNITTEST_FAIL.split("Ran 8 tests")[0] + output,
        problems=(),
        authored=_AUTHORED_UNITTEST,
        own_test_count=2,
    )

    assert not verdict.rescuable
    assert "ran" in verdict.reason


def test_the_switch_is_on_by_default_and_off_with_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_OWN_TEST_RESCUE", raising=False)
    assert own_tests.enabled()
    monkeypatch.setenv("SDLC_OWN_TEST_RESCUE", "0")
    assert not own_tests.enabled()
    monkeypatch.setenv("SDLC_OWN_TEST_RESCUE", "1")
    assert own_tests.enabled()


# ---- the two defects the offline check on the 14 stored class-O runs found (SSPN-227) -------------------
#
# `classify` named the failing tests correctly on all 14 real outputs, yet rescued none: it was told the run
# wrote every test in every test file it touched, and a module that could not be imported was matched to a
# same-named method in another file. The shapes below are the stored ones (django-14349, 10973, 13925); the
# counts are the tests each run ADDED, measured against HEAD.


def test_a_new_file_adds_every_test_it_defines() -> None:
    assert own_tests.added_test_functions("def test_a():\n    pass\n\ndef test_b():\n    pass\n") == 2


def test_an_edited_file_adds_only_the_tests_that_were_not_there() -> None:
    before = "class T:\n    def test_old_a(self): ...\n    def test_old_b(self): ...\n"
    after = before + "    def test_new(self): ...\n"

    assert own_tests.added_test_functions(after, before) == 1


@pytest.mark.parametrize(
    ("after", "before", "added"),
    [
        ("def test_a(): ...\n", "def test_a(): ...\n", 0),  # untouched
        ("def test_a(): ...\n", "def test_a(): ...\ndef test_b(): ...\n", 0),  # tests only removed
        ("def test_c(): ...\n", "def test_a(): ...\n", 1),  # renamed: one new name
        ("def test_a(): ...\ndef test_a(): ...\n", "def test_a(): ...\n", 1),  # same name added again
        ("x = 1\n", "", 0),  # a file with no tests
    ],
)
def test_added_tests_never_go_negative_and_count_repeats(after: str, before: str, added: int) -> None:
    assert own_tests.added_test_functions(after, before) == added


_FAILED_MODULE_14349 = """\
Testing against Django installed in '/testbed/django'
Found 21 tests.
System check identified no issues (0 silenced).
E....................
======================================================================
ERROR: test_url_validator_unsafe_chars (unittest.loader._FailedTest)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_url_validator_unsafe_chars
Traceback (most recent call last):
  File "/testbed/tests/test_url_validator_unsafe_chars.py", line 11, in <module>
    import pytest
ModuleNotFoundError: No module named 'pytest'

----------------------------------------------------------------------
Ran 21 tests in 0.065s

FAILED (errors=1)
"""

# django-14349: the model also added a method with the module's name to an existing file.
_AUTHORED_14349 = {
    "tests/test_url_validator_unsafe_chars.py": "import pytest\n\n\ndef test_rejects_newline():\n    pass\n",
    "tests/validators/tests.py": (
        "class TestValidators(SimpleTestCase):\n"
        "    def test_url_validator_unsafe_chars(self):\n        pass\n"
    ),
}


def test_a_module_that_cannot_be_imported_is_matched_to_the_file_of_that_name_before_any_method() -> None:
    verdict = own_tests.classify(
        output=_FAILED_MODULE_14349, problems=(), authored=_AUTHORED_14349, own_test_count=9
    )

    assert verdict.own_only
    assert verdict.files == ("tests/test_url_validator_unsafe_chars.py",)
    assert verdict.rescuable  # 21 ran, 9 of them added by the run


def test_a_module_that_cannot_be_imported_is_not_vouched_for_by_a_same_named_method() -> None:
    # No file the run wrote has that name: the module is not the run's, whatever a method elsewhere is called.
    authored = {"tests/validators/tests.py": _AUTHORED_14349["tests/validators/tests.py"]}

    verdict = own_tests.classify(
        output=_FAILED_MODULE_14349, problems=(), authored=authored, own_test_count=1
    )

    assert not verdict.own_only and not verdict.rescuable
    assert verdict.files == ()


def test_a_module_is_matched_by_its_file_name_exactly_not_by_a_part_of_it() -> None:
    authored = {"tests/test_url_validator_unsafe_chars_extra.py": "def test_x():\n    pass\n"}

    verdict = own_tests.classify(
        output=_FAILED_MODULE_14349, problems=(), authored=authored, own_test_count=1
    )

    assert not verdict.own_only and verdict.files == ()


def test_a_failing_method_keeps_being_found_by_its_definition() -> None:
    # The module rule must not leak: an ordinary FAIL line still names a method.
    verdict = own_tests.classify(
        output=_UNITTEST_FAIL, problems=(), authored=_AUTHORED_UNITTEST, own_test_count=1
    )

    assert verdict.files == ("tests/test_partial_repr.py",) and verdict.rescuable


_FAILED_10973 = """\
Testing against Django installed in '/testbed/django'
System check identified no issues (0 silenced).
.......FF.......
======================================================================
FAIL: test_minimal_params (test_postgresql_dbshell_env.SubprocessRunTests)
----------------------------------------------------------------------
AssertionError: {'CONDA_EXE': '/opt/miniconda3/bin/conda'} is not None

======================================================================
FAIL: test_no_password_means_no_custom_environment (test_postgresql_dbshell_env.SubprocessRunTests)
----------------------------------------------------------------------
AssertionError: {'CONDA_EXE': '/opt/miniconda3/bin/conda'} is not None

----------------------------------------------------------------------
Ran 16 tests in 0.003s

FAILED (failures=2)
"""

_FAILED_13925 = """\
Testing against Django installed in '/testbed/django'
Creating test database for alias 'default'...
......................F.FF.....F
======================================================================
FAIL: test_inherited_auto_created_pk_from_parent_link (check_framework.test_model_checks.AutoFieldTests)
----------------------------------------------------------------------
AssertionError: Lists differ

======================================================================
FAIL: test_auto_created_pk_warns (test_check_default_pk_inherited.InheritedTests)
----------------------------------------------------------------------
AssertionError: 0 != 1

======================================================================
FAIL: test_child_of_auto_created_pk_ancestor_still_warns (test_check_default_pk_inherited.InheritedTests)
----------------------------------------------------------------------
AssertionError: 0 != 1

======================================================================
FAIL: test_warning_message_and_hint (test_check_default_pk_inherited.InheritedTests)
----------------------------------------------------------------------
AssertionError: 0 != 1

----------------------------------------------------------------------
Ran 32 tests in 0.046s

FAILED (failures=4)
Destroying test database for alias 'default'...
"""


def test_django_10973_is_rescuable_when_the_tests_in_the_file_it_edited_are_not_counted_as_its_own() -> None:
    authored = {
        "tests/test_postgresql_dbshell_env.py": (
            "class SubprocessRunTests(SimpleTestCase):\n"
            "    def test_minimal_params(self): ...\n"
            "    def test_no_password_means_no_custom_environment(self): ...\n"
        ),
        "tests/dbshell/test_postgresql.py": "class CommandTests:\n    def test_basic(self): ...\n",
    }

    verdict = own_tests.classify(output=_FAILED_10973, problems=(), authored=authored, own_test_count=10)

    assert verdict.files == ("tests/test_postgresql_dbshell_env.py",)
    assert verdict.rescuable  # 16 ran, 10 added: six existing tests ran too


def test_django_13925_is_rescuable_and_withdraws_both_files_that_hold_a_failing_test() -> None:
    authored = {
        "tests/check_framework/test_model_checks.py": (
            "class AutoFieldTests(SimpleTestCase):\n"
            "    def test_inherited_auto_created_pk_from_parent_link(self): ...\n"
        ),
        "tests/test_check_default_pk_inherited.py": (
            "class InheritedTests(SimpleTestCase):\n"
            "    def test_auto_created_pk_warns(self): ...\n"
            "    def test_child_of_auto_created_pk_ancestor_still_warns(self): ...\n"
            "    def test_warning_message_and_hint(self): ...\n"
        ),
    }

    verdict = own_tests.classify(output=_FAILED_13925, problems=(), authored=authored, own_test_count=10)

    assert verdict.files == (
        "tests/check_framework/test_model_checks.py",
        "tests/test_check_default_pk_inherited.py",
    )
    assert verdict.rescuable  # 32 ran, 10 added


def test_a_run_whose_command_ran_only_its_own_files_is_rescuable_and_unsupported() -> None:
    # django-13401 shape: 9 ran, the run added 9. D10: rescued; the verdict says nothing existing ran.
    output = _UNITTEST_FAIL.replace("Ran 8 tests", "Ran 9 tests")

    verdict = own_tests.classify(output=output, problems=(), authored=_AUTHORED_UNITTEST, own_test_count=9)

    assert verdict.rescuable and not verdict.existing_executed


# ---- D10a: an empty test run is "nothing left to run", not red ----------------------------------------


@pytest.mark.parametrize(
    ("returncode", "output", "nothing"),
    [
        (5, "No changed test files to run; refusing an empty test run.", True),  # CommandTestRunner
        (5, "Ran 0 tests in 0.000s\n[spine] the runner executed 0 tests, so this run proves nothing", True),
        (5, "collected 0 items\n\n============ no tests ran in 0.01s ============", True),
        (1, "No changed test files to run; refusing an empty test run.", False),  # only exit 5 counts
        (5, "something else exited with 5", False),
        (1, "FAIL: test_x (m.T)\n\nRan 3 tests in 0.1s\n\nFAILED (failures=1)", False),
        (0, "OK", False),
    ],
)
def test_only_an_empty_or_zero_test_run_is_nothing_left_to_run(
    returncode: int, output: str, nothing: bool
) -> None:
    assert own_tests.nothing_left_to_run(returncode, output) is nothing


async def test_the_refusal_text_is_the_one_the_real_runner_prints(tmp_path: Path) -> None:
    """`own_tests` is below the runners and cannot import them; this keeps its copy of the wording honest."""
    import subprocess

    from orchestrator.sdlc.testrunner import CommandTestRunner

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "b"],
        cwd=tmp_path,
        check=True,
    )
    result = await CommandTestRunner(["true", "{files}"]).run(path=str(tmp_path))

    assert not result.passed and result.returncode == 5
    assert own_tests.nothing_left_to_run(result.returncode, result.output)
