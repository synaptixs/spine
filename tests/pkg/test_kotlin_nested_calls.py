"""N17: a bare constructor call to a nested class lands on the nested class, not a phantom.

``Inner()`` written inside ``Outer`` (or inside a class nested in it) names ``Outer.Inner`` by
Kotlin's scoping. The call pass built ``java:<package>.Inner`` from the simple name alone, so the
edge went to a type that does not exist and an external placeholder of that name appeared in the
graph. Each test reads a small repository with the real extractor, finalize included.

tree-sitter-kotlin is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from orchestrator.pkg import EdgeKind
from orchestrator.pkg.extractor import RepoCodeExtractor, default_extractors
from orchestrator.pkg.facts import FactBatch

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

SRC = """\
package a

class Outer {
    class Inner {
        fun run(): Int {
            return 1
        }

        fun recreate(): Outer {
            return Outer()
        }
    }

    class Sibling {
        fun fromSibling(): Inner {
            return Inner()
        }

        class Deep {
            fun deep(): Inner {
                return Inner()
            }
        }
    }

    fun inOuter(): Inner {
        return Inner()
    }
}

class Shadow {
    class Box

    fun make(): Box {
        return Box()
    }
}

class Box

class Holder {
    fun build(): Outer {
        return Outer()
    }

    fun other(): Box {
        return Box()
    }
}

fun qualified(): Outer.Inner {
    return Outer.Inner()
}
"""


def _graph(tmp_path: Path) -> FactBatch:
    root = tmp_path / "repo"
    (root / "a").mkdir(parents=True)
    (root / "a" / "A.kt").write_text(textwrap.dedent(SRC), encoding="utf-8")
    return RepoCodeExtractor(default_extractors()).extract(root)


def _calls(tmp_path: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for e in _graph(tmp_path).edges:
        if e.kind is EdgeKind.CALLS:
            out.setdefault(e.src, set()).add(e.dst)
    return out


def test_a_nested_class_is_constructed_by_its_nested_id_from_the_outer_class(tmp_path: Path) -> None:
    assert _calls(tmp_path)["java:a.Outer.inOuter"] == {"java:a.Outer.Inner"}


def test_a_sibling_nested_class_reaches_it_through_the_shared_outer_class(tmp_path: Path) -> None:
    assert _calls(tmp_path)["java:a.Outer.Sibling.fromSibling"] == {"java:a.Outer.Inner"}


def test_a_doubly_nested_class_reaches_it_through_every_enclosing_class(tmp_path: Path) -> None:
    assert _calls(tmp_path)["java:a.Outer.Sibling.Deep.deep"] == {"java:a.Outer.Inner"}


def test_a_nested_class_shadows_a_top_level_class_of_the_same_name(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert calls["java:a.Shadow.make"] == {"java:a.Shadow.Box"}
    assert calls["java:a.Holder.other"] == {"java:a.Box"}


def test_the_enclosing_class_constructor_stays_the_top_level_class(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert calls["java:a.Outer.Inner.recreate"] == {"java:a.Outer"}
    assert calls["java:a.Holder.build"] == {"java:a.Outer"}


def test_a_qualified_nested_constructor_is_unchanged(tmp_path: Path) -> None:
    assert _calls(tmp_path)["java:a.qualified"] == {"java:a.Outer.Inner"}


def test_no_phantom_placeholder_is_left_behind(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    assert [n.id for n in graph.nodes if n.external and n.id.startswith("java:a.")] == []
