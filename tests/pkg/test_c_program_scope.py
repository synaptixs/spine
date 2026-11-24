"""B142 (SSPN-339): same-named definitions in separate C/C++ programs are separate nodes.

C and C++ name a file-scope symbol by its name alone (`c:loop`, `c:main`), so a repository that
holds several programs, each with its own `main` and its own global `loop`, got one node for all
of them: libuv's `c:main` was claimed by 34 files, and its owning module and its file disagreed.

A name is split into per-file ids (`c:<file>::<name>`, the form `static` symbols already have)
only when nothing is lost: it is *defined* in two or more source files, no header and no
non-defining source file declares it, and every edge into it comes from one of the files that
define it. Otherwise it stays one node, because some caller links to "the" definition and the
graph cannot tell which (libuv's `uv_close`, defined in `src/unix` and `src/win` and called from
`test/`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_c", reason="install the 'c' extra")

from orchestrator.pkg.c_extractor import CExtractor  # noqa: E402
from orchestrator.pkg.extractor import LanguageExtractor, RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind  # noqa: E402


def _repo(
    tmp_path: Path, files: dict[str, str], extractors: list[LanguageExtractor] | None = None
) -> FactBatch:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return RepoCodeExtractor(extractors=extractors or [CExtractor()]).extract(tmp_path)


def _ids(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes}


def _edges(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


PROG = """\
int loop;

static int tick(void) {
  return loop;
}

int helper(void) {
  return tick();
}

int main(void) {
  return helper();
}
"""


def test_two_programs_globals_are_two_nodes(tmp_path: Path) -> None:
    """T21. Each program's `int loop;` is its own node, owned by its own module."""
    batch = _repo(tmp_path, {"prog1/main.c": PROG, "prog2/main.c": PROG})

    assert "c:loop" not in _ids(batch)
    assert {"c:prog1/main.c::loop", "c:prog2/main.c::loop"} <= _ids(batch)
    contains = _edges(batch, EdgeKind.CONTAINS)
    assert ("c:prog1/main.c", "c:prog1/main.c::loop") in contains
    assert ("c:prog2/main.c", "c:prog2/main.c::loop") in contains


def test_two_programs_functions_keep_their_own_calls(tmp_path: Path) -> None:
    """T22. `main → helper` in each program stays inside that program; nothing crosses."""
    batch = _repo(tmp_path, {"prog1/main.c": PROG, "prog2/main.c": PROG})

    calls = _edges(batch, EdgeKind.CALLS)
    assert ("c:prog1/main.c::main", "c:prog1/main.c::helper") in calls
    assert ("c:prog2/main.c::main", "c:prog2/main.c::helper") in calls
    assert not any(dst == "c:helper" or src == "c:main" for src, dst in calls)
    assert ("c:prog1/main.c::main", "c:prog2/main.c::helper") not in calls


def test_a_split_node_keeps_its_own_file_and_line(tmp_path: Path) -> None:
    """T22b. Each split node's provenance is its own definition, not the first file read."""
    batch = _repo(tmp_path, {"prog1/main.c": PROG, "prog2/main.c": "\n\n" + PROG})
    by_id = {n.id: n for n in batch.nodes}

    one, two = by_id["c:prog1/main.c::main"], by_id["c:prog2/main.c::main"]
    assert one.provenance is not None and two.provenance is not None
    assert (one.provenance.file, one.provenance.line) == ("prog1/main.c", 11)
    assert (two.provenance.file, two.provenance.line) == ("prog2/main.c", 13)
    assert one.kind is NodeKind.FUNCTION and not one.external


def test_a_header_declaration_and_one_definition_stay_one_node(tmp_path: Path) -> None:
    """T23 (guard). `lib/api.h` declares, `lib/api.c` defines, `app.c` calls: one entity."""
    batch = _repo(
        tmp_path,
        {
            "lib/api.h": "int api(void);\n",
            "lib/api.c": '#include "api.h"\nint api(void) {\n  return 1;\n}\n',
            "app.c": '#include "lib/api.h"\nint run(void) {\n  return api();\n}\n',
        },
    )

    assert "c:api" in _ids(batch)
    assert ("c:run", "c:api") in _edges(batch, EdgeKind.CALLS)


