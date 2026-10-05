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


def test_self_that_is_not_the_instance_refuses(tmp_path: Path) -> None:
    """B53: `self` names the instance only while it is the method's first parameter and nothing
    rebinds it. A rebound `self`, a `@staticmethod` whose first parameter is `self`, and a
    `@classmethod` (where the first parameter is the class) must not land on an inherited method."""
    src = """\
    from app.store import Store
    class Sub(Store):
        def plain(self):
            return self.shared()
        def rebound(self, other):
            self = other
            return self.shared()
        def looped(self, items):
            for self in items:
                pass
            return self.shared()
        def nested(self, items):
            return [self.shared() for self in items]
        @staticmethod
        def static(self):
            return self.shared()
        @classmethod
        def klass(self):
            return self.shared()
        def __init__(self):
            self.store = Store()
        def field_rebound(self, other):
            self = other
            return self.store.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    by = {
        n: {d for s, d in calls if s == f"py:app.use.Sub.{n}"}
        for n in ("plain", "rebound", "looped", "nested", "static", "klass", "field_rebound")
    }
    assert by["plain"] == {"py:app.store.Base.shared"}
    assert {n: v for n, v in by.items() if n != "plain"} == {n: set() for n in by if n != "plain"}


def test_a_mixed_external_and_in_repo_base_refuses_in_either_order(tmp_path: Path) -> None:
    """`class W(Widget, Base)` and `class W(Base, Widget)`: the external base may override what the
    in-repo one declares, so neither claims `Base.shared` (B53 — nothing pinned this before)."""
    src = """\
    from somelib import Widget
    from app.store import Base
    class First(Widget, Base):
        pass
    class Second(Base, Widget):
        def own(self):
            return self.shared()
    def f(w: First):
        return w.shared()
    def g(w: Second):
        return w.shared()
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert _from(calls, "f") == set()
    assert _from(calls, "g") == set()
    assert {d for s, d in calls if s == "py:app.use.Second.own"} == set()


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


def test_two_processes_give_the_same_edges(tmp_path: Path) -> None:
    """Invariant 2 across interpreters: hash randomisation must not change what is emitted."""
    import json
    import os
    import subprocess
    import sys

    root = tmp_path / "repo"
    files = {
        "app/__init__.py": "",
        "app/store.py": STORE,
        "app/use.py": "from app.store import Rocket, Store\n"
        "class W(Store):\n"
        "    def go(self, r: Rocket, s: Store):\n"
        "        return self.shared(), r.get(1), s.get(1)\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    script = (
        "import json,sys;from pathlib import Path;from orchestrator.pkg import EdgeKind, RepoCodeExtractor;"
        "from orchestrator.pkg.extractor import default_extractors;"
        "b=RepoCodeExtractor(default_extractors()).extract(Path(sys.argv[1]));"
        "print(json.dumps([[e.src,e.dst] for e in b.edges if e.kind is EdgeKind.CALLS]))"
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", script, str(root)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("1", "2", "3")
    ]
    assert len(set(runs)) == 1
    assert len(json.loads(runs[0])) >= 3


def test_a_class_defined_inside_the_function_shadows_the_module_one(tmp_path: Path) -> None:
    """Review, real-repo smoke test: a function-local `class Model` is not the module's `Model`."""
    src = """\
    class Model:
        def run(self):
            return 1
    def f():
        class Model:
            def run(self):
                return 2
        m = Model()
        return m.run()
    def g(m: Model):
        return m.run()
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert "py:app.use.Model.run" not in _from(calls, "f")
    assert _from(calls, "g") == {"py:app.use.Model.run"}


def test_an_attribute_on_the_chain_shadows_an_inherited_method(tmp_path: Path) -> None:
    """Review, real-repo smoke test: Python runs the subclass attribute, not the base method."""
    src = """\
    def helper(self):
        return 3
    class Base:
        def m(self):
            return 1
        def n(self):
            return 2
    class Child(Base):
        m = helper
        def __init__(self):
            self.n = lambda: 2
        def run(self):
            return self.m() + self.n()
    def f(c: Child):
        return c.m() + c.n()
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    shadowed = {"py:app.use.Base.m", "py:app.use.Base.n"}
    assert not ({d for s, d in calls if s == "py:app.use.Child.run"} & shadowed)
    assert not (_from(calls, "f") & shadowed)


def test_a_parameter_annotation_is_read_where_the_def_runs(tmp_path: Path) -> None:
    """`def f(store: store.Store)` — the parameter shadows the module inside `f`, not in its
    own annotation (a shape the review's smoke test found in rich)."""
    src = "import app.store as store\ndef f(store: store.Store):\n    return store.get(1)\n"
    # (The per-file pass also emits `py:app.store.get`, reading `store` as the imported module —
    # a pre-existing edge, unchanged by B35.)
    assert GET in _from(_calls(tmp_path, {"app/use.py": src}), "f")


