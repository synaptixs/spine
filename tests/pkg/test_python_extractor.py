"""PKG Layer 1: the Python front-end produces the right grounded facts."""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg import EdgeKind, FactBatch, NodeKind, PythonExtractor, RepoCodeExtractor

INVOICE = """\
from billing.tax import calc_tax


class Invoice:
    def total(self, items):
        return sum(items) + calc_tax(items)
"""


def _facts(tmp_path: Path, src: str = INVOICE, module: str = "billing.invoice") -> FactBatch:
    f = tmp_path / "invoice.py"
    f.write_text(src, encoding="utf-8")
    return PythonExtractor().extract(path=f, module=module, rel="src/billing/invoice.py")


def test_emits_module_type_function_nodes(tmp_path: Path) -> None:
    batch = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}

    assert by_id["py:billing.invoice"].kind is NodeKind.MODULE
    assert by_id["py:billing.invoice.Invoice"].kind is NodeKind.TYPE
    total = by_id["py:billing.invoice.Invoice.total"]
    assert total.kind is NodeKind.FUNCTION
    assert total.grounded and str(total.provenance) == "src/billing/invoice.py:5"


def test_import_contains_and_call_edges(tmp_path: Path) -> None:
    batch = _facts(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}

    assert ("py:billing.invoice", "py:billing.tax.calc_tax", EdgeKind.IMPORTS) in edges
    assert ("py:billing.invoice", "py:billing.invoice.Invoice", EdgeKind.CONTAINS) in edges
    assert ("py:billing.invoice.Invoice", "py:billing.invoice.Invoice.total", EdgeKind.CONTAINS) in edges
    # the call resolves to the *imported* symbol, not a bare guess
    assert ("py:billing.invoice.Invoice.total", "py:billing.tax.calc_tax", EdgeKind.CALLS) in edges


