"""Cross-repository joins — the only edges no single parser could have produced.

Which makes this the least certain code in the package, so the tests are weighted towards the
refusals: what it must *not* join matters more than what it does. A matcher that joins
everything scores wonderfully on recall and poisons every surface downstream.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
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


# ---- 3b: data --------------------------------------------------------------
#
# Two repositories writing one physical table produce two Entity nodes, so "who writes this
# table" answers per repository and silently under-reports — a schema change looks safe because
# half its writers are in a graph nobody merged.


def _entity(batch: FactBatch, repo: str, table: str) -> None:
    batch.add_node(Node(f"py:{repo}@entity:{table}", NodeKind.ENTITY, table, "python", Provenance("m.py", 1)))
    batch.add_node(
        Node(f"py:{repo}@app.models", NodeKind.MODULE, "app.models", "python", Provenance("m.py", 1))
    )
    batch.add_edge(
        Edge(f"py:{repo}@app.models", f"py:{repo}@entity:{table}", EdgeKind.CONTAINS, Provenance("m.py", 1))
    )


def _data_batch() -> FactBatch:
    b = FactBatch()
    _entity(b, "billing", "invoices")
    _entity(b, "reporting", "invoices")
    _entity(b, "reporting", "ledger")
    b.add_edge(
        Edge(
            "py:reporting@app.models.read",
            "py:reporting@entity:invoices",
            EdgeKind.READS,
            Provenance("m.py", 9),
        )
    )
    b.add_edge(
        Edge(
            "py:reporting@app.models.audit",
            "py:reporting@entity:ledger",
            EdgeKind.READS,
            Provenance("m.py", 9),
        )
    )
    return b


def test_a_shared_table_collapses_onto_the_declared_owner() -> None:
    batch, report = link_joins(_data_batch(), [Join("data", "reporting", "billing")], {})
    ids = {n.id for n in batch.nodes}
    assert "py:reporting@entity:invoices" not in ids, "the duplicate must go, not merely be bypassed"
    assert "py:billing@entity:invoices" in ids
    reads = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.READS}
    assert ("py:reporting@app.models.read", "py:billing@entity:invoices") in reads
    assert report.joined == 1


def test_a_table_the_provider_does_not_have_survives_untouched() -> None:
    """The control. A joiner collapsing every entity would pass the test above and destroy this."""
    batch, _ = link_joins(_data_batch(), [Join("data", "reporting", "billing")], {})
    assert "py:reporting@entity:ledger" in {n.id for n in batch.nodes}
    reads = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.READS}
    assert ("py:reporting@app.models.audit", "py:reporting@entity:ledger") in reads


def test_the_stale_contains_edge_goes_with_the_collapsed_node() -> None:
    """Keeping it would dangle; repointing it would say billing's module contains reporting's
    node. Dropping is the only answer that is both valid and true."""
    batch, _ = link_joins(_data_batch(), [Join("data", "reporting", "billing")], {})
    contains = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS}
    assert ("py:reporting@app.models", "py:billing@entity:invoices") not in contains
    ids = {n.id for n in batch.nodes}
    assert not [e for e in batch.edges if e.dst not in ids], "no dangling edge"


# ---- 3b: package -----------------------------------------------------------


def _package_batch() -> FactBatch:
    b = FactBatch()
    b.add_node(Node("py:billing@app.charge", NodeKind.MODULE, "app.charge", "python", Provenance("c.py", 1)))
    b.add_node(
        Node("py:shared@shared.money", NodeKind.MODULE, "shared.money", "python", Provenance("m.py", 1))
    )
    b.add_node(
        Node(
            "py:shared@shared.money.to_cents", NodeKind.FUNCTION, "to_cents", "python", Provenance("m.py", 2)
        )
    )
    b.add_node(Node("py:shared.money.to_cents", NodeKind.MODULE, "to_cents", "python", external=True))
    b.add_node(Node("py:json", NodeKind.MODULE, "json", "python", external=True))
    b.add_edge(
        Edge("py:billing@app.charge", "py:shared.money.to_cents", EdgeKind.IMPORTS, Provenance("c.py", 3))
    )
    b.add_edge(Edge("py:billing@app.charge", "py:json", EdgeKind.IMPORTS, Provenance("c.py", 1)))
    b.add_edge(
        Edge(
            "py:billing@app.charge.charge", "py:shared.money.to_cents", EdgeKind.CALLS, Provenance("c.py", 7)
        )
    )
    return b


def test_an_import_of_another_declared_repo_is_repointed() -> None:
    batch, report = link_joins(_package_batch(), [Join("package", "billing", "shared")], {})
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("py:billing@app.charge", "py:shared@shared.money.to_cents") in imports
    assert "py:shared.money.to_cents" not in {n.id for n in batch.nodes}
    assert report.joined == 2  # the import and the call


def test_the_call_moves_with_the_import() -> None:
    """Moving only the import would drop a real call edge when the placeholder is removed —
    a join that destroys knowledge rather than adding it."""
    batch, _ = link_joins(_package_batch(), [Join("package", "billing", "shared")], {})
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("py:billing@app.charge.charge", "py:shared@shared.money.to_cents") in calls


def test_a_genuinely_third_party_import_stays_external() -> None:
    """The control. `json` is external and nobody declares it — a joiner repointing every
    external import would pass the test above and destroy this."""
    batch, _ = link_joins(_package_batch(), [Join("package", "billing", "shared")], {})
    imports = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}
    assert ("py:billing@app.charge", "py:json") in imports
    assert "py:json" in {n.id for n in batch.nodes}


def test_base_is_refused_on_a_kind_where_it_means_nothing() -> None:
    with pytest.raises(RepoConfigError, match="applies to http only"):
        joins_from_list([{"kind": "data", "consumer": "a", "provider": "b", "base": "/v1"}])


# ---- the shared-extractor leak (2026-09-02) --------------------------------


QUIET = """def total():
    return 1