def test_a_name_called_from_a_file_that_does_not_define_it_stays_merged(tmp_path: Path) -> None:
    """T24 (guard). Platform alternatives: `close` defined in `unix/` and `win/`, called from `test/`.

    Which definition the test links against is a build decision the source does not state, so
    splitting would have to drop the call or guess; the name stays one node and the call stays.
    """
    batch = _repo(
        tmp_path,
        {
            "unix/core.c": "int close_it(void) {\n  return 1;\n}\n",
            "win/core.c": "int close_it(void) {\n  return 2;\n}\n",
            "test/t.c": "int close_it(void);\nint t(void) {\n  return close_it();\n}\n",
        },
    )

    assert "c:close_it" in _ids(batch)
    assert ("c:t", "c:close_it") in _edges(batch, EdgeKind.CALLS)
    assert not any("::close_it" in i for i in _ids(batch))


def test_a_call_from_a_file_that_neither_defines_nor_declares_keeps_the_name_whole(tmp_path: Path) -> None:
    """T24c (guard). Rule 3 on its own: an implicit call (no prototype) from a third file."""
    batch = _repo(
        tmp_path,
        {
            "unix/core.c": "int close_it(void) {\n  return 1;\n}\n",
            "win/core.c": "int close_it(void) {\n  return 2;\n}\n",
            "test/t.c": "int t(void) {\n  return close_it();\n}\n",
        },
    )

    assert "c:close_it" in _ids(batch)
    assert ("c:t", "c:close_it") in _edges(batch, EdgeKind.CALLS)


def test_a_name_a_header_declares_stays_merged(tmp_path: Path) -> None:
    """T24b (guard). Two definitions and a shared header prototype: the header names one entity."""
    batch = _repo(
        tmp_path,
        {
            "include/h.h": "int helper(void);\n",
            "a/x.c": '#include "../include/h.h"\nint helper(void) {\n  return 1;\n}\n',
            "b/x.c": '#include "../include/h.h"\nint helper(void) {\n  return 2;\n}\n',
        },
    )

    assert "c:helper" in _ids(batch)


def test_a_static_symbol_keeps_its_file_scoped_id(tmp_path: Path) -> None:
    """T25 (guard). `static int tick` was file-scoped already, and is untouched."""
    batch = _repo(tmp_path, {"prog1/main.c": PROG, "prog2/main.c": PROG})

    assert {"c:prog1/main.c::tick", "c:prog2/main.c::tick"} <= _ids(batch)
    assert ("c:prog1/main.c::helper", "c:prog1/main.c::tick") in _edges(batch, EdgeKind.CALLS)


def test_a_struct_split_takes_its_fields_along(tmp_path: Path) -> None:
    """T26. Two programs each define `struct state`; each program's fields follow its own type."""
    src = "struct state {\n  int n;\n};\n\nint main(void) {\n  struct state s;\n  return s.n;\n}\n"
    batch = _repo(tmp_path, {"p1/m.c": src, "p2/m.c": src})
    ids = _ids(batch)

    assert "c:state" not in ids and "c:state.n" not in ids
    assert {"c:p1/m.c::state", "c:p1/m.c::state.n", "c:p2/m.c::state", "c:p2/m.c::state.n"} <= ids
    contains = _edges(batch, EdgeKind.CONTAINS)
    assert ("c:p1/m.c::state", "c:p1/m.c::state.n") in contains


def test_the_split_is_the_same_whatever_order_the_files_are_read_in(tmp_path: Path) -> None:
    """T27 (determinism). Same input, two runs, and the file set mirrored: same ids and edges."""
    files = {"prog1/main.c": PROG, "prog2/main.c": PROG, "zz/main.c": PROG}
    first = _repo(tmp_path / "a", files)
    second = _repo(tmp_path / "b", dict(reversed(list(files.items()))))

    def facts(b: FactBatch) -> tuple[set[str], set[tuple[str, str, str]]]:
        return _ids(b), {(e.src, e.dst, e.kind.value) for e in b.edges}

    assert facts(first) == facts(second)


def test_cpp_definitions_in_two_programs_are_split_too(tmp_path: Path) -> None:
    """T26b. The same rule for C++: two test programs each define a `Fixture` and a `run`."""
    pytest.importorskip("tree_sitter_cpp", reason="install the 'cpp' extra")
    from orchestrator.pkg.cpp_extractor import CppExtractor

    src = "struct Fixture {\n  int go() { return 1; }\n};\n\nint run() {\n  Fixture f;\n  return f.go();\n}\n"
    batch = _repo(tmp_path, {"t1/a.cpp": src, "t2/a.cpp": src}, [CppExtractor()])
    ids = _ids(batch)

    assert "cpp:run" not in ids and "cpp:Fixture" not in ids
    assert {
        "cpp:t1/a.cpp::run",
        "cpp:t2/a.cpp::run",
        "cpp:t1/a.cpp::Fixture",
        "cpp:t1/a.cpp::Fixture::go",
    } <= ids
