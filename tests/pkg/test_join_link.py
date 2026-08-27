"""Cross-repository joins — the only edges no single parser could have produced.

Which makes this the least certain code in the package, so the tests are weighted towards the
refusals: what it must *not* join matters more than what it does. A matcher that joins
everything scores wonderfully on recall and poisons every surface downstream.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.pkg.facts import EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.join_link import link_joins
from orchestrator.pkg.python_client import PendingCall
from orchestrator.pkg.repos import Join, RepoConfigError, RepoSet, from_mapping, joins_from_list

WEB = (
    "import httpx\n\n\n"
    "def order():\n    return httpx.post('/v1/orders')\n\n\n"
    "def health():\n    return httpx.get('/health')\n"
)
BILLING = (
    "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
    "@app.post('/v1/orders')\ndef create():\n    return 1\n\n\n"
    "@app.get('/v1/orders/{oid}')\ndef read(oid):\n    return oid\n"
)


def _endpoints(*names: str) -> FactBatch:
    b = FactBatch()
    for name in names:
        b.add_node(
            Node(f"py:billing@endpoint:{name}", NodeKind.ENDPOINT, name, "python", Provenance("r.py", 1))
        )
    return b


def _call(verb: str, path: str) -> PendingCall:
    return PendingCall(verb=verb, path=path, caller_id="py:app.client.fn", provenance=Provenance("c.py", 3))


def _join(base: str = "") -> Join:
    return Join("http", "web", "billing", base)


# ---- what it joins ---------------------------------------------------------


def test_an_exact_path_joins() -> None:
    batch, report = link_joins(
        _endpoints("POST /v1/orders"), [_join()], {"web": [_call("POST", "/v1/orders")]}
    )
    assert report.joined == 1
    edge = next(e for e in batch.edges if e.kind is EdgeKind.CONSUMES)
    assert edge.src == "py:web@app.client.fn"
    assert edge.dst == "py:billing@endpoint:POST /v1/orders"


def test_a_concrete_path_joins_to_a_templated_route() -> None:
    _, report = link_joins(
        _endpoints("GET /v1/orders/{oid}"), [_join()], {"web": [_call("GET", "/v1/orders/42")]}
    )
    assert report.joined == 1


def test_a_declared_base_is_applied_to_the_consumers_path() -> None:
    _, report = link_joins(
        _endpoints("POST /v1/orders"), [_join(base="/v1")], {"web": [_call("POST", "/orders")]}
    )
    assert report.joined == 1


# ---- what it refuses, which is the point -----------------------------------


def test_a_template_never_matches_across_a_slash() -> None:
    """`/v1/orders/{oid}` must not swallow `42/refund` — a different endpoint, likely a
    different handler, and an edge asserting otherwise is fiction."""
    _, report = link_joins(
        _endpoints("GET /v1/orders/{oid}"), [_join()], {"web": [_call("GET", "/v1/orders/42/refund")]}
    )
    assert report.joined == 0
    assert report.unjoined[0].reason == "no-matching-endpoint"


def test_two_possible_endpoints_join_to_neither() -> None:
    """Evidence does not settle it, so neither edge is emitted — recall pays, precision does not."""
    batch = _endpoints("GET /v1/orders/{oid}", "GET /v1/orders/{ref}")
    _, report = link_joins(batch, [_join()], {"web": [_call("GET", "/v1/orders/42")]})
    assert report.joined == 0
    assert report.unjoined[0].reason == "ambiguous"


def test_a_verb_mismatch_does_not_join() -> None:
    _, report = link_joins(_endpoints("POST /v1/orders"), [_join()], {"web": [_call("GET", "/v1/orders")]})
    assert report.joined == 0


def test_a_call_with_no_declared_provider_is_reported_not_guessed() -> None:
    _, report = link_joins(_endpoints("POST /v1/orders"), [], {"web": [_call("POST", "/v1/orders")]})
    assert report.joined == 0
    assert report.unjoined[0].reason == "no-declared-provider"


def test_a_path_nobody_serves_stays_unjoined() -> None:
    _, report = link_joins(_endpoints("POST /v1/orders"), [_join()], {"web": [_call("GET", "/health")]})
    assert report.joined == 0
    assert report.recall == 0.0


# ---- the report ------------------------------------------------------------


def test_a_declared_join_that_places_nothing_is_visible_per_join() -> None:
    """A stale join must not hide inside a healthy-looking total."""
    joins = [_join(), Join("http", "billing", "web", "")]
    _, report = link_joins(_endpoints("POST /v1/orders"), joins, {"web": [_call("POST", "/v1/orders")]})
    per = dict(report.per_join)
    assert per["web -http-> billing"] == 1
    assert per["billing -http-> web"] == 0


def test_recall_is_none_rather_than_zero_when_nothing_was_examined() -> None:
    _, report = link_joins(_endpoints(), [_join()], {})
    assert report.recall is None


# ---- config ----------------------------------------------------------------


def test_an_unknown_join_kind_is_refused_not_ignored() -> None:
    """A silently dropped join produces missing edges, which read as uncoupled services."""
    with pytest.raises(RepoConfigError, match="expected one of"):
        joins_from_list([{"kind": "grpc", "consumer": "a", "provider": "b"}])


def test_a_join_naming_an_undeclared_repo_is_refused(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    with pytest.raises(RepoConfigError, match="undeclared provider"):
        from_mapping(
            {"web": "web"},
            base=tmp_path,
            joins=[{"kind": "http", "consumer": "web", "provider": "billing"}],
        )


def test_a_join_to_itself_is_refused() -> None:
    with pytest.raises(RepoConfigError, match="itself"):
        joins_from_list([{"kind": "http", "consumer": "a", "provider": "a"}])


# ---- end to end, through the real multi-repo path --------------------------


def _repo(root: Path, name: str, body: str) -> None:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / name).write_text(body, encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "i"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True)


def _system(tmp_path: Path) -> RepoSet:
    _repo(tmp_path / "web", "client.py", WEB)
    _repo(tmp_path / "billing", "routes.py", BILLING)
    return from_mapping(
        {"web": "web", "billing": "billing"},
        base=tmp_path,
        joins=[{"kind": "http", "consumer": "web", "provider": "billing"}],
    )


def test_the_join_survives_a_warm_cache(tmp_path: Path) -> None:
    """The side-channel is not in the fact cache, so a warm hit would lose the candidates —
    and the joiner would place nothing, which looks exactly like two uncoupled services."""
    from orchestrator.pkg.persistence import load_or_extract_repos

    repo_set, cache = _system(tmp_path), tmp_path / "cache"
    cold = load_or_extract_repos(repo_set, cache_dir=cache)
    assert cold.joins is not None and cold.joins.joined == 1

    warm = load_or_extract_repos(repo_set, cache_dir=cache)
    assert [r.cached for r in warm.repos] == [True, True], "precondition: the facts came from cache"
    assert warm.joins is not None
    assert warm.joins.joined == 1, "the side-channel did not survive the cache"


def test_the_merged_graph_gains_a_cross_repo_edge(tmp_path: Path) -> None:
    from orchestrator.pkg.persistence import load_or_extract_repos

    merged = load_or_extract_repos(_system(tmp_path), cache_dir=tmp_path / "cache")
    crossing = [
        (e.src, e.dst)
        for e in merged.batch.edges
        if e.kind is EdgeKind.CONSUMES and "web@" in e.src and "billing@" in e.dst
    ]
    assert crossing == [("py:web@app.client.order", "py:billing@endpoint:POST /v1/orders")]


def test_no_joins_declared_means_no_report_not_a_clean_one(tmp_path: Path) -> None:
    """`None` and `0 unplaced` are different answers, and conflating them is the silence this
    whole command exists to prevent."""
    from orchestrator.pkg.persistence import load_or_extract_repos

    _repo(tmp_path / "web", "client.py", WEB)
    _repo(tmp_path / "billing", "routes.py", BILLING)
    repo_set = from_mapping({"web": "web", "billing": "billing"}, base=tmp_path)
    merged = load_or_extract_repos(repo_set, cache_dir=tmp_path / "cache")
    assert merged.joins is None
