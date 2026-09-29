"""B35: a Python call through a typed variable lands on the method its declared type means.

Each test builds a small repository on disk and reads it with the real extractor, so the rules
are checked through the same path `pkg extract` uses — re-exports and `finalize` included. The
corpus case `corpus/python/instance_calls` pins the same rules on one fixture; these pin each
refusal on its own, so a failure names the rule that broke.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from orchestrator.pkg import EdgeKind, RepoCodeExtractor
from orchestrator.pkg.extractor import default_extractors

STORE = """\
class Base:
    def shared(self):
        return 1

    @classmethod
    def make(cls):
        return cls()


class Store(Base):
    def get(self, key):
        return key


class Rocket:
    def get(self, key):
        return key
"""


def _calls(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    root = tmp_path / "repo"
    for rel, src in {"app/__init__.py": "", "app/store.py": STORE, **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(src), encoding="utf-8")
    batch = RepoCodeExtractor(default_extractors()).extract(root)
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def _from(calls: set[tuple[str, str]], caller: str) -> set[str]:
    return {dst for src, dst in calls if src == f"py:app.use.{caller}"}


GET, ROCKET_GET = "py:app.store.Store.get", "py:app.store.Rocket.get"


def test_an_annotated_parameter_types_the_call(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path, {"app/use.py": "from app.store import Store\ndef f(s: Store):\n    return s.get(1)\n"}
    )
    assert _from(calls, "f") == {GET}


def test_a_quoted_and_a_dotted_annotation_resolve(tmp_path: Path) -> None:
    src = """\
    import app.store as mod
    def quoted(s: "mod.Store"):
        return s.get(1)
    def dotted(s: mod.Store):
        return s.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert _from(calls, "quoted") == {GET}
    assert _from(calls, "dotted") == {GET}


def test_an_explicit_external_import_ends_the_lookup(tmp_path: Path) -> None:
    """`Store` from a third-party package is not the repository's `Store` (typed-receivers B1)."""
    calls = _calls(
        tmp_path, {"app/use.py": "from somelib import Store\ndef f(s: Store):\n    return s.get(1)\n"}
    )
    assert _from(calls, "f") == set()


def test_a_name_both_imported_and_defined_refuses(tmp_path: Path) -> None:
    src = """\
    from app.store import Store
    class Store:
        def get(self, key):
            return key
    def f(s: Store):
        return s.get(1)
    """
    assert _from(_calls(tmp_path, {"app/use.py": src}), "f") == set()


def test_a_pep695_type_parameter_shadows_a_class(tmp_path: Path) -> None:
    src = "from app.store import Store\ndef f[Store](s: Store):\n    return s.get(1)\n"
    assert _from(_calls(tmp_path, {"app/use.py": src}), "f") == set()


def test_names_rebound_by_a_loop_comprehension_or_lambda_refuse(tmp_path: Path) -> None:
    src = """\
    from app.store import Store
    def loop(xs):
        s = Store()
        for s in xs:
            pass
        return s.get(1)
    def comp(xs):
        s = Store()
        return [s.get(1) for s in xs]
    def lam():
        s = Store()
        return (lambda s: s.get(1))(0)
    def walrus(xs):
        s = Store()
        if (s := xs):
            pass
        return s.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    for caller in ("loop", "comp", "lam", "walrus"):
        # Only the constructor call itself: no method edge through the rebound name.
        assert _from(calls, caller) == {"py:app.store.Store"}, caller


def test_a_local_from_a_function_call_is_not_typed(tmp_path: Path) -> None:
    src = """\
    from app.store import Store
    def factory():
        return Store()
    def f():
        s = factory()
        return s.get(1)
    """
    assert _from(_calls(tmp_path, {"app/use.py": src}), "f") == {"py:app.use.factory"}


def test_optional_unwraps_but_a_two_class_union_refuses(tmp_path: Path) -> None:
    src = """\
    from typing import Optional, Union
    from app.store import Rocket, Store
    def opt(s: Optional[Store]):
        return s.get(1)
    def union_none(s: Union[Store, None]):
        return s.get(1)
    def two(s: Union[Store, Rocket]):
        return s.get(1)
    def listed(s: list[Store]):
        return s.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert _from(calls, "opt") == {GET}
    assert _from(calls, "union_none") == {GET}
    assert _from(calls, "two") == set()
    assert _from(calls, "listed") == set()


def test_cls_super_and_chains_are_out_of_scope(tmp_path: Path) -> None:
    src = """\
    from app.store import Store
    class Sub(Store):
        def get(self, key):
            return super().get(key)
        @classmethod
        def build(cls):
            return cls.make()
    def chain(s: Store):
        return s.get(1).bit_length()
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert {d for s, d in calls if s == "py:app.use.Sub.get"} == set()
    assert {d for s, d in calls if s == "py:app.use.Sub.build"} == set()
    assert _from(calls, "chain") == {GET}  # the first link only


def test_an_inherited_self_call_lands_on_the_ancestor(tmp_path: Path) -> None:
    src = """\
    from app.store import Store
    class Sub(Store):
        def run(self):
            return self.shared()
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert {d for s, d in calls if s == "py:app.use.Sub.run"} == {"py:app.store.Base.shared"}


def test_nothing_is_claimed_past_an_external_base(tmp_path: Path) -> None:
    src = """\
    from somelib import Model
    class Thing(Model):
        def own(self):
            return self.validate()
    def f(t: Thing):
        t.own()
        return t.validate()
    def g():
        return Thing.parse()
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert _from(calls, "f") == {"py:app.use.Thing.own"}
    assert {d for s, d in calls if s == "py:app.use.Thing.own"} == set()
    # D6: no invented `Thing.parse` — an external base may define it, so the edge is dropped.
    assert _from(calls, "g") == set()


def test_an_inherited_classmethod_on_a_class_name_lands_on_its_owner(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path, {"app/use.py": "from app.store import Store\ndef f():\n    return Store.make()\n"}
    )
    assert _from(calls, "f") == {"py:app.store.Base.make"}


def test_two_bases_refuse(tmp_path: Path) -> None:
    src = """\
    from app.store import Rocket, Store
    class Both(Store, Rocket):
        pass
    def f(b: Both):
        return b.get(1)
    """
    assert _from(_calls(tmp_path, {"app/use.py": src}), "f") == set()


def test_fields_typed_by_annotation_constructor_or_parameter(tmp_path: Path) -> None:
    src = """\
    from app.store import Rocket, Store
    class Svc:
        annotated: Store
        def __init__(self, store: Store, maker):
            self.passed = store
            self.built = Rocket()
            self.unknown = maker()
            self.both = Store()
            self.both = Rocket()
        def a(self):
            return self.annotated.get(1)
        def p(self):
            return self.passed.get(1)
        def b(self):
            return self.built.get(1)
        def u(self):
            return self.unknown.get(1)
        def m(self):
            return self.both.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    by = {name: {d for s, d in calls if s == f"py:app.use.Svc.{name}"} for name in "apbum"}
    assert by == {"a": {GET}, "p": {GET}, "b": {ROCKET_GET}, "u": set(), "m": set()}


def test_the_same_tree_gives_the_same_edges(tmp_path: Path) -> None:
    files = {"app/use.py": "from app.store import Store\ndef f(s: Store):\n    return s.get(1)\n"}
    assert _calls(tmp_path / "one", files) == _calls(tmp_path / "two", files)
