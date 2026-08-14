"""PKG: the C front-end maps C source onto the universal facts (Track 2).

The new model: the translation unit (file) is the module; free functions, types and
globals hang off it. tree-sitter-c is an optional extra, so these skip when absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.c_extractor import CExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind

pytest.importorskip("tree_sitter_c", reason="install the 'c' extra")


def _extract(root: Path, *rels: str) -> FactBatch:
    """Extract each file under ``root`` and merge (as RepoCodeExtractor would)."""
    ex = CExtractor()
    batch = FactBatch()
    for rel in rels:
        f = root / rel
        batch.merge(ex.extract(path=f, module=ex.module_name(f, root), rel=rel))
    return batch


def _write(root: Path, rel: str, src: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(src, encoding="utf-8")


GEOMETRY_H = """\
#ifndef GEOMETRY_H
#define GEOMETRY_H
typedef struct Point { int x; int y; } Point;
struct Line { Point start; Point *end; };
enum Axis { X, Y };
int add(int a, int b);
double distance(Point p);
#endif
"""

GEOMETRY_C = """\
#include <math.h>
#include "geometry.h"

int origin_count;

int add(int a, int b) { return a + b; }

static int square(int n) { return n * n; }

double distance(Point p) {
    int s = square(p.x);
    return add(s, p.y);
}
"""


def _geometry(tmp_path: Path, order: tuple[str, ...] = ("geometry.h", "geometry.c")) -> FactBatch:
    _write(tmp_path, "geometry.h", GEOMETRY_H)
    _write(tmp_path, "geometry.c", GEOMETRY_C)
    return _extract(tmp_path, *order)


def test_module_name_is_the_relative_path(tmp_path: Path) -> None:
    _write(tmp_path, "src/util.c", "int f(void) { return 0; }\n")
    ex = CExtractor()
    assert ex.module_name(tmp_path / "src/util.c", tmp_path) == "src/util.c"


def test_types_and_members(tmp_path: Path) -> None:
    by_id = {n.id: n for n in _geometry(tmp_path).nodes}
    # struct (via typedef), struct (tag), and enum are all Types
    assert by_id["c:Point"].kind is NodeKind.TYPE
    assert by_id["c:Line"].kind is NodeKind.TYPE
    assert by_id["c:Axis"].kind is NodeKind.TYPE
    # struct members + enum constants are Fields
    assert by_id["c:Point.x"].kind is NodeKind.FIELD
    assert by_id["c:Point.y"].kind is NodeKind.FIELD
    assert by_id["c:Axis.X"].kind is NodeKind.FIELD


def test_free_functions_contained_by_module(tmp_path: Path) -> None:
    batch = _geometry(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["c:add"].kind is NodeKind.FUNCTION
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    # declared in the header, defined in the source → contained by BOTH translation units
    assert ("c:geometry.c", "c:add") in contains
    assert ("c:geometry.h", "c:add") in contains


@pytest.mark.parametrize("order", [("geometry.h", "geometry.c"), ("geometry.c", "geometry.h")])
def test_header_source_merge_prefers_definition(tmp_path: Path, order: tuple[str, ...]) -> None:
    # The .h prototype and the .c definition collapse to ONE node whose provenance is
    # the definition — regardless of which file is parsed first.
    by_id = {n.id: n for n in _geometry(tmp_path, order).nodes}
    add = by_id["c:add"]
    assert add.grounded is True
    assert add.provenance is not None and add.provenance.file == "geometry.c"


def test_static_function_is_file_scoped(tmp_path: Path) -> None:
    by_id = {n.id: n for n in _geometry(tmp_path).nodes}
    # internal linkage → keyed by file so same-named statics in other files don't merge
    assert "c:geometry.c::square" in by_id
    assert by_id["c:geometry.c::square"].kind is NodeKind.FUNCTION
    assert "c:square" not in by_id


def test_global_variable_is_a_field(tmp_path: Path) -> None:
    by_id = {n.id: n for n in _geometry(tmp_path).nodes}
    assert by_id["c:origin_count"].kind is NodeKind.FIELD


def test_include_resolution(tmp_path: Path) -> None:
    batch = _geometry(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("c:geometry.c", "c:geometry.h") in imports
    assert ("c:geometry.c", "c:math.h") in imports
    # a "local.h" that exists in-repo is grounded; a <system.h> is external
    assert by_id["c:geometry.h"].grounded is True
    assert by_id["c:math.h"].external is True


def test_missing_quoted_include_is_external(tmp_path: Path) -> None:
    _write(tmp_path, "a.c", '#include "nowhere.h"\nint f(void){return 0;}\n')
    by_id = {n.id: n for n in _extract(tmp_path, "a.c").nodes}
    assert by_id["c:nowhere.h"].external is True  # not found in-repo → external


def test_calls_resolve_global_and_local_static(tmp_path: Path) -> None:
    calls = {(e.src, e.dst) for e in _geometry(tmp_path).edges if e.kind is EdgeKind.CALLS}
    assert ("c:distance", "c:add") in calls  # global (external linkage)
    assert ("c:distance", "c:geometry.c::square") in calls  # local static → file-scoped id


def test_references_struct_member_type(tmp_path: Path) -> None:
    refs = {(e.src, e.dst) for e in _geometry(tmp_path).edges if e.kind is EdgeKind.REFERENCES}
    # `struct Line { Point start; Point *end; }` references Point (value and pointer)
    assert ("c:Line", "c:Point") in refs


def test_union_and_anonymous_typedef(tmp_path: Path) -> None:
    src = "typedef union { int i; float f; } Value;\ntypedef struct { int w; } Widget;\n"
    _write(tmp_path, "u.c", src)
    by_id = {n.id: n for n in _extract(tmp_path, "u.c").nodes}
    assert by_id["c:Value"].kind is NodeKind.TYPE  # typedef of anonymous union
    assert by_id["c:Value.i"].kind is NodeKind.FIELD
    assert by_id["c:Widget"].kind is NodeKind.TYPE  # typedef of anonymous struct


def test_a_call_through_a_function_pointer_invents_nothing(tmp_path: Path) -> None:
    """`cb()` where `cb` is a parameter used to emit `c:cb` — a global function that does not
    exist. The same invention the Python front-end made for `py:echo`, in a second front-end.
    """
    _write(
        tmp_path,
        "src/a.c",
        "int handle(void) { return 1; }\n\n"
        "int run(int (*cb)(void)) { return cb(); }\n\n"
        "int direct(void) { return handle(); }\n",
    )
    batch = _extract(tmp_path, "src/a.c")
    targets = {e.dst for e in batch.edges if e.kind is EdgeKind.CALLS}

    assert "c:cb" not in targets, "a function-pointer parameter is not a global function"
    assert "c:handle" in targets, "a direct call must survive"


def test_a_cross_translation_unit_call_still_resolves(tmp_path: Path) -> None:
    """The regression the fix had to avoid, and why C could not copy Python's one-liner.

    In C an unresolved callee is usually NOT a defect — it is declared in a header and linked
    from another translation unit, which is the normal case. Skipping everything unresolved,
    as Python now does, would silence every cross-TU call in every C repository. The test is
    "did this function bind the name", not "can we resolve it".
    """
    _write(
        tmp_path,
        "src/a.c",
        "#include <stdio.h>\n\n"
        "int helper(void) { return 1; }\n\n"
        "int go(int (*cb)(void), int n) {\n"
        "    int y = helper();\n"
        '    printf("%d", y);\n'
        "    other_tu_function(n);\n"
        "    return cb();\n"
        "}\n",
    )
    batch = _extract(tmp_path, "src/a.c")
    targets = {e.dst for e in batch.edges if e.kind is EdgeKind.CALLS}

    assert {"c:helper", "c:printf", "c:other_tu_function"} <= targets
    assert "c:cb" not in targets
    assert "c:y" not in targets, "an initializer binds y; the call inside it is still a call"
