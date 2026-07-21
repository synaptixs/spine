"""PKG: the Go front-end maps Go source onto the universal facts (phase 4.1).

tree-sitter-go is an optional extra, so these skip cleanly when it's absent. Go's twist:
the module is the PACKAGE = its directory, so every .go file in a dir merges into one
Module node (unlike every other front-end, which is one-module-per-file).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.facts import EdgeKind, Node, NodeKind
from orchestrator.pkg.go_extractor import GoExtractor

pytest.importorskip("tree_sitter_go", reason="install the 'go' extra")

SPAN = """\
package trace

import (
	"context"
	"fmt"
	o "go.opentelemetry.io/otel"
)

type Tracer interface {
	Start(ctx context.Context, name string) error
	Close() error
}

type SpanKind int

type recordingSpan struct {
	name   string
	a, b   int
	Embedded
}

func New(name string) *recordingSpan {
	return &recordingSpan{name: name}
}

func (s *recordingSpan) End() {
	fmt.Println(s.name)
}

func (s *recordingSpan) flush() error { return nil }
"""


def _facts(
    root: Path, rel: str = "trace/span.go", src: str = SPAN
) -> tuple[dict[str, Node], set[tuple[str, str, EdgeKind]], str]:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(src, encoding="utf-8")
    ex = GoExtractor()
    module = ex.module_name(f, root)
    batch = ex.extract(path=f, module=module, rel=rel)
    by_id = {n.id: n for n in batch.nodes}
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    return by_id, edges, module


def test_module_name_is_the_directory(tmp_path: Path) -> None:
    _, _, module = _facts(tmp_path)
    assert module == "trace"  # the package's directory, not the file


def test_emits_type_function_field_nodes(tmp_path: Path) -> None:
    by_id, _, _ = _facts(tmp_path)
    assert by_id["go:trace"].kind is NodeKind.MODULE
    assert by_id["go:trace.Tracer"].kind is NodeKind.TYPE  # interface
    assert by_id["go:trace.recordingSpan"].kind is NodeKind.TYPE  # struct
    assert by_id["go:trace.SpanKind"].kind is NodeKind.TYPE  # alias
    assert by_id["go:trace.New"].kind is NodeKind.FUNCTION  # top-level func
    assert by_id["go:trace.recordingSpan.End"].kind is NodeKind.FUNCTION  # method
    assert by_id["go:trace.recordingSpan.name"].kind is NodeKind.FIELD


def test_multi_name_fields_and_embedded_skipped(tmp_path: Path) -> None:
    by_id, _, _ = _facts(tmp_path)
    # `a, b int` → two Field nodes...
    assert by_id["go:trace.recordingSpan.a"].kind is NodeKind.FIELD
    assert by_id["go:trace.recordingSpan.b"].kind is NodeKind.FIELD
    # ...and the anonymous embedded field carries no name → no node in 4.1.
    assert "go:trace.recordingSpan.Embedded" not in by_id


def test_interface_methods_are_functions_under_the_interface(tmp_path: Path) -> None:
    by_id, edges, _ = _facts(tmp_path)
    assert by_id["go:trace.Tracer.Start"].kind is NodeKind.FUNCTION
    assert ("go:trace.Tracer", "go:trace.Tracer.Start", EdgeKind.CONTAINS) in edges


def test_method_owned_by_receiver_type(tmp_path: Path) -> None:
    # `func (s *recordingSpan) End()` → owned by recordingSpan, receiver `*T` stripped to T.
    _, edges, _ = _facts(tmp_path)
    assert ("go:trace.recordingSpan", "go:trace.recordingSpan.End", EdgeKind.CONTAINS) in edges


def test_imports_and_contains_edges(tmp_path: Path) -> None:
    _, edges, _ = _facts(tmp_path)
    assert ("go:trace", "go:context", EdgeKind.IMPORTS) in edges
    assert ("go:trace", "go:go.opentelemetry.io/otel", EdgeKind.IMPORTS) in edges  # aliased import
    assert ("go:trace", "go:trace.New", EdgeKind.CONTAINS) in edges


def test_calls_resolve_same_file_func_and_receiver_method(tmp_path: Path) -> None:
    src = (
        "package svc\n\n"
        "func helper() int { return 1 }\n\n"
        "type T struct{}\n"
        "func (t *T) run() { t.step(); helper() }\n"
        "func (t *T) step() {}\n"
    )
    _, edges, _ = _facts(tmp_path, rel="svc/s.go", src=src)
    # unqualified same-file func call, and a receiver-method call on the receiver's type.
    assert ("go:svc.T.run", "go:svc.helper", EdgeKind.CALLS) in edges
    assert ("go:svc.T.run", "go:svc.T.step", EdgeKind.CALLS) in edges


def test_calls_skip_unresolvable_selectors(tmp_path: Path) -> None:
    # A call on some other object (not the receiver) needs type inference → not emitted.
    src = "package svc\n\ntype T struct{ dep D }\ntype D struct{}\nfunc (t *T) run() { t.dep.Do() }\n"
    _, edges, _ = _facts(tmp_path, rel="svc/s.go", src=src)
    assert not [e for e in edges if e[2] is EdgeKind.CALLS]


def test_references_same_package_struct_field(tmp_path: Path) -> None:
    src = "package m\n\ntype Node struct {\n\tnext *Node\n\tval  int\n\tw    io.Writer\n}\n"
    _, edges, _ = _facts(tmp_path, rel="m/n.go", src=src)
    # named same-package field type → REFERENCES; builtin (int) and qualified (io.Writer) skipped.
    refs = {(e[0], e[1]) for e in edges if e[2] is EdgeKind.REFERENCES}
    assert refs == {("go:m.Node", "go:m.Node")}


def test_implements_by_method_set_across_files(tmp_path: Path) -> None:
    # The net-new algorithm: a concrete type implements an interface when its method
    # signatures (name+arity) ⊇ the interface's — even when split across files.
    from orchestrator.pkg.extractor import RepoCodeExtractor

    pkg = tmp_path / "store"
    pkg.mkdir()
    (pkg / "iface.go").write_text(
        "package store\n\ntype Repo interface {\n\tGet(id int) string\n\tPut(id int, v string)\n}\n"
    )
    (pkg / "impl.go").write_text(
        "package store\n\ntype Cache struct{}\n"
        'func (c *Cache) Get(id int) string { return "" }\n'
        "func (c *Cache) Put(id int, v string) {}\n"
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("go:store.Cache", "go:store.Repo") in impls


def test_implements_precision_arity_guards_false_positive(tmp_path: Path) -> None:
    # Same method NAMES but a different arity must NOT produce IMPLEMENTS (the guard that
    # separates a gRPC client's Do(a, b) from a server's Do(a)).
    from orchestrator.pkg.extractor import RepoCodeExtractor

    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "a.go").write_text("package p\n\ntype I interface {\n\tDo(a int, b int)\n}\n")
    (pkg / "b.go").write_text("package p\n\ntype C struct{}\nfunc (c C) Do(a int) {}\n")
    batch = RepoCodeExtractor().extract(tmp_path)
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("go:p.C", "go:p.I") not in impls


def test_package_is_the_directory_files_merge(tmp_path: Path) -> None:
    # Two files in the same dir share ONE Module node; their decls aggregate under it.
    from orchestrator.pkg.extractor import RepoCodeExtractor

    pkg = tmp_path / "sdk" / "trace"
    pkg.mkdir(parents=True)
    (pkg / "a.go").write_text("package trace\n\ntype A struct { x int }\n")
    (pkg / "b.go").write_text("package trace\n\ntype B struct { y int }\n")
    batch = RepoCodeExtractor().extract(tmp_path)
    modules = [n for n in batch.nodes if n.kind is NodeKind.MODULE and n.id == "go:sdk/trace"]
    assert len(modules) == 1  # one Module for the package/dir, not one per file
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["go:sdk/trace.A"].kind is NodeKind.TYPE
    assert by_id["go:sdk/trace.B"].kind is NodeKind.TYPE
    contained = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("go:sdk/trace", "go:sdk/trace.A") in contained
    assert ("go:sdk/trace", "go:sdk/trace.B") in contained


def test_generic_receiver_stripped(tmp_path: Path) -> None:
    src = "package col\n\ntype Stack[T any] struct { items []T }\n\nfunc (s *Stack[T]) Push(v T) {}\n"
    by_id, edges, _ = _facts(tmp_path, rel="col/stack.go", src=src)
    assert by_id["go:col.Stack.Push"].kind is NodeKind.FUNCTION
    assert ("go:col.Stack", "go:col.Stack.Push", EdgeKind.CONTAINS) in edges


def test_repo_extractor_dispatches_go_by_suffix(tmp_path: Path) -> None:
    from orchestrator.pkg.extractor import RepoCodeExtractor

    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n")
    (tmp_path / "m.py").write_text("def f() -> int:\n    return 1\n")
    batch = RepoCodeExtractor().extract(tmp_path)
    langs = {n.language for n in batch.nodes}
    assert "go" in langs and "python" in langs
