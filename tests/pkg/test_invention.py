"""Invention detection — CALLS edges to names that are bound in the caller's own scope.

The risk here is symmetrical and both directions are costly: miss a binding form and real
invention goes unreported; treat an import as a binding and legitimate external calls get
called fiction. So every binding form gets a test, and so does every reason *not* to flag.
"""

from __future__ import annotations

import ast
from pathlib import Path

from orchestrator.pkg import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, Node, NodeKind, Provenance
from orchestrator.pkg.invention import _scopes, _visible_names, find_invented_calls, sample_edges


def _visible(source: str, line: int) -> set[str]:
    return _visible_names(_scopes(ast.parse(source)), line)


# ---- binding forms (AC 2) -------------------------------------------------


def test_a_parameter_is_a_binding() -> None:
    src = "def run(echo):\n    echo('hi')\n"
    assert "echo" in _visible(src, 2)


def test_every_parameter_kind_is_a_binding() -> None:
    src = "def run(a, /, b, *args, c=1, **kw):\n    a()\n"
    assert {"a", "b", "args", "c", "kw"} <= _visible(src, 2)


def test_assignment_for_with_and_walrus_are_bindings() -> None:
    src = (
        "def run(xs, ctx):\n"
        "    fn = xs[0]\n"
        "    for item in xs:\n"
        "        pass\n"
        "    with ctx as handle:\n"
        "        pass\n"
        "    if (found := xs[1]):\n"
        "        pass\n"
        "    fn()\n"
    )
    assert {"fn", "item", "handle", "found"} <= _visible(src, 9)


def test_comprehension_and_except_targets_are_bindings() -> None:
    src = (
        "def run(xs):\n"
        "    ys = [f for f in xs]\n"
        "    try:\n"
        "        pass\n"
        "    except OSError as err:\n"
        "        err()\n"
    )
    assert {"f", "err"} <= _visible(src, 6)


def test_a_nested_def_binds_its_own_name() -> None:
    """The sub-class a file-scoped prototype misses entirely — and 170 of this repo's 496."""
    src = "def outer():\n    def esc(s):\n        return s\n    esc('x')\n"
    assert "esc" in _visible(src, 4)


# ---- scope, not file membership (AC 4) ------------------------------------


def test_a_binding_in_a_sibling_function_is_not_visible() -> None:
    """A file-scoped test reports this as bound and over-counts. Scope is the whole point."""
    src = "def a(handler):\n    handler()\n\n\ndef b():\n    handler()\n"
    assert "handler" in _visible(src, 2)
    assert "handler" not in _visible(src, 6)


def test_an_enclosing_scope_is_visible_from_a_nested_one() -> None:
    src = "def outer(cb):\n    def inner():\n        cb()\n    return inner\n"
    assert "cb" in _visible(src, 3)


def test_module_level_bindings_are_visible_everywhere() -> None:
    src = "handler = object()\n\n\ndef run():\n    handler()\n"
    assert "handler" in _visible(src, 5)


# ---- what must never be flagged (AC 3) ------------------------------------


def test_an_import_is_not_a_binding() -> None:
    """`import json` creates no Store node, and that is the correct behaviour, not luck.

    An imported name genuinely refers to something outside this file. Treating it as a
    binding would report every legitimate external call as invention.
    """
    src = "import json\nfrom os import getcwd\n\n\ndef run():\n    json.dumps({})\n    getcwd()\n"
    visible = _visible(src, 6)
    assert "json" not in visible
    assert "getcwd" not in visible


# ---- end to end ------------------------------------------------------------


def _repo(root: Path, body: str) -> Path:
    pkg = root / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(body, encoding="utf-8")
    return root


