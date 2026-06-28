"""PKG: the C++ front-end — a C superset adding classes/namespaces/inheritance (Track 3).

Reuses the C track's include graph + header-merge; adds the OO layer. tree-sitter-cpp
is an optional extra, so these skip when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.cpp_extractor import CppExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind

pytest.importorskip("tree_sitter_cpp", reason="install the 'cpp' extra")


def _extract(root: Path, *rels: str) -> FactBatch:
    ex = CppExtractor()
    batch = FactBatch()
    for rel in rels:
        f = root / rel
        batch.merge(ex.extract(path=f, module=ex.module_name(f, root), rel=rel))
    return batch


def _write(root: Path, rel: str, src: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(src, encoding="utf-8")


SHAPE_HPP = """\
#ifndef SHAPE_HPP
#define SHAPE_HPP
namespace geo {
class Shape {
public:
    virtual double area() const = 0;
    int id;
};
class Circle : public Shape {
    double r;
public:
    Circle(double radius);
    double area() const override;
};
struct Line { Shape* start; Shape* end; };
enum Axis { X, Y };
}
template <typename T> class Box { T value; public: T get(); };
int helper(int n);
#endif
"""

SHAPES_CPP = """\
#include "shape.hpp"
namespace geo {
double Circle::area() const { return helper(3) * 3.14; }
}
int helper(int n) { return n * 2; }
int compute(int n) { return helper(n) + n; }
"""


def _shapes(tmp_path: Path, order: tuple[str, ...] = ("shape.hpp", "shapes.cpp")) -> FactBatch:
    _write(tmp_path, "shape.hpp", SHAPE_HPP)
    _write(tmp_path, "shapes.cpp", SHAPES_CPP)
    return _extract(tmp_path, *order)


def test_namespaced_classes_structs_enums(tmp_path: Path) -> None:
    by_id = {n.id: n for n in _shapes(tmp_path).nodes}
    assert by_id["cpp:geo::Shape"].kind is NodeKind.TYPE
    assert by_id["cpp:geo::Circle"].kind is NodeKind.TYPE
    assert by_id["cpp:geo::Line"].kind is NodeKind.TYPE
    assert by_id["cpp:geo::Axis"].kind is NodeKind.TYPE
    assert by_id["cpp:Box"].kind is NodeKind.TYPE  # template class
    # members: a method, a field, an enum constant
    assert by_id["cpp:geo::Circle::area"].kind is NodeKind.FUNCTION
    assert by_id["cpp:geo::Circle::r"].kind is NodeKind.FIELD
    assert by_id["cpp:geo::Axis::X"].kind is NodeKind.FIELD


def test_inheritance_is_implements(tmp_path: Path) -> None:
    edges = {(e.src, e.dst) for e in _shapes(tmp_path).edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("cpp:geo::Circle", "cpp:geo::Shape") in edges  # base class, namespace-resolved


@pytest.mark.parametrize("order", [("shape.hpp", "shapes.cpp"), ("shapes.cpp", "shape.hpp")])
def test_out_of_line_method_merges_to_definition(tmp_path: Path, order: tuple[str, ...]) -> None:
    # the in-class declaration and the out-of-line `Circle::area` definition collapse
    # to one node whose provenance is the definition — regardless of file order.
    area = {n.id: n for n in _shapes(tmp_path, order).nodes}["cpp:geo::Circle::area"]
    assert area.grounded is True
    assert area.provenance is not None and area.provenance.file == "shapes.cpp"


def test_calls_free_and_member(tmp_path: Path) -> None:
    calls = {(e.src, e.dst) for e in _shapes(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert ("cpp:compute", "cpp:helper") in calls  # free → free
    assert ("cpp:geo::Circle::area", "cpp:helper") in calls  # member → free


def test_references_member_of_type_excludes_template_param(tmp_path: Path) -> None:
    refs = {(e.src, e.dst) for e in _shapes(tmp_path).edges if e.kind is EdgeKind.REFERENCES}
    assert ("cpp:geo::Line", "cpp:geo::Shape") in refs  # struct member of class type
    assert ("cpp:Box", "cpp:T") not in refs  # T is a template parameter, not a type


def test_include_resolution(tmp_path: Path) -> None:
    batch = _shapes(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("cpp:shapes.cpp", "cpp:shape.hpp") in imports
    assert by_id["cpp:shape.hpp"].grounded is True  # local header resolved in-repo


def test_multiple_inheritance(tmp_path: Path) -> None:
    src = "struct A {}; struct B {}; class C : public A, public B { int x; };\n"
    _write(tmp_path, "multi.cpp", src)
    edges = {(e.src, e.dst) for e in _extract(tmp_path, "multi.cpp").edges if e.kind is EdgeKind.IMPLEMENTS}
    assert ("cpp:C", "cpp:A") in edges and ("cpp:C", "cpp:B") in edges


def test_out_of_line_qualified_name_normalizes_whitespace(tmp_path: Path) -> None:
    # Out-of-line definitions often wrap the line between `::` and the name; the node
    # id/name must not carry that newline/indentation (`Conn::\n  read` → `Conn::read`).
    _write(tmp_path, "conn.cpp", "struct Conn { int read(); };\nint Conn::\n    read() { return 0; }\n")
    by_id = {n.id: n for n in _extract(tmp_path, "conn.cpp").nodes}
    assert "cpp:Conn::read" in by_id
    assert by_id["cpp:Conn::read"].name == "read"  # no embedded whitespace
    assert not any("\n" in n.id or "\n" in n.name for n in by_id.values())
