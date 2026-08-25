"""Invention detection — CALLS edges to names that are bound in the caller's own scope.

The risk here is symmetrical and both directions are costly: miss a binding form and real
invention goes unreported; treat an import as a binding and legitimate external calls get
called fiction. So every binding form gets a test, and so does every reason *not* to flag.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from orchestrator.pkg import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.invention import (
    MEASURED,
    NOT_APPLICABLE,
    UNWALKED,
    LanguageInvention,
    _scopes,
    _visible_names,
    find_invented_calls,
    sample_edges,
)


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


# ---- the other four front-ends ---------------------------------------------
#
# The Python detector could not see these. A shadowed name in TypeScript, Go, C++ or C#
# does not become an ungrounded `py:name`-style id — it resolves against the front-end's own
# file-level table and lands on a *real node*. Nothing dangles, so `pkg verify` was silent;
# no corpus case carried the shape, so precision read 1.00. Each case below is the minimal
# reproduction, and each is paired with the honest call it must not touch.

_SHADOW_CASES = {
    "typescript": (
        "a.ts",
        "export function send(x: string): void {}\n"
        "export function outer(send: (v: string) => void): void { send('hi'); }\n"
        "export function honest(): void { send('real'); }\n",
        "tree_sitter_typescript",
    ),
    "go": (
        "a.go",
        "package main\n\nfunc Send(x string) {}\n"
        'func Outer(Send func(string)) { Send("hi") }\n'
        'func Honest() { Send("real") }\n',
        "tree_sitter_go",
    ),
    "csharp": (
        "A.cs",
        "using System;\nclass A {\n  void Send(string x) {}\n"
        '  void Outer(Action<string> Send) { Send("hi"); }\n'
        '  void Honest() { Send("real"); }\n}\n',
        "tree_sitter_c_sharp",
    ),
    "cpp": (
        "a.cpp",
        "void send(const char *x) {}\n"
        'void outer(void (*send)(const char *)) { send("hi"); }\n'
        'void honest() { send("real"); }\n',
        "tree_sitter_cpp",
    ),
}


def _one_language(tmp_path: Path, language: str) -> LanguageInvention:
    name, source, extra = _SHADOW_CASES[language]
    pytest.importorskip(extra, reason=f"install the '{language}' extra")
    (tmp_path / name).write_text(source, encoding="utf-8")
    report = find_invented_calls(RepoCodeExtractor().extract(tmp_path), tmp_path)
    return next(e for e in report.by_language if e.language == language)


def test_typescript_shadowed_parameter_is_invention(tmp_path: Path) -> None:
    entry = _one_language(tmp_path, "typescript")
    assert len(entry.invented) == 1
    assert entry.invented[0].dst == "ts:a.send"
    assert entry.invented[0].line == 2


def test_go_shadowed_parameter_is_invention(tmp_path: Path) -> None:
    entry = _one_language(tmp_path, "go")
    assert len(entry.invented) == 1
    assert entry.invented[0].name == "Send"


def test_csharp_shadowed_parameter_is_invention(tmp_path: Path) -> None:
    entry = _one_language(tmp_path, "csharp")
    assert len(entry.invented) == 1
    assert entry.invented[0].dst == "csharp:A.Send"


def test_cpp_shadowed_function_pointer_is_invention(tmp_path: Path) -> None:
    entry = _one_language(tmp_path, "cpp")
    assert len(entry.invented) == 1
    assert entry.invented[0].dst == "cpp:send"


def test_the_honest_call_in_the_same_file_is_never_flagged(tmp_path: Path) -> None:
    """Each fixture's third function calls the same name without shadowing it.

    If this ever fails the oracle is accusing correct code, which is worse than missing the
    defect: the front-ends would then be 'fixed' into dropping real edges.
    """
    for language in _SHADOW_CASES:
        case = tmp_path / language
        case.mkdir()
        entry = _one_language(case, language)
        callers = {i.src for i in entry.invented}
        assert len(callers) == 1, language
        assert next(iter(callers)).lower().endswith("outer"), language


def test_c_is_the_control_and_stays_clean(tmp_path: Path) -> None:
    """`c_extractor._bound_names` already refuses these. Walked, not assumed."""
    pytest.importorskip("tree_sitter_c", reason="install the 'c' extra")
    (tmp_path / "a.c").write_text(
        "void send(const char *x) {}\n"
        'void outer(void (*send)(const char *)) { send("hi"); }\n'
        'void honest(void) { send("real"); }\n',
        encoding="utf-8",
    )
    report = find_invented_calls(RepoCodeExtractor().extract(tmp_path), tmp_path)
    entry = next(e for e in report.by_language if e.language == "c")
    assert entry.status == MEASURED
    assert entry.invented == ()


# ---- what a zero is allowed to mean ----------------------------------------


def test_a_language_with_no_walker_is_unwalked_not_clean() -> None:
    """The failure this project keeps having: a 0 that means 'nothing ran'."""
    batch = FactBatch()
    batch.add_node(Node("rust:a.f", NodeKind.FUNCTION, "f", "rust", Provenance("a.rs", 1)))
    batch.add_node(Node("rust:a.g", NodeKind.FUNCTION, "g", "rust", Provenance("a.rs", 2)))
    batch.add_edge(Edge("rust:a.f", "rust:a.g", EdgeKind.CALLS, Provenance("a.rs", 2)))

    report = find_invented_calls(batch, Path("."))
    entry = next(e for e in report.by_language if e.language == "rust")
    assert entry.status == UNWALKED
    assert entry.total_calls == 1
    assert entry.unexamined == 1
    assert report.unmeasured_languages == ("rust",)


def test_java_and_sql_are_excluded_with_a_stated_reason() -> None:
    batch = FactBatch()
    batch.add_node(Node("java:a.A.f", NodeKind.FUNCTION, "f", "java", Provenance("A.java", 1)))
    batch.add_node(Node("java:a.A.g", NodeKind.FUNCTION, "g", "java", Provenance("A.java", 2)))
    batch.add_edge(Edge("java:a.A.f", "java:a.A.g", EdgeKind.CALLS, Provenance("A.java", 2)))

    entry = next(e for e in find_invented_calls(batch, Path(".")).by_language if e.language == "java")
    assert entry.status == NOT_APPLICABLE
    assert "namespace" in entry.reason
    assert entry.total_calls == 1
    # Not counted as unmeasured: the language cannot express the defect, which is an answer.
    assert "java" not in find_invented_calls(batch, Path(".")).unmeasured_languages


def test_the_denominator_is_bare_calls_not_every_call(tmp_path: Path) -> None:
    """`0 of 1677 CALLS` reads as a far wider sweep than `0 of 946 bare calls`.

    Only a bare-identifier call can be reached by a shadow; a member call was never at risk,
    and folding it into the denominator claims coverage the oracle does not have (§7).
    """
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    (tmp_path / "a.ts").write_text(
        "export function target(): void {}\n"
        "export class A {\n"
        "  helper(): void {}\n"
        "  run(): void { this.helper(); target(); }\n"
        "}\n",
        encoding="utf-8",
    )
    report = find_invented_calls(RepoCodeExtractor().extract(tmp_path), tmp_path)
    entry = next(e for e in report.by_language if e.language == "typescript")
    assert entry.total_calls == 2  # this.helper() and target()
    assert entry.shadowable == 1  # only target() is a bare identifier
    assert entry.invented == ()