def test_a_property_on_the_chain_shadows_an_inherited_field(tmp_path: Path) -> None:
    src = """\
    from app.store import Rocket, Store
    class Base:
        def __init__(self):
            self.store = Store()
    class Sub(Base):
        @property
        def store(self):
            return Rocket()
        def go(self):
            return self.store.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert {d for s, d in calls if s == "py:app.use.Sub.go"} & {GET, ROCKET_GET} == set()


# ---- review pass 2: a name is typed only when every binding of it is one we read -----------


def test_every_unlisted_binding_form_refuses(tmp_path: Path) -> None:
    """The census counts each store generically, so forms nobody listed refuse too (H7)."""
    src = """\
    from app.store import Store
    def a(xs):
        s = Store()
        try:
            pass
        except* ValueError as s:
            pass
        return s.get(1)
    def b(xs):
        s = Store()
        match xs:
            case {**s}:
                pass
        return s.get(1)
    def c():
        s = Store()
        type s = int
        return s.get(1)
    def d(xs):
        s = Store()
        for s.attr in xs:
            pass
        return s.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    for caller in ("a", "b", "c"):
        assert GET not in _from(calls, caller), caller
    assert GET in _from(calls, "d")  # `s.attr` stores into s; `s` itself is still the Store


def test_a_nested_rebinding_refuses(tmp_path: Path) -> None:
    """`nonlocal` in an inner function, or `self.x = …` inside one, rebinds the outer name (H6)."""
    src = """\
    from app.store import Rocket, Store
    def outer(s: Store):
        def reset():
            nonlocal s
            s = Rocket()
        reset()
        return s.get(1)
    class Holder:
        def __init__(self):
            self.x = Store()
            def reset():
                self.x = Rocket()
            reset()
        def go(self):
            return self.x.get(1)
    class Pool:
        def __init__(self):
            self.conn = Store()
        def use(self):
            with open("f") as self.conn:
                pass
            return self.conn.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert _from(calls, "outer") & {GET, ROCKET_GET} == set()
    assert {d for s, d in calls if s == "py:app.use.Holder.go"} & {GET, ROCKET_GET} == set()
    assert {d for s, d in calls if s == "py:app.use.Pool.use"} & {GET, ROCKET_GET} == set()


def test_imports_are_module_level_and_must_agree(tmp_path: Path) -> None:
    """A function-local import binds a local there; two module-level imports of one name refuse (H3)."""
    other = "class Store:\n    def get(self, k):\n        return k\n"
    src = """\
    from app.store import Store
    try:
        from app.fast import P
    except ImportError:
        from app.slow import P
    def g(s: Store):
        return s.get(1)
    def h():
        from app.other import Store
        return Store
    def f(p: P):
        return p.run()
    """
    files = {
        "app/other.py": other,
        "app/fast.py": "class P:\n    def run(self): ...\n",
        "app/slow.py": "class P:\n    def run(self): ...\n",
        "app/use.py": src,
    }
    calls = _calls(tmp_path, files)
    assert _from(calls, "g") == {GET}
    assert _from(calls, "f") == set()


def test_a_method_signature_sees_its_class_body(tmp_path: Path) -> None:
    """`x: Inner` in a method signature means the nested `Outer.Inner`, not the module's (H8)."""
    src = """\
    class Inner:
        def run(self):
            return 1
    class Outer:
        class Inner:
            def run(self):
                return 2
        field: Inner
        def m(self, x: Inner):
            x.run()
            y = Inner()
            y.run()
            return self.field.run()
    """
    root = tmp_path / "repo"
    for rel, text in {"app/__init__.py": "", "app/store.py": STORE, "app/use.py": src}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(textwrap.dedent(text), encoding="utf-8")
    batch = RepoCodeExtractor(default_extractors()).extract(root)
    lines = sorted(
        e.provenance.line if e.provenance else 0
        for e in batch.edges
        if e.kind is EdgeKind.CALLS and e.src == "py:app.use.Outer.m" and e.dst == "py:app.use.Inner.run"
    )
    # Only `y.run()`: `y = Inner()` runs in the method body, which skips the class scope.
    # `x.run()` and `self.field.run()` are typed by `Inner` as the class body reads it — the
    # nested class, which this pass refuses rather than claim the module's.
    (y_run,) = [n for n, line in enumerate(textwrap.dedent(src).splitlines(), 1) if line.strip() == "y.run()"]
    assert lines == [y_run]


def test_no_edge_from_a_caller_without_a_node(tmp_path: Path) -> None:
    """A def inside a `match` case gets no node from the per-file pass; no edge may start there (H4)."""
    src = """\
    from app.store import Store
    def outer(v):
        match v:
            case 1:
                def inner(s: Store):
                    return s.get(1)
    """
    root = tmp_path / "repo"
    for rel, text in {"app/__init__.py": "", "app/store.py": STORE, "app/use.py": src}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(textwrap.dedent(text), encoding="utf-8")
    batch = RepoCodeExtractor(default_extractors()).extract(root)
    ids = {n.id for n in batch.nodes}
    assert all(e.src in ids and e.dst in ids for e in batch.edges if e.kind is EdgeKind.CALLS)


def test_a_function_local_import_is_a_readable_binding(tmp_path: Path) -> None:
    """Lazy imports inside a function (common in CLI code) type a receiver like a module-level
    one; the same name also rebound in the function refuses."""
    src = """\
    def lazy():
        from app.store import Store
        s = Store()
        return s.get(1)
    def lazy_param(s: "Store"):
        from app.store import Store
        return s.get(1)
    def rebound():
        from app.store import Store
        Store = object
        s = Store()
        return s.get(1)
    """
    calls = _calls(tmp_path, {"app/use.py": src})
    assert GET in _from(calls, "lazy")
    assert GET not in _from(calls, "lazy_param")  # the annotation is read where the def runs
    assert GET not in _from(calls, "rebound")