def test_builtins_are_not_emitted_as_calls(tmp_path: Path) -> None:
    batch = _facts(tmp_path)
    call_dsts = {e.dst for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert "py:sum" not in call_dsts  # sum() is a builtin → suppressed as noise


def test_self_method_call_resolves_to_sibling(tmp_path: Path) -> None:
    src = """\
class Svc:
    def run(self):
        return self.helper()

    def helper(self):
        return 1
"""
    batch = _facts(tmp_path, src=src, module="svc")
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("py:svc.Svc.run", "py:svc.Svc.helper", EdgeKind.CALLS) in edges


def test_module_level_call_resolves_to_local_def(tmp_path: Path) -> None:
    src = """\
def helper():
    return 1


def main():
    return helper()
"""
    batch = _facts(tmp_path, src=src, module="app")
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("py:app.main", "py:app.helper", EdgeKind.CALLS) in edges


def test_repo_extractor_skips_unparseable_and_ignored(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("def oops(:\n", encoding="utf-8")  # syntax error
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "junk.py").write_text("def g(): pass\n", encoding="utf-8")

    extractor = RepoCodeExtractor()
    batch = extractor.extract(tmp_path)
    ids = {n.id for n in batch.nodes}

    assert "py:good.f" in ids
    assert extractor.skipped == ["bad.py"]
    assert not any("junk" in i for i in ids)  # __pycache__ pruned


# ---- deeper comprehension: Field nodes + IMPLEMENTS edges (G1) -------------

MODEL = """\
from dataclasses import dataclass

from app.base import Base


@dataclass
class Account(Base):
    owner: str
    balance: int = 0

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self._cache = {}

    def deposit(self, amount: int) -> None:
        self.balance += amount
"""


def test_emits_field_nodes_for_class_attrs_and_self_assigns(tmp_path: Path) -> None:
    batch = _facts(tmp_path, MODEL, module="app.account")
    fields = {n.name: n for n in batch.nodes if n.kind is NodeKind.FIELD}
    # class-body annotated fields (dataclass) + self.<attr> in methods
    assert set(fields) == {"owner", "balance", "_cache"}
    contains = {(e.src, e.dst, e.kind) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("py:app.account.Account", "py:app.account.Account.balance", EdgeKind.CONTAINS) in contains
    # provenance points at a real line
    assert fields["owner"].grounded


def test_field_name_colliding_with_method_is_skipped(tmp_path: Path) -> None:
    src = (
        "class C:\n"
        "    value = 1\n"
        "    def value(self) -> int:\n"  # method named 'value' — wins
        "        return 2\n"
    )
    batch = _facts(tmp_path, src, module="m")
    kinds = {(n.name, n.kind) for n in batch.nodes if n.name == "value"}
    assert (("value", NodeKind.FUNCTION)) in kinds
    assert (("value", NodeKind.FIELD)) not in kinds


def test_implements_edge_for_resolved_base(tmp_path: Path) -> None:
    batch = _facts(tmp_path, MODEL, module="app.account")
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    # base `Base` resolves via the import → IMPLEMENTS edge
    assert ("py:app.account.Account", "py:app.base.Base") in impls


def test_unresolved_or_attribute_base_is_skipped(tmp_path: Path) -> None:
    src = "import typing\n\n\nclass G(typing.Generic):\n    pass\n"
    batch = _facts(tmp_path, src, module="m")
    assert not [e for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS]


def test_nested_class_self_attrs_not_attributed_to_outer(tmp_path: Path) -> None:
    src = (
        "class Outer:\n"
        "    def m(self) -> None:\n"
        "        self.a = 1\n"
        "        class Inner:\n"
        "            def n(self) -> None:\n"
        "                self.b = 2\n"
    )
    batch = _facts(tmp_path, src, module="m")
    outer_fields = {e.dst for e in batch.edges if e.kind is EdgeKind.CONTAINS and e.src == "py:m.Outer"}
    assert "py:m.Outer.a" in outer_fields
    assert "py:m.Outer.b" not in outer_fields  # belongs to Inner, not Outer


def test_module_name_is_per_extractor(tmp_path: Path) -> None:
    """The dispatcher asks each extractor for its module name (no Python path
    logic in the core) — Python collapses to a dotted qualname; the default is
    the repo-relative path."""
    from orchestrator.pkg.extractor import PythonExtractor, rel_module_name

    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "a" / "b.py"
    f.parent.mkdir(parents=True)
    f.write_text("x = 1\n", encoding="utf-8")
    assert PythonExtractor().module_name(f, tmp_path) == "a.b"
    assert rel_module_name(f, tmp_path) == "src/a/b.py"


# ---- relative imports: the level is preserved and resolved locally ---------


def test_relative_import_resolves_against_own_package(tmp_path: Path) -> None:
    f = tmp_path / "core.py"
    f.write_text("from .types import convert\n", encoding="utf-8")
    batch = PythonExtractor().extract(path=f, module="click.core", rel="src/click/core.py")
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    # NOT py:types.convert — the package prefix must survive
    assert ("py:click.core", "py:click.types.convert", EdgeKind.IMPORTS) in edges


def test_relative_import_in_init_resolves_to_own_package(tmp_path: Path) -> None:
    f = tmp_path / "__init__.py"
    f.write_text("from . import core\nfrom .core import Context\n", encoding="utf-8")
    batch = PythonExtractor().extract(path=f, module="click", rel="src/click/__init__.py")
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("py:click", "py:click.core", EdgeKind.IMPORTS) in edges
    assert ("py:click", "py:click.core.Context", EdgeKind.IMPORTS) in edges


def test_double_dot_import_climbs_one_package(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text("from ..types import convert\n", encoding="utf-8")
    batch = PythonExtractor().extract(path=f, module="click.sub.mod", rel="src/click/sub/mod.py")
    edges = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("py:click.sub.mod", "py:click.types.convert") in edges


def test_stdlib_shadow_is_not_conflated_with_relative(tmp_path: Path) -> None:
    f = tmp_path / "core.py"
    f.write_text("import types\nfrom .types import convert\n", encoding="utf-8")
    batch = PythonExtractor().extract(path=f, module="click.core", rel="src/click/core.py")
    dsts = {e.dst for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert "py:types" in dsts  # the stdlib module, absolute
    assert "py:click.types.convert" in dsts  # the sibling, package-qualified


def test_relative_import_escaping_the_tree_keeps_its_dots(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text("from ...outside import thing\n", encoding="utf-8")
    batch = PythonExtractor().extract(path=f, module="pkg.mod", rel="pkg/mod.py")
    dsts = {e.dst for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    # climbs past the scanned tree: stays visibly relative, can never join or shadow
    assert "py:...outside.thing" in dsts


def test_relatively_imported_symbol_call_resolves(tmp_path: Path) -> None:
    src = "from .tax import calc\n\n\ndef run():\n    return calc()\n"
    f = tmp_path / "mod.py"
    f.write_text(src, encoding="utf-8")
    batch = PythonExtractor().extract(path=f, module="billing.mod", rel="billing/mod.py")
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("py:billing.mod.run", "py:billing.tax.calc", EdgeKind.CALLS) in edges


def test_builtin_base_is_recorded_as_an_external_type(tmp_path: Path) -> None:
    """A class extending a builtin used to have no base at all in the graph, so the
    root of an exception hierarchy answered "extends nothing"."""
    src = "class AppError(Exception):\n    pass\n"
    batch = _facts(tmp_path, src, module="m")
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("py:m.AppError", "py:Exception", EdgeKind.IMPLEMENTS) in edges
    base = next(n for n in batch.nodes if n.id == "py:Exception")
    assert base.external and base.kind is NodeKind.TYPE


def test_walk_order_is_sorted_not_filesystem_order(tmp_path: Path) -> None:
    """Extraction order must not depend on the filesystem.

    `os.walk` yields entries in filesystem order, which differs between APFS and
    ext4. Order leaks into output via `Counter.most_common`, whose ties break on
    insertion order — so an unsorted walk made the committed knowledge base differ
    between a laptop and CI for the same commit.
    """
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    for name in ("zebra.py", "alpha.py", "middle.py"):
        (pkg / name).write_text("x = 1\n", encoding="utf-8")
    (pkg / "sub" / "nested.py").write_text("y = 2\n", encoding="utf-8")

    by_dir: dict[str, list[str]] = {}
    for p in RepoCodeExtractor()._iter_files(tmp_path):
        by_dir.setdefault(str(p.parent), []).append(p.name)

    for names in by_dir.values():
        assert names == sorted(names), f"walk not sorted: {names}"
    assert any("nested.py" in names for names in by_dir.values())  # descends too
