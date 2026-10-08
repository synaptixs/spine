"""B62: the Python calls the receiver pass refuses are recorded — beside the graph, never in it.

``blast_radius`` lists only calls the graph can bind to a receiver's declared type, and until now a
refused call left no trace, so no tool could say "and some calls named ``get`` were not traced". The
receiver pass now records each refused attribute call whose name the repository declares, as
``unbound_member_calls`` on the extractor. These tests pin what is recorded and, as important, what
is not: a call that has an edge, a name nobody declares, and anything in the batch itself.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

from orchestrator.pkg import EdgeKind, FactBatch, RepoCodeExtractor
from orchestrator.pkg.extractor import default_extractors
from orchestrator.pkg.persistence import (
    facts_to_dict,
    load_or_extract,
    load_or_extract_repos,
    load_unbound,
    load_unbound_repos,
    load_with_unbound,
)
from orchestrator.pkg.repos import load_repo_config
from orchestrator.pkg.unbound import MAX_SITES, UnboundCall, UnboundIndex

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


# ---- P3: persisted beside the cache, loaded the same warm or cold -----------------------------

ANY_CALL = "from typing import Any\ndef f(s: Any):\n    return s.get(1)\n"


def _git(root: Path, files: dict[str, str]) -> Path:
    """A real git repo with one commit — the commit-keyed cache only trusts a clean tree."""
    for rel, src in {"app/__init__.py": "", "app/store.py": STORE, **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(src), encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, env=env)
    return root


def _sidecars(cache: Path) -> list[Path]:
    return sorted(cache.glob("*.unbound.json"))


def test_a_cold_load_returns_the_graph_and_the_index_from_one_extraction(tmp_path: Path) -> None:
    repo, cache = _git(tmp_path / "repo", {"app/use.py": ANY_CALL}), tmp_path / "cache"
    extractor = RepoCodeExtractor(default_extractors())
    batch, index = load_with_unbound(repo, cache_dir=cache, extractor=extractor)
    assert extractor.extractions == 1
    entry = index.entry("get")
    assert entry is not None and entry.count == 1
    assert [(c.caller, c.receiver) for c in entry.sites] == [("py:app.use.f", "s")]
    assert facts_to_dict(batch) == facts_to_dict(load_or_extract(repo, cache_dir=cache))
    assert len(_sidecars(cache)) == 1


def test_a_warm_load_gives_the_same_answer_without_extracting(tmp_path: Path) -> None:
    repo, cache = _git(tmp_path / "repo", {"app/use.py": ANY_CALL}), tmp_path / "cache"
    _, cold = load_with_unbound(repo, cache_dir=cache)
    extractor = RepoCodeExtractor(default_extractors())
    batch, warm = load_with_unbound(repo, cache_dir=cache, extractor=extractor)
    assert extractor.extractions == 0  # both the graph and the list came from the cache
    assert warm == cold
    assert warm.entry("get") is not None  # not an empty list that merely looks like "none found"
    assert batch.nodes  # and the graph is the real one


def test_a_missing_sidecar_is_rebuilt_not_read_as_none_found(tmp_path: Path) -> None:
    repo, cache = _git(tmp_path / "repo", {"app/use.py": ANY_CALL}), tmp_path / "cache"
    _, cold = load_with_unbound(repo, cache_dir=cache)
    for sidecar in _sidecars(cache):
        sidecar.unlink()  # the graph cache survives; the list does not
    extractor = RepoCodeExtractor(default_extractors())
    _, rebuilt = load_with_unbound(repo, cache_dir=cache, extractor=extractor)
    assert extractor.extractions == 1
    assert rebuilt == cold and rebuilt.entry("get") is not None
    assert len(_sidecars(cache)) == 1  # written back, so it happens once


def test_a_corrupt_sidecar_is_rebuilt(tmp_path: Path) -> None:
    repo, cache = _git(tmp_path / "repo", {"app/use.py": ANY_CALL}), tmp_path / "cache"
    _, cold = load_with_unbound(repo, cache_dir=cache)
    for sidecar in _sidecars(cache):
        sidecar.write_text("{not json", encoding="utf-8")
    assert load_unbound(repo, cache_dir=cache) == cold


def test_a_repository_with_nothing_untraced_is_tracked_and_empty(tmp_path: Path) -> None:
    src = "from app.store import Store\ndef f(s: Store):\n    return s.get(1)\n"
    index = load_unbound(_git(tmp_path / "repo", {"app/use.py": src}), cache_dir=tmp_path / "cache")
    assert index.total() == 0 and index.entry("get") is None  # zero, which is an answer


def test_a_dirty_tree_is_extracted_every_time_and_writes_no_sidecar(tmp_path: Path) -> None:
    repo, cache = _git(tmp_path / "repo", {"app/use.py": ANY_CALL}), tmp_path / "cache"
    (repo / "app" / "use.py").write_text(ANY_CALL + "\n# edited\n", encoding="utf-8")
    extractor = RepoCodeExtractor(default_extractors())
    index = load_unbound(repo, cache_dir=cache, extractor=extractor)
    assert extractor.extractions == 1 and index.entry("get") is not None
    assert _sidecars(cache) == []


def test_the_index_keeps_exact_counts_and_a_bounded_deterministic_list() -> None:
    calls = [UnboundCall(f"py:m.f{i:03d}", "get", "m.py", i + 1, "d") for i in range(MAX_SITES + 25)]
    calls.append(UnboundCall("py:m.g", "other", "m.py", 1, "x"))
    index = UnboundIndex.from_calls(reversed(calls))  # input order must not matter
    entry = index.entry("get")
    assert entry is not None and entry.count == MAX_SITES + 25
    assert [c.caller for c in entry.sites] == [f"py:m.f{i:03d}" for i in range(MAX_SITES)]
    assert index.total() == MAX_SITES + 26
    assert UnboundIndex.from_dict(index.to_dict()) == index


def test_the_index_rejects_a_payload_it_does_not_understand() -> None:
    assert UnboundIndex.from_dict({"version": 99, "members": {}}) is None
    assert UnboundIndex.from_dict({"version": 1, "members": {"get": {"count": "x"}}}) is None


def test_a_merged_graph_gets_caller_ids_that_match_its_node_ids(tmp_path: Path) -> None:
    billing = _git(tmp_path / "billing", {"app/use.py": ANY_CALL})
    web = _git(tmp_path / "web", {"app/use.py": ANY_CALL.replace("def f", "def g")})
    config = tmp_path / "repos.yaml"
    config.write_text(f"repos:\n  billing: {billing}\n  web: {web}\n", encoding="utf-8")
    repo_set, cache = load_repo_config(str(config)), tmp_path / "cache"
    index = load_unbound_repos(repo_set, cache_dir=cache)
    merged_ids = {n.id for n in load_or_extract_repos(repo_set, cache_dir=cache).batch.nodes}
    entry = index.entry("get")
    assert entry is not None and entry.count == 2
    callers = sorted(c.caller for c in entry.sites)
    assert callers == ["py:billing@app.use.f", "py:web@app.use.g"]
    assert set(callers) <= merged_ids  # the ids the merged store knows, or the lookup finds nothing
    # a second load (warm) gives the same answer
    assert load_unbound_repos(repo_set, cache_dir=cache) == index
