"""External docs (pulled over MCP) in ``blast_radius``, ``explain_symbol`` and ``docs_for`` (SSPN-80).

The cache is written directly with :func:`write_pull` — the pull itself is covered in
``tests/mcp/test_doc_pull.py``; these tests are about what the read tools do with a cache.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orchestrator.pkg import external_docs as ext
from orchestrator.pkg.external_docs import ExternalPage, record_failure, source_cache_dir, write_pull
from orchestrator.plugin.server import blast_radius, docs_for, explain_symbol

_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
_DOCS = "docs:\n  app:\n    - {name: wiki, server: atlassian, confluence: {roots: ['1']}}\n"


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path_factory.mktemp("docs-cache")
    monkeypatch.setenv(ext.ENV_CACHE_DIR, str(cache))
    monkeypatch.setattr(ext, "_now", lambda: _T0)
    return cache


def _repo(tmp_path: Path, *, docs: str = _DOCS) -> Path:
    repo = tmp_path / "app"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "lib" / "client.py").write_text(
        "class Client:\n    def ask(self, q):\n        return q\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# Reference\n\n`Client` answers questions.\n", encoding="utf-8")
    (repo / ".spine").mkdir()
    (repo / ".spine" / "repos.yaml").write_text(f"repos:\n  app: ..\n{docs}", encoding="utf-8")
    return repo


def _pull(repo: Path, pages: list[ExternalPage], *, key: str = "app", source: str = "wiki") -> Path:
    dest = source_cache_dir(repo, key, source)
    write_pull(dest, pages, {"pulled_at": ext.utc_stamp()})
    return dest


_GUIDE = ExternalPage(
    id="1",
    title="SDK guide",
    text="# Install\n\nCreate a `Client` first.",
    url="https://wiki/1",
    kind="confluence",
)


def test_an_external_page_naming_a_symbol_is_in_blast_radius_with_its_origin(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _pull(repo, [_GUIDE])
    out = blast_radius(str(repo), "Client")
    m = out["matches"][0]
    assert m["doc_count"] == 2
    local, external = m["docs"]
    assert local["doc"] == "README.md#reference" and local["origin"] == "repo"
    assert external == {
        "doc": "mcp:atlassian/1#install",
        "via": "symbol",
        "origin": "mcp:atlassian",
        "where": "mcp:atlassian/1:1",
        "source": "wiki",
        "title": "SDK guide",
        "url": "https://wiki/1",
    }
    (row,) = out["external_docs"]
    assert (row["repo"], row["source"], row["status"], row["stale"]) == ("app", "wiki", "ok", False)
    assert "**External docs** `wiki` (mcp:atlassian): pulled 2026-09-01T12:00:00Z" in out["markdown"]
    # the code impact is untouched
    assert not any(t["id"].startswith("doc:") for t in m["touches"])


def test_explain_symbol_carries_the_same_external_docs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _pull(repo, [_GUIDE])
    blast = blast_radius(str(repo), "ask")["matches"][0]
    explain = explain_symbol(str(repo), "ask")
    assert explain["matches"][0]["docs"] == blast["docs"]
    assert [d["via"] for d in blast["docs"]] == ["class", "class"]
    assert explain["external_docs"][0]["source"] == "wiki"


def test_a_rename_drops_the_external_mention_without_a_re_pull(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _pull(repo, [_GUIDE])
    assert blast_radius(str(repo), "Client")["matches"][0]["doc_count"] == 2
    (repo / "lib" / "client.py").write_text("class Customer:\n    pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# Reference\n\n`Customer` answers questions.\n", encoding="utf-8")
    m = blast_radius(str(repo), "Customer")["matches"][0]
    assert [d["origin"] for d in m["docs"]] == ["repo"]
    summary = docs_for(str(repo))
    assert summary["external_drift"] == 1
    assert summary["external_drift_top"] == [{"claim": "Client", "doc": "mcp:atlassian/1#install"}]
    assert summary["drift_total"] == 0  # external drift never enters the repository's


def test_an_identical_mirror_is_listed_once_under_the_repo_doc(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    mirror = ExternalPage(
        id="9", title="Reference", text="# Reference\n\n`Client` answers questions.", kind="confluence"
    )
    _pull(repo, [mirror])
    out = blast_radius(str(repo), "Client")
    (only,) = out["matches"][0]["docs"]
    assert only["doc"] == "README.md#reference" and only["origin"] == "repo"
    assert only["also_in"] == ["mcp:atlassian/wiki"]
    assert out["external_docs"][0]["collapsed_into_repo_docs"] == 1
    lookup = docs_for(str(repo), "Client")["matches"][0]
    assert lookup["docs"] == ["README.md#reference"] and lookup["external"] == []
    assert lookup["also_in"] == {"README.md#reference": ["mcp:atlassian/wiki"]}


def test_a_pull_older_than_seven_days_is_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    _pull(repo, [_GUIDE])
    monkeypatch.setattr(ext, "_now", lambda: _T0 + timedelta(days=8))
    out = blast_radius(str(repo), "Client")
    (row,) = out["external_docs"]
    assert row["stale"] is True and row["age_days"] == 8.0
    assert "**stale**" in out["markdown"]


def test_a_failed_pull_shows_the_last_good_data_and_says_when_it_is_from(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dest = _pull(repo, [_GUIDE])
    record_failure(dest, "403 Forbidden")
    out = blast_radius(str(repo), "Client")
    assert out["matches"][0]["doc_count"] == 2
    (row,) = out["external_docs"]
    assert row["status"] == "failed"
    assert row["error"] == "last pull failed: 403 Forbidden; showing data from 2026-09-01T12:00:00Z"


def test_a_declared_source_never_pulled_is_said_so(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = blast_radius(str(repo), "Client")
    assert [d["origin"] for d in out["matches"][0]["docs"]] == ["repo"]
    (row,) = out["external_docs"]
    assert row["status"] == "never_pulled" and row["pulled_at"] is None
    assert "never pulled" in out["markdown"]


def test_no_docs_block_means_no_external_docs_key(tmp_path: Path) -> None:
    repo = _repo(tmp_path, docs="")
    out = blast_radius(str(repo), "Client")
    assert "external_docs" not in out
    assert "external_docs" not in docs_for(str(repo))


def test_docs_for_lists_external_docs_apart_and_summarises_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    jira_like = ExternalPage(id="2", title="Other", text="# Notes\n\nSee `lib.client` and `gone_symbol`.")
    _pull(repo, [_GUIDE, jira_like])
    lookup = docs_for(str(repo), "Client")
    match = lookup["matches"][0]
    assert match["docs"] == ["README.md#reference"]  # the contract: repository docs, by name
    assert [(r["doc"], r["origin"], r["source"]) for r in match["external"]] == [
        ("mcp:atlassian/1#install", "mcp:atlassian", "wiki")
    ]
    assert "external (1)" in lookup["markdown"]
    summary = docs_for(str(repo))
    assert summary["docs"] == 1  # unchanged: the repository's own sections
    assert summary["external_doc_count"] == 2
    assert summary["external_documented_symbols"] == 2  # Client + the lib.client module
    assert summary["external_drift"] == 1 and summary["external_drift_top"][0]["claim"] == "gone_symbol"
    assert "external doc section(s)" in summary["markdown"]
    assert summary["external_docs"][0]["pages"] == 2


def test_each_repository_binds_only_its_own_sources(tmp_path: Path) -> None:
    """D26: both repos define `create_order`; only web declares a source, and only web's match
    lists it — even though billing's symbol has the same name."""
    for name in ("billing", "web"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "orders.py").write_text("def create_order(x):\n    return x\n", encoding="utf-8")
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(
        "repos:\n  billing: billing\n  web: web\n"
        "docs:\n  web:\n    - {name: wiki, server: atlassian, confluence: {roots: ['5']}}\n",
        encoding="utf-8",
    )
    page = ExternalPage(id="5", title="Checkout", text="# Flow\n\n`create_order` posts the form.")
    _pull(tmp_path / "web", [page], key="web")
    out = blast_radius(symbol="create_order", repos=str(cfg))
    by_repo = {
        ("billing" if "billing" in m["id"] else "web"): [d["doc"] for d in m["docs"]] for m in out["matches"]
    }
    assert by_repo == {"billing": [], "web": ["mcp:atlassian/5#flow"]}
    assert [(r["repo"], r["source"]) for r in out["external_docs"]] == [("web", "wiki")]
    per_repo = docs_for(repos=str(cfg))["repos"]
    assert per_repo["web"]["external_doc_count"] == 1
    assert "external_doc_count" not in per_repo["billing"]


def test_understand_and_state_never_see_a_populated_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D8, invariant 2: `analyse()` output is identical with and without pulled docs."""
    from orchestrator.knowledge.current_state import load_current_state, render_current_state

    repo = _repo(tmp_path)
    _pull(repo, [_GUIDE, ExternalPage(id="3", title="x", text="`gone_symbol` and `Client`")])
    with_cache, batch_with = load_current_state(repo)
    monkeypatch.delenv(ext.ENV_CACHE_DIR)
    monkeypatch.setenv(ext.ENV_CACHE_DIR, str(tmp_path / "empty-cache"))
    without, batch_without = load_current_state(repo)
    assert (with_cache.docs, with_cache.doc_drift_total, with_cache.documented_symbols) == (
        without.docs,
        without.doc_drift_total,
        without.documented_symbols,
    )
    assert render_current_state(with_cache) == render_current_state(without)
    assert not any("mcp:" in n.id for n in batch_with.nodes)
    assert sorted(n.id for n in batch_with.nodes) == sorted(n.id for n in batch_without.nodes)
