"""B62: the Python calls the receiver pass refuses are recorded — beside the graph, never in it.

``blast_radius`` lists only calls the graph can bind to a receiver's declared type, and until now a
refused call left no trace, so no tool could say "and some calls named ``get`` were not traced". The
receiver pass now records each refused attribute call whose name the repository declares, as
``unbound_member_calls`` on the extractor. These tests pin what is recorded and, as important, what
is not: a call that has an edge, a name nobody declares, and anything in the batch itself.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from orchestrator.pkg import EdgeKind, FactBatch, RepoCodeExtractor
from orchestrator.pkg.extractor import default_extractors
from orchestrator.pkg.unbound import UnboundCall

STORE = """\
class Base:
    def shared(self):
        return 1


class Store(Base):
    def get(self, key):
        return key


class Rocket:
    def get(self, key):
        return key
"""

UTIL = """\
def helper():
    return 1
"""


def _extract(
    root: Path, files: dict[str, str], extractor: RepoCodeExtractor | None = None
) -> tuple[RepoCodeExtractor, FactBatch]:
    for rel, src in {"app/__init__.py": "", "app/store.py": STORE, "app/util.py": UTIL, **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(src), encoding="utf-8")
    extractor = extractor or RepoCodeExtractor(default_extractors())
    batch = extractor.extract(root)
    return extractor, batch


def _unbound(tmp_path: Path, files: dict[str, str]) -> list[UnboundCall]:
    extractor, _ = _extract(tmp_path / "repo", files)
    return list(extractor.unbound_member_calls)


def _one(calls: list[UnboundCall], caller: str) -> list[UnboundCall]:
    return [c for c in calls if c.caller == f"py:app.use.{caller}"]


def test_an_any_parameter_is_recorded(tmp_path: Path) -> None:
    src = "from typing import Any\ndef f(s: Any):\n    return s.get(1)\n"
    [call] = _one(_unbound(tmp_path, {"app/use.py": src}), "f")
    assert (call.member, call.receiver, call.rel, call.line) == ("get", "s", "app/use.py", 3)


def test_a_name_bound_twice_is_recorded(tmp_path: Path) -> None:
    src = """\
    from app.store import Store
    def make():
        return Store(), 1
    def f(flag):
        if flag:
            s, _m = make()
        else:
            s = Store()
        return s.get(1)
    """
    assert [c.member for c in _one(_unbound(tmp_path, {"app/use.py": src}), "f")] == ["get"]


def test_a_closure_over_an_enclosing_local_is_recorded(tmp_path: Path) -> None:
    src = """\
    from app.store import Store
    def outer():
        s = Store()
        def inner():
            return s.get(1)
        return inner
    """
    calls = _unbound(tmp_path, {"app/use.py": src})
    assert [c.caller for c in calls] == ["py:app.use.outer.inner"]


def test_a_chain_and_a_call_result_are_recorded_with_their_receiver_text(tmp_path: Path) -> None:
    src = """\
    from app.store import Store
    def make():
        return Store()
    def chained(a: Store):
        return a.helper.get(1)
    def from_result():
        s = make()
        return s.get(2)
    """
    calls = _unbound(tmp_path, {"app/use.py": src})
    assert [(c.caller.rsplit(".", 1)[-1], c.receiver) for c in calls] == [
        ("chained", "a.helper"),
        ("from_result", "s"),
    ]


def test_self_in_a_method_that_is_not_an_instance_method_is_recorded(tmp_path: Path) -> None:
    src = """\
    class C:
        @staticmethod
        def f(self):
            return self.get(1)
    """
    [call] = _unbound(tmp_path, {"app/use.py": src})
    assert (call.caller, call.member, call.receiver) == ("py:app.use.C.f", "get", "self")


def test_a_typed_receiver_whose_class_lacks_the_member_is_recorded(tmp_path: Path) -> None:
    # `shared` is declared on Base; `Rocket` does not inherit it, so the lookup fails at resolve time.
    src = "from app.store import Rocket\ndef f(r: Rocket):\n    return r.shared()\n"
    assert [c.member for c in _one(_unbound(tmp_path, {"app/use.py": src}), "f")] == ["shared"]


def test_a_call_that_resolves_is_not_recorded(tmp_path: Path) -> None:
    src = "from app.store import Store\ndef f(s: Store):\n    return s.get(1)\n"
    extractor, batch = _extract(tmp_path / "repo", {"app/use.py": src})
    assert extractor.unbound_member_calls == []
    assert any(e.kind is EdgeKind.CALLS and e.dst == "py:app.store.Store.get" for e in batch.edges)


def test_a_module_attribute_call_the_per_file_pass_resolves_is_not_recorded(tmp_path: Path) -> None:
    # `util.helper()` looks refused to the receiver pass (`util` is not a local) but has an edge.
    src = "import app.util as util\ndef f():\n    return util.helper()\n"
    extractor, batch = _extract(tmp_path / "repo", {"app/use.py": src})
    assert _one(extractor.unbound_member_calls, "f") == []
    assert any(e.kind is EdgeKind.CALLS and e.dst.endswith("util.helper") for e in batch.edges)


def test_a_name_the_repository_does_not_declare_is_not_recorded(tmp_path: Path) -> None:
    src = "from typing import Any\ndef f(s: Any):\n    return s.append(1), s.join(2)\n"
    assert _unbound(tmp_path, {"app/use.py": src}) == []


def test_a_caller_the_graph_has_no_node_for_is_not_recorded(tmp_path: Path) -> None:
    # A def inside a `match` case gets no node, so a record naming it as the caller would dangle.
    src = """\
    from typing import Any
    def f(x):
        match x:
            case 1:
                def inner(s: Any):
                    return s.get(1)
    """
    assert _unbound(tmp_path, {"app/use.py": src}) == []


def test_the_records_are_sorted_and_the_same_every_time(tmp_path: Path) -> None:
    src = """\
    from typing import Any
    def b(s: Any):
        return s.get(1)
    def a(s: Any):
        return s.shared()
    """
    first = _unbound(tmp_path / "one", {"app/use.py": src})
    second = _unbound(tmp_path / "two", {"app/use.py": src})
    assert first == second
    assert [(c.member, c.line) for c in first] == sorted((c.member, c.line) for c in first)


def test_recording_does_not_change_the_batch(tmp_path: Path) -> None:
    # Nothing a record says may appear as a node or an edge: only the typed call has a CALLS edge here.
    src = """\
    from typing import Any
    from app.store import Store
    def typed(s: Store):
        return s.get(1)
    def untyped(s: Any):
        return s.get(1)
    """
    extractor, batch = _extract(tmp_path / "repo", {"app/use.py": src})
    calls_from_use = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS and "use." in e.src}
    assert calls_from_use == {("py:app.use.typed", "py:app.store.Store.get")}
    assert [c.caller for c in extractor.unbound_member_calls] == ["py:app.use.untyped"]


def test_a_second_repository_does_not_inherit_the_first_ones_records(tmp_path: Path) -> None:
    any_call = "from typing import Any\ndef f(s: Any):\n    return s.get(1)\n"
    extractor, _ = _extract(tmp_path / "first", {"app/use.py": any_call})
    assert len(extractor.unbound_member_calls) == 1
    # Reused without a reset, and with nothing to record: the list describes the latest extract.
    _extract(tmp_path / "second", {"app/use.py": "def f(s):\n    return 1\n"}, extractor)
    assert extractor.unbound_member_calls == []
    # An explicit reset clears it too.
    extractor, _ = _extract(tmp_path / "third", {"app/use.py": any_call}, extractor)
    assert len(extractor.unbound_member_calls) == 1
    extractor.reset_unresolved()
    assert extractor.unbound_member_calls == []
