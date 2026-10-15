"""B54: a name a function binds itself is not the module-level import of the same name.

``def f(Store): Store.make()`` calls whatever the caller passed, ``Store = Rocket`` rebinds it, a
local ``class Store`` or ``from x import Rocket as Store`` is a different thing again. The per-file
call pass used to look the leading name up in the file's imports regardless, and the receiver pass
then landed the invented ``Store.make`` on the ancestor that declares ``make`` — a confident edge
to a class the code never touches. Each test reads a small repository with the real extractor, as
``pkg extract`` does, so the repoint step is included.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from orchestrator.pkg import EdgeKind, RepoCodeExtractor
from orchestrator.pkg.extractor import default_extractors

STORE = """\
class Base:
    @classmethod
    def make(cls):
        return cls()


class Store(Base):
    pass


class Rocket:
    pass
"""

USE = """\
from app.store import Rocket, Store


def bare():
    return Store.make()


def constructs():
    return Store()


def by_param(Store):
    return Store.make()


def by_param_ctor(Store):
    return Store()


def by_assignment():
    Store = Rocket
    return Store.make()


def by_class():
    class Store:
        pass

    return Store()


def by_other_import():
    from app.store import Rocket as Store

    return Store.make()


def by_same_import():
    from app.store import Store

    return Store.make()


def by_loop():
    for Store in (Rocket,):
        Store.make()


def by_enclosing(Store):
    def inner():
        return Store.make()

    return inner


def sibling_unbound():
    def inner():
        return Store.make()

    return inner
"""


def _calls(tmp_path: Path) -> dict[str, set[str]]:
    root = tmp_path / "repo"
    for rel, src in {"app/__init__.py": "", "app/store.py": STORE, "app/use.py": USE}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(src), encoding="utf-8")
    batch = RepoCodeExtractor(default_extractors()).extract(root)
    out: dict[str, set[str]] = {}
    for e in batch.edges:
        if e.kind is EdgeKind.CALLS and e.src.startswith("py:app.use."):
            out.setdefault(e.src.removeprefix("py:app.use."), set()).add(e.dst)
    return out


def test_unshadowed_calls_keep_their_edges(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert calls["bare"] == {"py:app.store.Base.make"}
    assert calls["constructs"] == {"py:app.store.Store"}
    assert calls["sibling_unbound.inner"] == {"py:app.store.Base.make"}


def test_a_parameter_named_like_the_import_is_not_the_import(tmp_path: Path) -> None:
    calls = _calls(tmp_path)
    assert calls.get("by_param", set()) == set()
    assert calls.get("by_param_ctor", set()) == set()


def test_a_reassigned_name_is_not_the_import(tmp_path: Path) -> None:
    assert _calls(tmp_path).get("by_assignment", set()) == set()


def test_a_loop_variable_is_not_the_import(tmp_path: Path) -> None:
    assert _calls(tmp_path).get("by_loop", set()) == set()


def test_a_local_class_is_not_the_import(tmp_path: Path) -> None:
    assert _calls(tmp_path).get("by_class", set()) == set()


def test_a_local_import_of_another_target_is_not_the_module_import(tmp_path: Path) -> None:
    assert _calls(tmp_path).get("by_other_import", set()) == set()


def test_a_local_import_of_the_same_target_keeps_the_edge(tmp_path: Path) -> None:
    assert _calls(tmp_path)["by_same_import"] == {"py:app.store.Base.make"}


def test_a_nested_function_inherits_its_enclosing_function_locals(tmp_path: Path) -> None:
    assert _calls(tmp_path).get("by_enclosing.inner", set()) == set()
