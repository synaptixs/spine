"""RAG chunks (pulled over MCP) in ``blast_radius``, ``explain_symbol`` and ``docs_for`` (SSPN-82).

The cache is written directly with :func:`write_pull`, as a ``rag`` pull leaves it — the pull is
covered in ``tests/mcp/test_rag_pull.py``; these tests are about binding at read time: admission by
the unique-anchor rule (D9), the per-symbol retrieval count, repo-path collapse (Q42), drift only
for a walk, isolation between repositories (D26) and ``understand``/``state`` never reading it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import orchestrator.pkg.external_docs as ext
from orchestrator.pkg.external_docs import ExternalPage, source_cache_dir, write_pull
from orchestrator.plugin.server import blast_radius, docs_for, explain_symbol

_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
_DOCS = "docs:\n  app:\n    - {name: kb, server: chroma, rag: {collection: docs}}\n"


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path_factory.mktemp("docs-cache")
    monkeypatch.setenv(ext.ENV_CACHE_DIR, str(cache))
    monkeypatch.setattr(ext, "_now", lambda: _T0)
    return cache


def _repo(tmp_path: Path, *, docs: str = _DOCS, name: str = "app") -> Path:
    repo = tmp_path / name
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "lib" / "client.py").write_text(
        "class Client:\n    def ask(self, q):\n        return q\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# Reference\n\n`Client` answers questions.\n", encoding="utf-8")
    (repo / ".spine").mkdir()
    (repo / ".spine" / "repos.yaml").write_text(f"repos:\n  {name}: ..\n{docs}", encoding="utf-8")
    return repo


def _chunk(cid: str, text: str, source: str = "", title: str = "") -> ExternalPage:
    url = source if source.startswith("https://") else ""  # as `Chunk.page()` sets it
    return ExternalPage(id=cid, title=title or cid, text=text, url=url, kind="chunk", source=source)


_NAMES = _chunk("c1", "Create a `Client` before anything else.", "https://kb/guide", "Guide")
_VAGUE = _chunk("c2", "Clients should retry on timeouts.")
_STALE = _chunk("c3", "Call `gone_symbol` to reset the client.")


def _pull(
    repo: Path,
    pages: list[ExternalPage],
    *,
    queries: dict[str, list[str]] | None = None,
    strategy: str = "query",
    key: str = "app",
    source: str = "kb",
) -> Path:
    dest = source_cache_dir(repo, key, source)
    counts = {"bound": "queried 1 of 1 symbols → 3 chunk(s)" if strategy == "query" else "3 of 3"}
    manifest = {"pulled_at": ext.utc_stamp(), "strategy": strategy, "counts": counts}
    write_pull(dest, pages, manifest, queries=queries if strategy == "query" else None)
    return dest


def _query_pull(repo: Path) -> Path:
    return _pull(repo, [_NAMES, _VAGUE, _STALE], queries={"Client": ["c1", "c2", "c3"], "client": []})


def test_a_chunk_naming_the_symbol_is_listed_and_the_rest_are_counted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _query_pull(repo)
    out = blast_radius(str(repo), "Client")
    m = out["matches"][0]
    external = [d for d in m["docs"] if d["origin"] != "repo"]
    assert external == [
        {
            "doc": "mcp:chroma/c1",
            "via": "symbol",
            "origin": "mcp:chroma",
            "where": "mcp:chroma/c1:1",
            "source": "kb",
            "title": "Guide",
            "url": "https://kb/guide",
            "source_path": "https://kb/guide",
        }
    ]
    assert (m["external_retrieved_count"], m["external_unverified_count"]) == (3, 2)
    assert "3 retrieved, 1 name the symbol" in out["markdown"]
    (row,) = out["external_docs"]
    assert (row["strategy"], row["pull_bound"], row["pages"]) == (
        "query",
        "queried 1 of 1 symbols → 3 chunk(s)",
        3,
    )
    assert "query: queried 1 of 1 symbols" in out["markdown"]


def test_explain_symbol_and_docs_for_carry_the_same_counts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _query_pull(repo)
    blast = blast_radius(str(repo), "Client")["matches"][0]
    explain = explain_symbol(str(repo), "Client")["matches"][0]
    assert explain["docs"] == blast["docs"]
    assert (explain["external_retrieved_count"], explain["external_unverified_count"]) == (3, 2)
    lookup = docs_for(str(repo), "Client")
    match = lookup["matches"][0]
    assert [r["doc"] for r in match["external"]] == ["mcp:chroma/c1"]
    assert (match["external_retrieved_count"], match["external_unverified_count"]) == (3, 2)
    assert "3 retrieved, 1 name the symbol" in lookup["markdown"]


def test_a_symbol_no_query_asked_about_reports_no_count(tmp_path: Path) -> None:
    """A method is never queried (D16): "0 retrieved" would claim a question never put. The module
    `lib.client` was asked as `client` and got nothing — that *is* an answer."""
    repo = _repo(tmp_path)
    _query_pull(repo)
    method = blast_radius(str(repo), "ask")["matches"][0]
    assert "external_retrieved_count" not in method
    assert [d["doc"] for d in method["docs"] if d["origin"] != "repo"] == ["mcp:chroma/c1"]  # via its class
    module = next(m for m in blast_radius(str(repo), "lib.client")["matches"] if m["kind"] == "Module")
    assert (module["external_retrieved_count"], module["external_unverified_count"]) == (0, 0)


def test_a_query_pull_reports_no_drift_a_walk_does(tmp_path: Path) -> None:
    """Drift for rag only on the enumerate path: a chunk retrieved *for* a current name can't
    say what the source holds about names that are gone."""
    repo = _repo(tmp_path)
    _query_pull(repo)
    assert docs_for(str(repo))["external_drift"] == 0
    _pull(repo, [_NAMES, _VAGUE, _STALE], strategy="enumerate")
    summary = docs_for(str(repo))
    assert summary["external_drift"] == 1
    assert summary["external_drift_top"] == [{"claim": "gone_symbol", "doc": "mcp:chroma/c3"}]
    walked = blast_radius(str(repo), "Client")["matches"][0]
    assert "external_retrieved_count" not in walked  # a walk asked nothing per symbol


@pytest.mark.parametrize("path", ["README.md", "./README.md", "{root}/README.md", "file://{root}/README.md"])
def test_a_chunk_from_a_repo_doc_file_collapses_into_it(tmp_path: Path, path: str) -> None:
    repo = _repo(tmp_path)
    indexed = _chunk(
        "r1", "`Client` answers questions, as the README says.", path.format(root=repo.resolve())
    )
    _pull(repo, [indexed, _VAGUE], queries={"Client": ["r1", "c2"]})
    out = blast_radius(str(repo), "Client")
    m = out["matches"][0]
    (only,) = m["docs"]
    assert only["doc"] == "README.md#reference" and only["also_in"] == ["mcp:chroma/kb"]
    # still retrieved, and it still names the symbol — collapsed, not forgotten
    assert (m["external_retrieved_count"], m["external_unverified_count"]) == (2, 1)
    assert out["external_docs"][0]["collapsed_into_repo_docs"] == 1


def test_only_an_exact_repo_path_collapses(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    elsewhere = _chunk("r2", "Build a `Client` like this.", "docs/README.md")
    _pull(repo, [elsewhere], queries={"Client": ["r2"]})
    docs = blast_radius(str(repo), "Client")["matches"][0]["docs"]
    assert [d["doc"] for d in docs] == ["README.md#reference", "mcp:chroma/r2"]
    assert "also_in" not in docs[0]


def test_each_repository_binds_only_its_own_rag_source(tmp_path: Path) -> None:
    """D26: both repos define `Client`; only web declares a rag source and only web's match
    lists its chunk or carries a retrieval count."""
    for name in ("billing", "web"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "orders.py").write_text("class Client:\n    pass\n", encoding="utf-8")
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(
        "repos:\n  billing: billing\n  web: web\ndocs:\n  web:\n    - {name: kb, server: chroma, rag: {}}\n",
        encoding="utf-8",
    )
    _pull(tmp_path / "web", [_NAMES, _VAGUE], queries={"Client": ["c1", "c2"]}, key="web")
    out = blast_radius(symbol="Client", repos=str(cfg))
    by_repo = {("billing" if "billing" in m["id"] else "web"): m for m in out["matches"]}
    assert [d["doc"] for d in by_repo["web"]["docs"]] == ["mcp:chroma/c1"]
    assert by_repo["billing"]["docs"] == [] and "external_retrieved_count" not in by_repo["billing"]
    assert (by_repo["web"]["external_retrieved_count"], by_repo["web"]["external_unverified_count"]) == (2, 1)
    assert [(r["repo"], r["source"]) for r in out["external_docs"]] == [("web", "kb")]


def test_understand_and_state_never_see_a_populated_rag_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant 2: `load_current_state` is identical with and without pulled chunks."""
    from orchestrator.knowledge.current_state import load_current_state, render_current_state

    repo = _repo(tmp_path)
    _query_pull(repo)
    _pull(repo, [_NAMES, _STALE], strategy="enumerate", source="walk")
    with_cache, batch_with = load_current_state(repo)
    monkeypatch.setenv(ext.ENV_CACHE_DIR, str(tmp_path / "empty-cache"))
    without, batch_without = load_current_state(repo)
    assert render_current_state(with_cache) == render_current_state(without)
    assert (with_cache.docs, with_cache.doc_drift_total) == (without.docs, without.doc_drift_total)
    assert sorted(n.id for n in batch_with.nodes) == sorted(n.id for n in batch_without.nodes)
    assert not any("mcp:" in n.id for n in batch_with.nodes)
