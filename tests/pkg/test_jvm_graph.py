"""Four defects in the Java (and, for one, C#) graph, each measured on a real repository.

- B29: an enum's methods, fields and constructors were never extracted (`enum_body_declarations`).
  81 enums in one Java repository held 327 methods and 328 fields the graph did not know, and 524
  calls and creations inside them.
- B27: a base type the repository does not declare left its `IMPLEMENTS` edge dangling (270 in the
  same repository). Qualified names (`com.lib.Base`) are honest and only need a node; a simple name
  that fell to a same-package guess (`java:app.Runnable`) is an invented id and is repointed.
- N9: one `CONTAINS` edge per *declaration*, so each overload listed its method again
  (`children_of` returned one member 104 times).
- B25: a field and a method of the same name shared one id and the first declared won, so a class
  that declared the field first lost its method node and `z.length()` was refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import EdgeKind, NodeKind, RepoCodeExtractor
from orchestrator.pkg.facts import FactBatch, Node
from orchestrator.pkg.store import FactStore


def _extract(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _java(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    return _extract(tmp_path, files)


def _edges(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


def _node(batch: FactBatch, node_id: str) -> Node | None:
    return next((n for n in batch.nodes if n.id == node_id), None)


def _kind(batch: FactBatch, node_id: str) -> NodeKind | None:
    node = _node(batch, node_id)
    return node.kind if node is not None else None


# --- B29: enum members ----------------------------------------------------------------------------

KIND = """\
package a;
public enum Kind {
    X, Y;
    private final int value = 1;
    Kind() {}
    static Kind of(int t) { return new Util().make(t); }
    int get() { return value; }
    static class Helper { void h() {} }
}
"""


def test_an_enums_methods_fields_constructors_and_nested_types_are_extracted(tmp_path: Path) -> None:
    batch = _java(
        tmp_path,
        {
            "a/Kind.java": KIND,
            "a/Util.java": "package a;\nclass Util { Kind make(int t) { return Kind.X; } }\n",
        },
    )
    contained = {d for s, d in _edges(batch, EdgeKind.CONTAINS) if s == "java:a.Kind"}
    assert {
        "java:a.Kind.of",
        "java:a.Kind.get",
        "java:a.Kind.Kind",
        "java:a.Kind.value",
        "java:a.Kind.Helper",
    } <= contained
    assert _kind(batch, "java:a.Kind.of") is NodeKind.FUNCTION
    assert _kind(batch, "java:a.Kind.value") is NodeKind.FIELD
    assert ("java:a.Kind.Helper", "java:a.Kind.Helper.h") in _edges(batch, EdgeKind.CONTAINS)


def test_a_call_inside_an_enum_method_and_a_call_to_it_are_both_edges(tmp_path: Path) -> None:
    files = {
        "a/Kind.java": KIND,
        "a/Util.java": "package a;\nclass Util { Kind make(int t) { return Kind.X; } }\n",
        "a/Use.java": "package a;\nclass Use { Kind go() { return Kind.of(1); } }\n",
    }
    calls = _edges(_java(tmp_path, files), EdgeKind.CALLS)
    assert ("java:a.Kind.of", "java:a.Util") in calls  # `new Util()` inside the enum's method
    assert ("java:a.Use.go", "java:a.Kind.of") in calls  # a call *to* an enum's static method


def test_enum_constants_with_bodies_stay_unextracted(tmp_path: Path) -> None:
    """Pinned limit: only the enum's declared members are read, not an anonymous constant body."""
    src = "package a;\npublic enum Op {\n  PLUS { int apply() { return 1; } };\n  abstract int apply();\n}\n"
    batch = _java(tmp_path, {"a/Op.java": src})
    members = {d for s, d in _edges(batch, EdgeKind.CONTAINS) if s == "java:a.Op"}
    assert members == {"java:a.Op.apply"}


# --- B27: external bases --------------------------------------------------------------------------

MSG = "package a;\npublic class Msg extends com.lib.Base implements java.io.Closeable {}\n"
TASK = "package a;\npublic class Task implements Runnable { public void run() {} }\n"


def test_no_implements_edge_dangles(tmp_path: Path) -> None:
    files = {
        "a/Msg.java": MSG,
        "a/Task.java": TASK,
        "a/Named.java": "package a;\ninterface Named {}\n",
        "a/Impl.java": "package a;\nclass Impl implements Named {}\n",
    }
    batch = _java(tmp_path, files)
    ids = {n.id for n in batch.nodes}
    assert [e for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS and e.dst not in ids] == []


def test_a_qualified_external_base_keeps_its_id_and_gains_an_external_node(tmp_path: Path) -> None:
    batch = _java(tmp_path, {"a/Msg.java": MSG})
    assert ("java:a.Msg", "java:com.lib.Base") in _edges(batch, EdgeKind.IMPLEMENTS)
    base = _node(batch, "java:com.lib.Base")
    assert base is not None and base.external and base.kind is NodeKind.TYPE and base.name == "Base"


