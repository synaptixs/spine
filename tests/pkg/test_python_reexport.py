"""Python re-exports land on the defining symbol, never on an import-text placeholder (B20).

Before this, ``from app import Store; Store()`` produced ``CALLS -> py:app.Store`` — an external
node — while the class was grounded as ``py:app.store.Store``: 1,271 CALLS edges on this
repository alone. The corpus cases ``python/package_reexport`` and ``python/reexport_refusals``
carry the shapes end to end; these pin each rule of ``python_reexport`` on its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

from orchestrator.pkg import EdgeKind, FactBatch, RepoCodeExtractor
from orchestrator.pkg.facts import Edge, Node, NodeKind, Provenance
from orchestrator.pkg.python_reexport import ModuleExports, collect_exports, resolve_reexports


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _pairs(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


def _exports(source: str, base: str = "pkg") -> ModuleExports:
    def import_base(stmt: ast.ImportFrom) -> str:
        return f"{base}.{stmt.module}" if stmt.level else (stmt.module or "")

    return collect_exports(ast.parse(source), import_base)


# ---- the binding table ---------------------------------------------------------------------


def _targets(table: ModuleExports, name: str) -> list[tuple[str, bool]]:
    return [(b.target, b.unconditional) for b in table.bindings.get(name, [])]


def test_only_module_level_statements_bind() -> None:
    table = _exports(
        "from .a import kept\n"
        "if TYPE_CHECKING:\n    from .b import typed\n"
        "def __getattr__(name):\n    from .c import lazy\n    return lazy\n"
        "class K:\n    from .d import inner\n"
    )
    assert _targets(table, "kept") == [("py:pkg.a.kept", True)]
    assert _targets(table, "typed") == [("py:pkg.b.typed", False)]
    assert not {"lazy", "inner"} & set(table.bindings)
    assert table.defined == {"__getattr__", "K"}


def test_a_name_bound_twice_keeps_both_events_in_order() -> None:
    table = _exports("try:\n    from .fast import f\nexcept ImportError:\n    from .slow import f\n")
    assert _targets(table, "f") == [("py:pkg.fast.f", False), ("py:pkg.slow.f", False)]


def test_assignment_del_loops_and_handlers_are_unfollowable_bindings() -> None:
    table = _exports(
        "from .x import f\nf = wrap(f)\ndel g\nfor h in []: pass\nwith m() as w: pass\n"
        "try:\n    pass\nexcept E as err:\n    pass\nif (v := 1): pass\n"
    )
    assert _targets(table, "f") == [("py:pkg.x.f", True), ("<rebound>", True)]
    for name in ("g", "h", "w", "err", "v"):
        assert [t for t, _ in _targets(table, name)] == ["<rebound>"], name


def test_renames_plain_imports_stars_and_escaping_relatives() -> None:
    table = _exports("from .util import helper as assist\nimport a.b\nimport c.d as e\nfrom .m import *\n")
    assert _targets(table, "assist") == [("py:pkg.util.helper", True)]
    assert _targets(table, "a") == [("py:a", True)]
    assert _targets(table, "e") == [("py:c.d", True)]
    assert table.stars == [(4, True, "py:pkg.m")]

    def climbing(stmt: ast.ImportFrom) -> str:
        return "..outside"  # the extractor keeps the dots when an import climbs out of the tree

    escaped = collect_exports(ast.parse("from ...outside import f\n"), climbing)
    assert _targets(escaped, "f") == [("<rebound>", True)]


def test_all_is_read_only_when_it_is_one_literal() -> None:
    assert _exports('__all__ = ["A", "B"]\n').all_names == ("A", "B")
    assert _exports('__all__: list[str] = ["A"]\n').all_names == ("A",)
    unknowable = _exports("__all__ = names()\n").all_names
    assert unknowable is not None and unknowable != ("A",)
    assert _exports('__all__ = ["A"]\n__all__ += ["B"]\n').all_names == unknowable
    assert _exports('if X:\n    __all__ = ["a"]\nelse:\n    __all__ = ["a", "b"]\n').all_names == unknowable


# ---- resolution, end to end through the extractor ------------------------------------------


def test_a_package_reexport_lands_calls_imports_and_bases_on_the_definition(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/__init__.py": "from .store import Store\nfrom .base import Base\n",
            "app/store.py": "class Store:\n    def get(self):\n        return 1\n",
            "app/base.py": "class Base:\n    pass\n",
            "client.py": (
                "from app import Base, Store\n"
                "class Special(Base):\n    pass\n"
                "def make():\n    return Store()\n"
                "def member():\n    return Store.get(Store())\n"
            ),
        },
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    calls = _pairs(batch, EdgeKind.CALLS)
    assert ("py:client.make", "py:app.store.Store") in calls
    assert ("py:client.member", "py:app.store.Store.get") in calls
    assert ("py:client.Special", "py:app.base.Base") in _pairs(batch, EdgeKind.IMPLEMENTS)
    # D4: the import names the defining symbol, as a direct import would — not the package.
    assert ("py:client", "py:app.store.Store") in _pairs(batch, EdgeKind.IMPORTS)
    ids = {n.id for n in batch.nodes}
    assert not {"py:app.Store", "py:app.Base", "py:app.Store.get"} & ids  # placeholders dropped


def test_a_chain_through_a_subpackage_and_an_ordinary_module(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/__init__.py": "from .sub import Engine\n",
            "app/sub/__init__.py": "from .deep import Engine\n",
            "app/sub/deep.py": "class Engine:\n    pass\n",
            "app/util.py": "def helper():\n    return 2\n",
            "app/relay.py": "from app.util import helper\n",
            "client.py": (
                "from app import Engine\nfrom app.relay import helper\n"
                "def chain():\n    return Engine()\n"
                "def relay():\n    return helper()\n"
            ),
        },
    )
    calls = _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)
    assert ("py:client.chain", "py:app.sub.deep.Engine") in calls
    assert ("py:client.relay", "py:app.util.helper") in calls


def test_disagreeing_bindings_and_lazy_getattr_stay_on_the_placeholder(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "lib/__init__.py": (
                "try:\n    from .fast import compute\nexcept ImportError:\n    from .slow import compute\n"
                "def __getattr__(name):\n    from .lazy import Lazy\n    return Lazy\n"
            ),
            "lib/fast.py": "def compute():\n    return 1\n",
            "lib/slow.py": "def compute():\n    return 1\n",
            "lib/lazy.py": "class Lazy:\n    pass\n",
            "main.py": (
                "from lib import Lazy, compute\ndef a():\n    return compute()\ndef b():\n    return Lazy()\n"
            ),
        },
    )
    calls = _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)
    assert calls >= {("py:main.a", "py:lib.compute"), ("py:main.b", "py:lib.Lazy")}
    assert not calls & {
        ("py:main.a", "py:lib.fast.compute"),
        ("py:main.a", "py:lib.slow.compute"),
        ("py:main.b", "py:lib.lazy.Lazy"),
    }


def test_a_reexport_cycle_terminates_and_resolves_nothing(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "a.py": "from b import thing\n",
            "b.py": "from a import thing\n",
            "main.py": "from a import thing\ndef go():\n    return thing()\n",
        },
    )
    assert ("py:main.go", "py:a.thing") in _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)


def test_a_third_party_star_does_not_block_an_explicit_reexport(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/__init__.py": "from numpy import *\nfrom .store import Store\n",
            "app/store.py": "class Store:\n    pass\n",
            "client.py": "from app import Store\ndef go():\n    return Store()\n",
        },
    )
    calls = _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)
    assert ("py:client.go", "py:app.store.Store") in calls


def test_nothing_to_resolve_returns_the_same_batch() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:m", NodeKind.MODULE, "m", "python", Provenance("m.py", 1)))
    batch.add_node(Node("py:os.path.join", NodeKind.FUNCTION, "join", "python", external=True))
    batch.add_edge(Edge("py:m", "py:os.path.join", EdgeKind.CALLS))
    assert resolve_reexports(batch, {"py:m": ModuleExports()}) is batch


def _calls(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    _write(tmp_path, files)
    return _pairs(RepoCodeExtractor().extract(tmp_path), EdgeKind.CALLS)


def test_an_unknowable_star_after_an_explicit_reexport_refuses(tmp_path: Path) -> None:
    # At run time numpy's `array` (if it has one) replaces the explicit binding above it.
    calls = _calls(
        tmp_path,
        {
            "app/__init__.py": "from .mine import array\nfrom numpy import *\n",
            "app/mine.py": "def array():\n    return 1\n",
            "client.py": "from app import array\ndef go():\n    return array()\n",
        },
    )
    assert ("py:client.go", "py:app.array") in calls
    assert ("py:client.go", "py:app.mine.array") not in calls


def test_an_in_tree_star_with_an_unreadable_all_refuses_too(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path,
        {
            "app/__init__.py": "from .mine import g\nfrom .other import *\n",
            "app/mine.py": "def g():\n    return 1\n",
            "app/other.py": '__all__ = ["g"] + []\ndef g():\n    return 2\n',
            "client.py": "from app import g\ndef go():\n    return g()\n",
        },
    )
    assert ("py:client.go", "py:app.g") in calls


def test_a_star_of_a_star_does_not_block_an_explicit_reexport(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path,
        {
            "app/__init__.py": "from .a import *\nfrom .store import Store\n",
            "app/a.py": "from .c import *\n",
            "app/c.py": "def deep():\n    return 1\n",
            "app/store.py": "class Store:\n    pass\n",
            "client.py": "from app import Store, deep\ndef go():\n    Store()\n    deep()\n",
        },
    )
    assert {("py:client.go", "py:app.store.Store"), ("py:client.go", "py:app.c.deep")} <= calls


def test_rebinding_by_assignment_or_del_refuses(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path,
        {
            "app/__init__.py": (
                "try:\n    from ._fast import f\nexcept ImportError:\n"
                "    from ._slow import slow_f\n    f = slow_f\n"
                "from ._fast import g\ndel g\n"
            ),
            "app/_fast.py": "def f():\n    return 1\ndef g():\n    return 1\n",
            "app/_slow.py": "def slow_f():\n    return 2\n",
            "client.py": "from app import f, g\ndef go():\n    f()\n    g()\n",
        },
    )
    assert {("py:client.go", "py:app.f"), ("py:client.go", "py:app.g")} <= calls
    assert not {("py:client.go", "py:app._fast.f"), ("py:client.go", "py:app._fast.g")} & calls


def test_a_later_unconditional_binding_wins(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path,
        {
            "app/__init__.py": "f = None\nfrom .a import f\nfrom .a import h\nfrom .b import h\n",
            "app/a.py": "def f():\n    return 1\ndef h():\n    return 1\n",
            "app/b.py": "def h():\n    return 2\n",
            "client.py": "from app import f, h\ndef go():\n    f()\n    h()\n",
        },
    )
    assert {("py:client.go", "py:app.a.f"), ("py:client.go", "py:app.b.h")} <= calls


def test_bindings_that_agree_resolve(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path,
        {
            "app/__init__.py": "try:\n    from .a import f\nexcept ImportError:\n    from .a import f\n",
            "app/a.py": "def f():\n    return 1\n",
            "client.py": "from app import f\ndef go():\n    return f()\n",
        },
    )
    assert ("py:client.go", "py:app.a.f") in calls


def test_an_all_naming_something_never_bound_resolves_nothing(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path,
        {
            "app/__init__.py": "from .m import *\n",
            "app/m.py": '__all__ = ["ghost"]\n',
            "client.py": "from app import ghost\ndef go():\n    return ghost()\n",
        },
    )
    assert ("py:client.go", "py:app.ghost") in calls


def test_import_of_a_submodule_binds_the_package(tmp_path: Path) -> None:
    # `import app.store` binds `app`: `app.run()` is the package's own `run`, never
    # `app.store.run` — and the resolver must not follow that wrong id into app.engine.
    calls = _calls(
        tmp_path,
        {
            "app/__init__.py": "def run():\n    return 0\n",
            "app/store.py": "from .engine import run\n",
            "app/engine.py": "def run():\n    return 1\n",
            "client.py": "import app.store\ndef go():\n    return app.run()\n",
        },
    )
    assert ("py:client.go", "py:app.run") in calls
    assert not {("py:client.go", "py:app.store.run"), ("py:client.go", "py:app.engine.run")} & calls


def test_resolution_is_identical_across_hash_seeds(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    _write(
        tmp_path,
        {
            "app/__init__.py": "from .a import *\nfrom .b import *\nfrom .util import helper as assist\n",
            "app/a.py": "def one():\n    return 1\n",
            "app/b.py": "def two():\n    return 2\n",
            "app/util.py": "def helper():\n    return 3\n",
            "client.py": "from app import assist, one, two\ndef go():\n    one()\n    two()\n    assist()\n",
        },
    )
    script = (
        "import sys; from orchestrator.pkg import RepoCodeExtractor\n"
        "b = RepoCodeExtractor().extract(sys.argv[1])\n"
        "print(sorted((e.src, e.dst, e.kind.value, str(e.provenance)) for e in b.edges))\n"
        "print(sorted((n.id, n.kind.value, n.external) for n in b.nodes))\n"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("0", "1", "99")
    }
    assert len(runs) == 1