"""


def test_a_shared_extractor_does_not_carry_one_repos_calls_into_another(tmp_path: Path) -> None:
    """`pkg extract --repos` and `investigate --repos` both pass one extractor for every repo.

    `unresolved_calls` on the extractor is a copy; the originals live on the front-ends, which
    are built once and reused, and `ClientState.clear()` preserves `unmatched` on purpose. So
    clearing only the extractor's list left the earlier repo's calls on the front-end for the
    next one to inherit — and the joiner scopes a candidate to the repo it is examining, so a
    `CONSUMES` edge is drawn from `py:zeta@app.client.order`, a node that does not exist.

    Repos run in key order, so `api` (which calls) precedes `zeta` (which does not), and only
    `zeta` is declared as a consumer. Without the leak `zeta` has no candidates and the join
    places nothing.

    The corpus could not catch this: `pkg/accuracy.py` builds a fresh extractor per root, so
    the multirepo fixtures score a different code path from the one the CLI runs.
    """
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.persistence import load_or_extract_repos

    _repo(tmp_path / "api", "client.py", WEB)  # makes POST /v1/orders
    _repo(tmp_path / "billing", "routes.py", BILLING)  # serves it
    _repo(tmp_path / "zeta", "svc.py", QUIET)  # makes no HTTP calls at all
    repo_set = from_mapping(
        {"api": "api", "billing": "billing", "zeta": "zeta"},
        base=tmp_path,
        joins=[{"kind": "http", "consumer": "zeta", "provider": "billing"}],
    )

    merged = load_or_extract_repos(repo_set, cache_dir=tmp_path / "cache", extractor=RepoCodeExtractor())
    assert [r.key for r in merged.repos] == ["api", "billing", "zeta"], "precondition: all ran"

    assert merged.joins is not None
    assert merged.joins.joined == 0, "zeta makes no calls; it cannot consume anything"

    # And the invariant the leak broke: every edge endpoint is a node the graph actually has.
    ids = {n.id for n in merged.batch.nodes}
    dangling = [(e.kind.value, e.src, e.dst) for e in merged.batch.edges if e.src not in ids]
    assert not dangling


def test_reset_unresolved_clears_the_front_ends_too(tmp_path: Path) -> None:
    """The narrow unit: clearing only the extractor's own list is not enough."""
    from orchestrator.pkg.extractor import RepoCodeExtractor

    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "client.py").write_text(WEB, encoding="utf-8")
    (tmp_path / "quiet").mkdir()
    (tmp_path / "quiet" / "svc.py").write_text(QUIET, encoding="utf-8")

    ex = RepoCodeExtractor()
    ex.extract(tmp_path / "web")
    assert ex.unresolved_calls, "precondition: web makes a call it does not serve"

    ex.reset_unresolved()
    ex.extract(tmp_path / "quiet")
    assert not ex.unresolved_calls, "quiet makes no HTTP calls at all"
