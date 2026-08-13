"""The runtime oracle — frame-to-node-id mapping, and recall arithmetic.

The mapping is the whole risk of this feature: a tracer that works on a flat module and
silently mismatches every decorated or nested function would report a confident wrong number.
So each hazard in the build document's §9 table gets a test here, against real code objects
rather than mocks — the hazards are properties of CPython, and a mock would only assert what
the author already believed.
"""

from __future__ import annotations

import json
from functools import wraps
from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg.runtime_oracle import (
    OracleError,
    RuntimeReport,
    callee_id,
    caller_id,
    module_for_file,
    score_observations,
    score_runtime,
)

REPO = Path(__file__).resolve().parents[2]
HERE = "tests.pkg.test_runtime_oracle"


# ---- specimens: real shapes, not mocks -----------------------------------


def _outer() -> Any:
    def inner() -> int:
        return 1

    return inner


def _decorator(fn: Any) -> Any:
    @wraps(fn)
    def wrapper(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return wrapper


def _bare_decorator(fn: Any) -> Any:
    def wrapper(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return wrapper


@_decorator
def _wrapped() -> int:
    return 1


@_bare_decorator
def _unwrapped() -> int:
    return 1


class Specimen:
    def method(self) -> int:
        return 1

    @classmethod
    def klass(cls) -> int:
        return 1

    @staticmethod
    def static() -> int:
        return 1


def _genexpr_code() -> Any:
    gen = (x for x in [0])
    return gen.gi_code  # type: ignore[attr-defined]  # runtime attribute, not in the stub


# ---- hazard 1: nested functions -----------------------------------------


def test_locals_is_stripped_from_a_nested_function() -> None:
    """`outer.<locals>.inner` is CPython's spelling; the graph holds a flat dotted path."""
    inner = _outer()
    assert "<locals>" in inner.__code__.co_qualname
    assert caller_id(inner.__code__, REPO) == f"py:{HERE}._outer.inner"
    assert callee_id(inner, REPO) == (f"py:{HERE}._outer.inner", "ok")


# ---- hazard 2 & 3: decorators -------------------------------------------


def test_functools_wraps_preserves_the_original_identity() -> None:
    """With @wraps the callee reports the wrapped name, which is what has a node."""
    assert callee_id(_wrapped, REPO) == (f"py:{HERE}._wrapped", "ok")


def test_a_decorator_without_wraps_reports_the_wrapper() -> None:
    """Without @wraps the wrapper's own qualname surfaces — a real, unavoidable divergence.

    It maps to a well-formed id that has no node, so it lands in `unmapped` rather than being
    counted as a recall miss against a symbol that was never called.
    """
    mapped, why = callee_id(_unwrapped, REPO)
    assert why == "ok"
    assert mapped == f"py:{HERE}._bare_decorator.wrapper"
    assert mapped != f"py:{HERE}._unwrapped"


# ---- hazard 4 & 5: methods ----------------------------------------------


def test_a_bound_method_maps_to_its_class_qualified_id() -> None:
    assert callee_id(Specimen().method, REPO) == (f"py:{HERE}.Specimen.method", "ok")


def test_classmethod_and_staticmethod_map_the_same_way() -> None:
    assert callee_id(Specimen.klass, REPO) == (f"py:{HERE}.Specimen.klass", "ok")
    assert callee_id(Specimen.static, REPO) == (f"py:{HERE}.Specimen.static", "ok")


# ---- hazard 6 & 7: anonymous code ---------------------------------------


def test_a_lambda_is_anonymous_not_a_miss() -> None:
    """A lambda has no node, so there is nothing it could have matched."""
    assert callee_id(lambda: 0, REPO) == (None, "anonymous")
    assert caller_id((lambda: 0).__code__, REPO) is None


def test_a_generator_expression_is_anonymous() -> None:
    code = _genexpr_code()
    assert "<genexpr>" in code.co_qualname
    assert caller_id(code, REPO) is None


# ---- hazard 8 & 9: outside the graph's remit ----------------------------


def test_a_builtin_without_dunders_is_reported_as_builtin() -> None:
    assert callee_id([].append, REPO) == (None, "builtin")


def test_a_stdlib_callee_is_out_of_tree_not_unmapped() -> None:
    """Never the graph's job — distinct from an in-tree symbol with no node."""
    assert callee_id(json.dumps, REPO) == (None, "out-of-tree")


def test_module_for_file_rejects_dot_directories_and_non_python() -> None:
    """The corpus fixtures live under `.repo/`; tracing must not resurrect them."""
    assert module_for_file(str(REPO / "src/orchestrator/pkg/accuracy.py"), REPO) == (
        "orchestrator.pkg.accuracy"
    )
    assert module_for_file(str(REPO / "corpus/python/plain/.repo/shop/cart.py"), REPO) is None
    assert module_for_file(str(REPO / "README.md"), REPO) is None
    assert module_for_file("/elsewhere/mod.py", REPO) is None


# ---- scoring -------------------------------------------------------------

NODES = {"py:a.f", "py:a.g", "py:a.h"}


def test_recall_counts_only_pairs_whose_ends_are_both_nodes() -> None:
    report = score_observations(
        {("py:a.f", "py:a.g"), ("py:a.f", "py:ghost")},
        NODES,
        {("py:a.f", "py:a.g")},
    )
    assert report.observed == 1
    assert report.matched == 1
    assert report.unmapped == 1
    assert report.recall == 1.0
    assert report.unmapped_examples == ("py:a.f -> py:ghost",)


def test_an_observed_call_with_no_edge_is_a_miss() -> None:
    report = score_observations({("py:a.f", "py:a.g"), ("py:a.g", "py:a.h")}, NODES, {("py:a.f", "py:a.g")})
    assert report.observed == 2
    assert report.matched == 1
    assert report.recall == 0.5
    assert report.missing == ("py:a.g -CALLS-> py:a.h",)


def test_an_empty_trace_scores_none_not_one() -> None:
    assert score_observations(set(), NODES, set()).recall is None


def test_precision_is_structurally_unavailable() -> None:
    """Not an oversight — a trace cannot see the calls that did not happen."""
    assert score_observations({("py:a.f", "py:a.g")}, NODES, set()).precision is None


def test_unobserved_edges_do_not_count_against_anything() -> None:
    """The graph having edges the tests never exercise is expected, not a defect."""
    report = score_observations({("py:a.f", "py:a.g")}, NODES, {("py:a.f", "py:a.g"), ("py:a.g", "py:a.h")})
    assert report.observed == 1
    assert report.recall == 1.0


# ---- end to end ----------------------------------------------------------


def test_score_runtime_traces_a_real_suite(tmp_path: Path) -> None:
    """The whole path: subprocess, tracer, extraction, comparison."""
    (tmp_path / "app.py").write_text(
        "def helper() -> int:\n    return 1\n\n\ndef caller() -> int:\n    return helper()\n",
        encoding="utf-8",
    )
    (tmp_path / "test_app.py").write_text(
        "from app import caller\n\n\ndef test_calls() -> None:\n    assert caller() == 1\n",
        encoding="utf-8",
    )

    report = score_runtime(tmp_path, targets=["test_app.py"])

    assert report.pytest_exit == 0
    assert report.observed >= 1
    assert report.recall == 1.0, report.missing
    # `missing` holds formatted strings, not pairs — comparing a tuple here passes vacuously.
    assert "py:app.caller -CALLS-> py:app.helper" not in report.missing
    assert "runtime_oracle" in report.command


def test_a_missing_repo_is_an_oracle_error() -> None:
    with pytest.raises(OracleError, match="not a directory"):
        score_runtime("/nope/does/not/exist")


def test_report_is_frozen() -> None:
    report = RuntimeReport(1, 1, 0, (), (), {}, 0, "cmd")
    with pytest.raises(AttributeError):
        report.observed = 2  # type: ignore[misc]


def test_coverage_is_reported_or_honestly_absent(tmp_path: Path) -> None:
    """Coverage is the second bound on the number, so it must be present or explicitly null.

    Best-effort by design: a `coverage` that clashes with the tracer's tool id should degrade
    to `None`, never take the measurement down with it.
    """
    (tmp_path / "app.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text(
        "from app import f\n\n\ndef test_f() -> None:\n    assert f() == 1\n", encoding="utf-8"
    )
    report = score_runtime(tmp_path, targets=["test_app.py"])
    assert report.coverage_pct is None or 0.0 <= report.coverage_pct <= 100.0
