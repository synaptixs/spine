"""The KG track's fix-forward (P7, SSPN-358): the sdlc-side defects the post-merge review found.

* **B146** — ``EditScope.allows`` kept a tenth private test rule (``_TEST_FILE_RE``, ``_TEST_DIRS``
  with ``spec``): it admitted a product ``src/spec/`` folder to a confident edit scope and refused
  real tests in Perl, Ruby, ``.mts`` and ``*.UnitTests`` projects. It now asks the one rule, plus
  one documented filter: a Ruby file under ``spec/`` (``spec_helper.rb``, ``spec/support/``) stays
  in scope, because RSpec's helpers are not tests by the rule (D24) but a test change edits them (D43).
* **B149** — ``feature_runner`` handed the rule absolute paths at two sites, so a checkout under a
  folder named ``MyApp.Tests/`` or ``__tests__/`` made every file read as a test (D46). The other
  three call sites the grill listed read ``_changed_files``, which is ``git status`` output and so
  already repository-relative.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.sdlc import feature_runner
from orchestrator.sdlc.scope import EditScope

# ---- B146: the edit scope asks the one rule ------------------------------------------------------------

_SCOPE = EditScope(files=("src/app/cart.py",), confident=True)


@pytest.mark.parametrize(
    "rel",
    [
        "t/basic.t",  # Perl
        "src/cart_spec.rb",  # RSpec's own pattern
        "src/foo.test.mts",  # a TS test with an ESM extension
        "Shop.UnitTests/CartFacts.cs",  # an xUnit project
        "spec/spec_helper.rb",  # RSpec's helper: not a test, but a test change edits it (D43)
        "spec/support/factories.rb",
        "tests/test_cart.py",
        "src/app/cart.py",  # listed
    ],
)
def test_a_test_or_a_listed_file_is_in_scope(rel: str) -> None:
    assert _SCOPE.allows(rel)


@pytest.mark.parametrize(
    "rel",
    [
        "src/spec/loader.py",  # a product folder named `spec` (D24, B139)
        "spec/loader.py",  # only a *Ruby* file under `spec/` is let in
        "intake/specs.py",
        "src/app/other.py",
    ],
)
def test_product_code_outside_the_listed_files_is_out_of_scope(rel: str) -> None:
    assert not _SCOPE.allows(rel)


def test_the_new_package_init_is_still_in_scope() -> None:
    assert _SCOPE.allows("src/app/new/__init__.py", new_dirs=frozenset({"src/app/new"}))


# ---- B149: the run loop hands the rule repository-relative paths -------------------------------------------

_CHECKOUT = Path("/home/ci/MyApp.Tests/wt")


def test_a_stalled_loop_names_only_the_tests_it_wrote_wherever_the_checkout_sits() -> None:
    authored = {str(_CHECKOUT / "src/app/service.py"), str(_CHECKOUT / "tests/test_service.py")}
    failures = (
        f"FAILED {_CHECKOUT}/tests/test_service.py::test_x - AssertionError\n"
        f"ERROR {_CHECKOUT}/src/app/service.py:3 NameError\n"
    )
    lines = feature_runner._stall_lines(
        2, SimpleNamespace(), failures, authored, {"acceptance_criteria": ["c"]}, root=_CHECKOUT
    )
    assert "[refine]   written by this run: tests/test_service.py" in lines


def test_the_run_loop_gives_the_stall_report_its_worktree() -> None:
    source = inspect.getsource(feature_runner.run_feature)
    assert "_stall_lines(repeats, result, failures, authored, spec, root=path)" in source


def test_the_cover_stage_withdraws_only_tests_it_created_wherever_the_checkout_sits() -> None:
    files = [str(_CHECKOUT / "src/app/service.py"), str(_CHECKOUT / "tests/test_service.py")]
    created = feature_runner._cover_created_tests(_CHECKOUT, files, authored=set())
    assert created == [str(_CHECKOUT / "tests/test_service.py")]


def test_the_cover_stage_never_lists_a_test_an_earlier_stage_wrote() -> None:
    files = [str(_CHECKOUT / "tests/test_service.py")]
    assert feature_runner._cover_created_tests(_CHECKOUT, files, authored=set(files)) == []


def test_the_run_loop_records_cover_tests_through_the_relative_check() -> None:
    source = inspect.getsource(feature_runner.run_feature)
    assert "cover_written.extend(_cover_created_tests(path, covered.files, authored))" in source
