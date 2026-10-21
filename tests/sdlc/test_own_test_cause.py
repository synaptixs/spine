"""SSPN-228 (P4a/P4b): the cause class of a failing test the run wrote.

The texts are the shapes real runs produced (P6, django-14349 / 13401 / 13925), not invented ones.
"""

from __future__ import annotations

import pytest

from orchestrator.sdlc.own_test_cause import CAUSES, Diagnosis, diagnose

_IMPORT_PYTEST = (
    "ERROR: test_url_validator_unsafe_chars (unittest.loader._FailedTest)\n"
    "ImportError: Failed to import test module: test_url_validator_unsafe_chars\n"
    "Traceback (most recent call last):\n"
    "ModuleNotFoundError: No module named 'pytest'\n\nFAILED (errors=1)\n"
)
_PYTEST_COLLECT = (
    "ERROR tests/test_x.py\n"
    "ImportError while importing test module '/w/tests/test_x.py'.\n"
    "E   ModuleNotFoundError: No module named 'helpers'\n"
)
_APP_LABEL = (
    '    "INSTALLED_APPS." % (module, name)\n'
    "RuntimeError: Model class model_fields.models.Foo doesn't declare an explicit app_label and isn't in "
    "an application in INSTALLED_APPS.\n\nERROR: test_two (test_field_model_equality.Tests)\n"
)
_WRONG = (
    "FAIL: test_auto_created_pk_warns (test_check_default_pk_inherited.CheckDefaultPkInheritedTests)\n"
    "AssertionError: 'models.W042' not found in []\n"
)
_PYTEST_ASSERT = "FAILED tests/test_x.py::test_a - assert 1 == 2\nE   assert 1 == 2\n"
_UNKNOWN = "FAIL: test_a (t.T)\nTypeError: unsupported operand type(s) for +: 'int' and 'str'\n"


@pytest.mark.parametrize(
    ("output", "cause", "evidence"),
    [
        (_IMPORT_PYTEST, "import_error", "No module named 'pytest'"),
        (_PYTEST_COLLECT, "import_error", "No module named 'helpers'"),
        (_APP_LABEL, "missing_app", "isn't in an application in INSTALLED_APPS"),
        (_WRONG, "wrong_expectation", "AssertionError: 'models.W042' not found in []"),
        (_PYTEST_ASSERT, "wrong_expectation", "assert 1 == 2"),
        (_UNKNOWN, "other", "TypeError: unsupported operand"),
    ],
    ids=[
        "unittest-import",
        "pytest-collect-import",
        "django-app-label",
        "unittest-assert",
        "pytest-assert",
        "other",
    ],
)
def test_each_shape_a_real_run_produced_gets_its_class_and_the_line_that_shows_it(
    output: str, cause: str, evidence: str
) -> None:
    got = diagnose(output)

    assert got.cause == cause
    assert evidence in got.evidence
    assert got.suggestion  # one sentence a person can act on


def test_a_missing_app_wins_over_the_assertion_that_follows_it() -> None:
    """The same output holds both lines; the app label is the cause, the assertion is its symptom."""
    assert diagnose(_APP_LABEL + _WRONG).cause == "missing_app"


def test_an_import_error_wins_over_an_assertion_elsewhere_in_the_output() -> None:
    assert diagnose(_WRONG + _IMPORT_PYTEST).cause == "import_error"


def test_empty_output_is_other_and_the_classes_are_a_closed_set() -> None:
    got = diagnose("")

    assert got == Diagnosis("other", "", got.suggestion) and got.suggestion
    assert set(CAUSES) == {"import_error", "missing_app", "wrong_expectation", "other"}


def test_the_evidence_line_is_bounded() -> None:
    long = "AssertionError: " + "x" * 5000

    assert len(diagnose(long).evidence) <= 200
