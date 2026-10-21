"""SSPN-227: telling the model's own failing tests apart from everything else a runner reports.

The inputs are real shapes: the unittest lines are what Django's runner printed in the P6 runs
(django-14155, django-14349), the pytest lines are what pytest and scikit-learn-11578 print. Nothing
here runs a test; the module under test reads text.
"""

from __future__ import annotations

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


def test_a_run_in_which_only_the_runs_own_tests_executed_has_nothing_to_lean_on() -> None:
    output = _UNITTEST_FAIL.replace("Ran 8 tests", "Ran 2 tests")

    verdict = own_tests.classify(output=output, problems=(), authored=_AUTHORED_UNITTEST, own_test_count=2)

    assert verdict.own_only
    assert not verdict.existing_executed and not verdict.rescuable
    assert "no existing test" in verdict.reason


@pytest.mark.parametrize("output", ["Ran 0 tests in 0.000s\n\nNO TESTS RAN", "no summary line at all"])
def test_a_run_that_executed_nothing_or_cannot_say_has_nothing_to_lean_on(output: str) -> None:
    verdict = own_tests.classify(
        output=_UNITTEST_FAIL.split("Ran 8 tests")[0] + output,
        problems=(),
        authored=_AUTHORED_UNITTEST,
        own_test_count=2,
    )

    assert not verdict.existing_executed and not verdict.rescuable


def test_the_switch_is_on_by_default_and_off_with_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_OWN_TEST_RESCUE", raising=False)
    assert own_tests.enabled()
    monkeypatch.setenv("SDLC_OWN_TEST_RESCUE", "0")
    assert not own_tests.enabled()
    monkeypatch.setenv("SDLC_OWN_TEST_RESCUE", "1")
    assert own_tests.enabled()