def test_an_invented_edge_is_detected(tmp_path: Path) -> None:
    """The shape found at launch.py:237 — `echo: Echo` is a parameter, and the graph asserts
    a module named `echo` exists outside the tree.

    The edge is built by hand rather than extracted. The Python front-end no longer emits it
    (it returns None instead of inventing `py:{name}`), so a fixture cannot produce one — and
    a detector that can only be tested by a broken front-end stops being a regression guard
    the moment the defect is fixed. This asserts what the detector does, not what the
    extractor happens to do.
    """
    _repo(tmp_path, "def run(echo):\n    echo('hi')\n")
    batch = RepoCodeExtractor().extract(tmp_path)
    batch.add_node(Node("py:echo", NodeKind.FUNCTION, "echo", "python", external=True))
    batch.add_edge(Edge("py:app.mod.run", "py:echo", EdgeKind.CALLS, Provenance("app/mod.py", 2)))

    report = find_invented_calls(batch, tmp_path)

    assert len(report.invented) == 1
    found = report.invented[0]
    assert found.dst == "py:echo"
    assert found.name == "echo"
    assert found.file == "app/mod.py"
    assert "echo is local" in str(found)


def test_the_python_front_end_no_longer_invents(tmp_path: Path) -> None:
    """The fix, asserted where it will be noticed if it regresses.

    A call through a parameter used to emit `py:echo`; it now emits nothing, because an
    unresolvable bare name is skipped rather than guessed at — the rule the resolver already
    applied to ambiguous attribute chains.
    """
    _repo(tmp_path, "def run(echo):\n    echo('hi')\n")
    report = find_invented_calls(RepoCodeExtractor().extract(tmp_path), tmp_path)
    assert report.invented == ()
    assert report.candidates == 0


def test_a_genuine_external_call_is_not_flagged(tmp_path: Path) -> None:
    _repo(tmp_path, "import json\n\n\ndef run(x):\n    return json.dumps(x)\n")
    report = find_invented_calls(RepoCodeExtractor().extract(tmp_path), tmp_path)
    assert report.invented == ()


def test_a_resolved_local_call_is_not_flagged(tmp_path: Path) -> None:
    """A call the extractor resolved correctly has a first-party target, not an external one."""
    _repo(tmp_path, "def helper():\n    return 1\n\n\ndef run():\n    return helper()\n")
    report = find_invented_calls(RepoCodeExtractor().extract(tmp_path), tmp_path)
    assert report.invented == ()


def test_the_rate_is_over_all_calls_not_just_candidates(tmp_path: Path) -> None:
    _repo(tmp_path, "def helper():\n    return 1\n\n\ndef run():\n    return helper()\n")
    batch = RepoCodeExtractor().extract(tmp_path)
    batch.add_node(Node("py:ghost", NodeKind.FUNCTION, "ghost", "python", external=True))
    batch.add_edge(Edge("py:app.mod.run", "py:ghost", EdgeKind.CALLS, Provenance("app/mod.py", 5)))

    report = find_invented_calls(batch, tmp_path)
    assert report.rate == len(report.invented) / report.total_calls
    assert report.unexamined == 0


def test_nothing_to_examine_scores_none_not_zero(tmp_path: Path) -> None:
    _repo(tmp_path, "x = 1\n")
    report = find_invented_calls(RepoCodeExtractor().extract(tmp_path), tmp_path)
    assert report.rate is None


# ---- the sampler (AC 6) ----------------------------------------------------


def test_the_sample_is_deterministic(tmp_path: Path) -> None:
    """Two people auditing the same commit must review the same facts, or they cannot
    compare notes — which is the only thing that makes a manual audit worth doing twice."""
    _repo(tmp_path, "def a():\n    return b()\n\n\ndef b():\n    return 1\n")
    batch = RepoCodeExtractor().extract(tmp_path)

    first = sample_edges(batch, EdgeKind.CALLS, 3)
    second = sample_edges(batch, EdgeKind.CALLS, 3)
    assert first == second
    assert all("-CALLS->" in line for line in first)


def test_an_empty_or_zero_sample_is_empty(tmp_path: Path) -> None:
    _repo(tmp_path, "x = 1\n")
    batch = RepoCodeExtractor().extract(tmp_path)
    assert sample_edges(batch, EdgeKind.CONSUMES, 5) == []
    assert sample_edges(batch, EdgeKind.CALLS, 0) == []
