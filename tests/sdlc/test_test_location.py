"""SSPN-223: say where THIS repository keeps the tests for the modules a change touches.

P6 stopped 9 of its 32 no-patch runs with the runner executing zero tests: the layout block told
the model to put tests under `tests/`, and sympy (`sympy/core/tests/`) and django
(`tests/utils_tests/`) do not collect a file there. The function under test is pure: it looks at
directories that already hold tests and ranks them. What matters as much as the ranking is when it
says nothing — a repository whose tests already live in `tests/` must get the prompt it gets today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.sdlc.test_location import Placement, locate_tests, placement_block, ran_no_tests


def _tree(root: Path, files: list[str]) -> None:
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


SYMPY_LIKE = [
    "pkg/core/sympify.py",
    "pkg/core/tests/test_sympify.py",
    "pkg/sets/conditionset.py",
    "pkg/sets/tests/test_conditionset.py",
]
DJANGO_LIKE = [
    "lib/utils/html.py",
    "tests/utils_tests/__init__.py",
    "tests/utils_tests/test_html.py",
    "tests/aggregation/__init__.py",
    "tests/aggregation/tests.py",
]


def test_the_tests_beside_the_module_are_where_it_keeps_them(tmp_path: Path) -> None:
    """sympy: `sympy/core/sympify.py` is tested in `sympy/core/tests/`."""
    _tree(tmp_path, SYMPY_LIKE)
    found = locate_tests(tmp_path, ["pkg/core/sympify.py"])
    assert [p.directory for p in found] == ["pkg/core/tests"]
    assert found[0].reason == "sibling"
    assert found[0].example == "pkg/core/tests/test_sympify.py"


def test_a_test_file_named_for_the_module_names_the_directory(tmp_path: Path) -> None:
    """django: `django/utils/html.py` has no `tests/` beside it; `test_html.py` says where its tests are."""
    _tree(tmp_path, DJANGO_LIKE)
    found = locate_tests(tmp_path, ["lib/utils/html.py"])
    assert [p.directory for p in found] == ["tests/utils_tests"]
    assert found[0].reason == "name"


def test_a_covering_test_from_the_graph_comes_before_a_name_match(tmp_path: Path) -> None:
    """The PKG is the primary signal: a test that reaches the code beats one that merely shares a name."""
    _tree(
        tmp_path,
        [
            "lib/mod.py",
            "tests/a_tests/test_x.py",
            "tests/b_tests/test_mod.py",
        ],
    )
    found = locate_tests(tmp_path, ["lib/mod.py"], covering=["tests/a_tests/test_x.py"])
    assert [(p.directory, p.reason) for p in found] == [
        ("tests/a_tests", "covering"),
        ("tests/b_tests", "name"),
    ]


def test_a_directory_found_three_ways_is_listed_once_with_its_best_reason(tmp_path: Path) -> None:
    _tree(tmp_path, SYMPY_LIKE)
    found = locate_tests(tmp_path, ["pkg/core/sympify.py"], covering=["pkg/core/tests/test_sympify.py"])
    assert [(p.directory, p.reason) for p in found] == [("pkg/core/tests", "covering")]


def test_no_tests_anywhere_for_the_module_gives_nothing(tmp_path: Path) -> None:
    _tree(tmp_path, ["lib/orphan.py", "tests/test_other.py"])
    assert locate_tests(tmp_path, ["lib/orphan.py"]) == []


def test_only_a_directory_that_exists_and_holds_a_test_is_ever_suggested(tmp_path: Path) -> None:
    """Never invent a directory: the sibling `tests/` must exist, and must hold a test file."""
    _tree(tmp_path, ["pkg/a/mod.py", "pkg/b/mod.py", "pkg/b/tests/conftest.py"])
    assert locate_tests(tmp_path, ["pkg/a/mod.py", "pkg/b/mod.py"]) == []


def test_covering_paths_that_do_not_exist_or_leave_the_repo_are_ignored(tmp_path: Path) -> None:
    _tree(tmp_path, SYMPY_LIKE)
    found = locate_tests(
        tmp_path,
        ["pkg/core/sympify.py"],
        covering=["../outside/test_x.py", "pkg/gone/tests/test_gone.py", "/etc/test_passwd.py"],
    )
    assert [p.directory for p in found] == ["pkg/core/tests"]


def test_the_answer_is_deterministic_and_ties_break_by_path(tmp_path: Path) -> None:
    _tree(tmp_path, ["lib/m.py", "tests/z_tests/test_m.py", "tests/a_tests/test_m.py"])
    first = locate_tests(tmp_path, ["lib/m.py"])
    assert first == locate_tests(tmp_path, ["lib/m.py"])
    assert [p.directory for p in first] == ["tests/a_tests", "tests/z_tests"]


def test_several_touched_modules_each_contribute_their_directory(tmp_path: Path) -> None:
    _tree(tmp_path, SYMPY_LIKE)
    found = locate_tests(tmp_path, ["pkg/core/sympify.py", "pkg/sets/conditionset.py"])
    assert sorted(p.directory for p in found) == ["pkg/core/tests", "pkg/sets/tests"]


# ---- the block: when it speaks, and when it must not ------------------------------------------


def test_a_repository_whose_tests_are_in_tests_gets_no_block(tmp_path: Path) -> None:
    """Byte-identical prompt for what already works: the suggestion equals the layout's tests dir."""
    _tree(tmp_path, ["lib/mod.py", "tests/test_mod.py"])
    found = locate_tests(tmp_path, ["lib/mod.py"])
    assert [p.directory for p in found] == ["tests"]
    assert placement_block(found, tests_dir="tests") == ""