def test_an_invented_same_package_guess_is_repointed_to_the_name_as_written(tmp_path: Path) -> None:
    """`Runnable` is `java.lang`'s, not `app`'s: the id the per-file pass invented is not asserted."""
    batch = _java(tmp_path, {"a/Task.java": TASK})
    implements = _edges(batch, EdgeKind.IMPLEMENTS)
    assert ("java:a.Task", "java:Runnable") in implements
    assert ("java:a.Task", "java:a.Runnable") not in implements
    assert _node(batch, "java:a.Runnable") is None
    node = _node(batch, "java:Runnable")
    assert node is not None and node.external


def test_a_declared_base_is_left_alone(tmp_path: Path) -> None:
    files = {
        "a/Named.java": "package a;\ninterface Named {}\n",
        "a/Impl.java": "package a;\nclass Impl implements Named {}\n",
    }
    batch = _java(tmp_path, files)
    assert ("java:a.Impl", "java:a.Named") in _edges(batch, EdgeKind.IMPLEMENTS)
    assert _node(batch, "java:Named") is None


def test_external_bases_do_not_change_any_call(tmp_path: Path) -> None:
    """The external-base step runs after the calls are settled; the same repository with and
    without the extra base resolves the same calls."""
    use = "package a;\nclass Use { void go(Task t) { t.run(); } }\n"
    with_base = _edges(_java(tmp_path / "w", {"a/Task.java": TASK, "a/Use.java": use}), EdgeKind.CALLS)
    assert ("java:a.Use.go", "java:a.Task.run") in with_base


# --- N9: one CONTAINS per (parent, child) ---------------------------------------------------------

OVERLOADS = (
    "package a;\npublic class C {\n  int f;\n  void m(int a) {}\n  void m(String s) {}\n  void m() {}\n}\n"
)


def test_java_overloads_are_contained_once(tmp_path: Path) -> None:
    batch = _java(
        tmp_path, {"a/C.java": OVERLOADS, "a/U.java": "package a;\nclass U { void go(C c) { c.m(); } }\n"}
    )
    contains = [(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS]
    assert contains.count(("java:a.C", "java:a.C.m")) == 1
    assert [c.id for c in FactStore(batch).children_of("java:a.C")].count("java:a.C.m") == 1
    assert ("java:a.U.go", "java:a.C.m") in _edges(batch, EdgeKind.CALLS)  # the calls are untouched


def test_the_kept_edge_points_at_the_first_declaration(tmp_path: Path) -> None:
    batch = _java(tmp_path, {"a/C.java": OVERLOADS})
    edge = next(e for e in batch.edges if e.kind is EdgeKind.CONTAINS and e.dst == "java:a.C.m")
    assert edge.provenance is not None and edge.provenance.line == 4


def test_csharp_overloads_are_contained_once(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")
    src = (
        "namespace App;\npublic class C {\n  public void M(int a) {}\n  public void M(string s) {}\n"
        "  public int P { get; set; }\n}\n"
    )
    batch = _extract(tmp_path, {"C.cs": src})
    contains = [(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS]
    assert contains.count(("csharp:App.C", "csharp:App.C.M")) == 1
    assert contains.count(("csharp:App.C", "csharp:App.C.P")) == 1


# --- B25: a field and a method of the same name ----------------------------------------------------

USE_LENGTH = "package a;\nclass Use { int go(Lazy z) { return z.length(); } }\n"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("  private int length;\n  public int length() { return length; }\n", id="field-first"),
        pytest.param("  public int length() { return length; }\n  private int length;\n", id="method-first"),
    ],
)
def test_a_field_and_a_method_of_one_name_are_two_nodes_and_the_call_resolves(
    tmp_path: Path, body: str
) -> None:
    batch = _java(
        tmp_path, {"a/Lazy.java": f"package a;\npublic class Lazy {{\n{body}}}\n", "a/Use.java": USE_LENGTH}
    )
    method, field = _node(batch, "java:a.Lazy.length"), _node(batch, "java:a.Lazy.length#field")
    assert method is not None and method.kind is NodeKind.FUNCTION
    assert field is not None and field.kind is NodeKind.FIELD and field.name == "length"
    contained = {d for s, d in _edges(batch, EdgeKind.CONTAINS) if s == "java:a.Lazy"}
    assert {"java:a.Lazy.length", "java:a.Lazy.length#field"} <= contained
    assert ("java:a.Use.go", "java:a.Lazy.length") in _edges(batch, EdgeKind.CALLS)


def test_a_field_with_no_method_of_its_name_keeps_its_plain_id(tmp_path: Path) -> None:
    src = "package a;\npublic class Lazy {\n  private int size;\n  public int length() { return size; }\n}\n"
    batch = _java(tmp_path, {"a/Lazy.java": src})
    assert _node(batch, "java:a.Lazy.size") is not None
    assert _node(batch, "java:a.Lazy.size#field") is None