def test_no_placements_no_block() -> None:
    assert placement_block([], tests_dir="tests") == ""


def test_the_block_names_the_directory_an_example_and_says_to_add_a_new_file(tmp_path: Path) -> None:
    _tree(tmp_path, SYMPY_LIKE)
    block = placement_block(locate_tests(tmp_path, ["pkg/core/sympify.py"]), tests_dir="tests")
    assert block.startswith("\n\n")
    assert "WHERE THIS REPOSITORY KEEPS TESTS FOR THESE MODULES" in block
    assert "pkg/core/tests" in block
    assert "pkg/core/tests/test_sympify.py" in block
    assert "new file" in block and "do not overwrite" in block.lower()
    # It must win against the generic sentence the layout block prints.
    assert "instead of `tests/`" in block


def test_the_block_lists_at_most_three_directories() -> None:
    many = [Placement(directory=f"d{i}", reason="name", example=f"d{i}/test_a.py") for i in range(8)]
    block = placement_block(many, tests_dir="tests")
    assert block.count("test_a.py") == 3


@pytest.mark.parametrize("tests_dir", ["tests", "tests/", "./tests"])
def test_the_layouts_tests_dir_is_compared_after_normalising(tests_dir: str) -> None:
    only = [Placement(directory="tests", reason="name", example="tests/test_a.py")]
    assert placement_block(only, tests_dir=tests_dir) == ""


# ---- the trigger: the block is a recovery, shown only after a run executed nothing ------------


@pytest.mark.parametrize(
    "output",
    [
        "\n[spine] the runner executed 0 tests, so this run proves nothing; exit status changed from 0 to 5",
        "============================ no tests ran in 0.01s =============================",
        "collected 0 items\n",
        "Ran 0 tests in 0.000s\n\nOK",
    ],
)
def test_a_run_that_executed_nothing_is_recognised(output: str) -> None:
    assert ran_no_tests(output)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "1 failed, 3 passed in 0.39s",
        "Ran 13 tests in 0.018s\n\nOK",
        "collected 10 items\n",
        "AssertionError: expected the runner executed 3 tests",
    ],
)
def test_a_run_that_executed_tests_is_not(output: str) -> None:
    assert not ran_no_tests(output)


def test_the_benchmark_wrappers_own_note_is_recognised() -> None:
    """The wrapper that prints the note lives under `evals/`, which `sdlc/` must not import; this pins
    the wording across the boundary so a reword there cannot silently switch the recovery off."""
    from orchestrator.evals.swebench.exec import NO_TESTS_NOTE

    assert ran_no_tests(NO_TESTS_NOTE)
