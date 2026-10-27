"""The per-process graph memo (B110): the MCP server loads a repository's graph once per tree
state, not once per call — and never answers from a tree that has since changed."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import orchestrator.pkg as pkg
import orchestrator.pkg.persistence as persistence
from orchestrator.plugin import graph_memo
from orchestrator.plugin.server import blast_radius, file_insights, regression_gaps

_CART = "def cart_total(prices):\n    return sum(prices)\n"
_CHECKOUT = "from cart import cart_total\n\n\ndef checkout(prices):\n    return cart_total(prices)\n"
_EXTRA_CALLER = "\n\ndef refund(prices):\n    return -cart_total(prices)\n"


def _git(repo: Path, *args: str) -> None:
    # a repo-local identity: CI has no global one
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path, *, git: bool = True) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "cart.py").write_text(_CART, encoding="utf-8")
    (tmp_path / "checkout.py").write_text(_CHECKOUT, encoding="utf-8")
    if git:
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _callers(repo: Path) -> int:
    count: int = blast_radius(str(repo), "py:cart.cart_total")["matches"][0]["caller_count"]
    return count


@pytest.fixture(autouse=True)
def _fresh_memo() -> Any:
    graph_memo.clear()
    yield
    graph_memo.clear()


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Every graph load the server makes, by entry point; the disk cache goes to a temp dir."""
    seen: list[str] = []
    monkeypatch.setattr(persistence, "default_cache_dir", lambda: tmp_path / "pkg-cache")
    real_unbound, real_load = persistence.load_with_unbound, pkg.load_or_extract

    def unbound(*a: Any, **k: Any) -> Any:
        seen.append("load_with_unbound")
        return real_unbound(*a, **k)

    def load(*a: Any, **k: Any) -> Any:
        seen.append("load_or_extract")
        return real_load(*a, **k)

    monkeypatch.setattr(persistence, "load_with_unbound", unbound)
    monkeypatch.setattr(pkg, "load_or_extract", load)
    return seen


def test_a_clean_tree_is_loaded_once_for_every_call(tmp_path: Path, loads: list[str]) -> None:
    repo = _repo(tmp_path / "r")
    first = blast_radius(str(repo), "py:cart.cart_total")
    assert blast_radius(str(repo), "py:cart.cart_total") == first
    file_insights(str(repo), "cart.py")
    regression_gaps(str(repo), "cart_total")
    assert len(loads) == 1


def test_an_edit_is_seen_on_the_next_call_and_loaded_once(tmp_path: Path, loads: list[str]) -> None:
    repo = _repo(tmp_path / "r")
    assert _callers(repo) == 1
    with (repo / "checkout.py").open("a", encoding="utf-8") as f:
        f.write(_EXTRA_CALLER)
    assert _callers(repo) == 2
    assert _callers(repo) == 2
    assert len(loads) == 2  # the clean tree, then the edited one — not once per call
    with (repo / "checkout.py").open("a", encoding="utf-8") as f:
        f.write(_EXTRA_CALLER.replace("refund", "void"))
    assert _callers(repo) == 3
    assert len(loads) == 3


def test_an_untracked_file_is_seen(tmp_path: Path, loads: list[str]) -> None:
    repo = _repo(tmp_path / "r")
    assert _callers(repo) == 1
    (repo / "refunds.py").write_text("from cart import cart_total" + _EXTRA_CALLER, encoding="utf-8")
    assert _callers(repo) == 2
    # a change inside an already-untracked file is a new tree too, not only the first one
    with (repo / "refunds.py").open("a", encoding="utf-8") as f:
        f.write(_EXTRA_CALLER.replace("refund", "void"))
    assert _callers(repo) == 3


def test_reverting_an_edit_answers_for_the_clean_tree_again(tmp_path: Path, loads: list[str]) -> None:
    repo = _repo(tmp_path / "r")
    with (repo / "checkout.py").open("a", encoding="utf-8") as f:
        f.write(_EXTRA_CALLER)
    assert _callers(repo) == 2
    _git(repo, "checkout", "--", "checkout.py")
    assert _callers(repo) == 1


def test_a_new_commit_is_a_new_tree(tmp_path: Path, loads: list[str]) -> None:
    repo = _repo(tmp_path / "r")
    assert _callers(repo) == 1
    with (repo / "checkout.py").open("a", encoding="utf-8") as f:
        f.write(_EXTRA_CALLER)
    _git(repo, "commit", "-q", "-am", "refund")
    assert _callers(repo) == 2
    assert len(loads) == 2


def test_the_kept_graph_is_the_graph_a_fresh_load_gives(tmp_path: Path, loads: list[str]) -> None:
    repo = _repo(tmp_path / "r")
    with (repo / "checkout.py").open("a", encoding="utf-8") as f:
        f.write(_EXTRA_CALLER)
    blast_radius(str(repo), "py:cart.cart_total")
    kept = graph_memo.kept_batch(repo.resolve())
    assert kept is not None
    fresh = persistence.facts_to_dict(persistence.load_or_extract(repo, cache_dir=tmp_path / "other"))
    assert persistence.facts_to_dict(kept) == fresh


def test_doc_linking_never_reaches_the_kept_graph(tmp_path: Path, loads: list[str]) -> None:
    repo = _repo(tmp_path / "r", git=False)
    (repo / "README.md").write_text("# Cart\n\n`cart_total` adds the prices.\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    first = blast_radius(str(repo), "py:cart.cart_total")["matches"][0]
    second = blast_radius(str(repo), "py:cart.cart_total")["matches"][0]
    assert first["doc_count"] == second["doc_count"] == 1
    assert first["touches"] == second["touches"]
    assert not any(t["id"].startswith("doc:") for t in second["touches"])
    kept = graph_memo.kept_batch(repo.resolve())
    assert kept is not None and kept.counts().get("Doc", 0) == 0
    assert len(loads) == 1


def test_a_directory_outside_git_is_never_kept(tmp_path: Path, loads: list[str]) -> None:
    repo = _repo(tmp_path / "r", git=False)
    assert _callers(repo) == 1
    with (repo / "checkout.py").open("a", encoding="utf-8") as f:
        f.write(_EXTRA_CALLER)
    assert _callers(repo) == 2
    assert len(loads) == 2
    assert graph_memo.kept_batch(repo.resolve()) is None


def test_it_keeps_a_bounded_number_of_repositories(
    tmp_path: Path, loads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_memo, "MAX_REPOS", 2)
    repos = [_repo(tmp_path / f"r{i}") for i in range(3)]
    for repo in repos:
        _callers(repo)
    assert graph_memo.kept_batch(repos[0].resolve()) is None
    assert all(graph_memo.kept_batch(r.resolve()) is not None for r in repos[1:])


def test_a_tree_that_changes_during_the_load_is_not_kept(
    tmp_path: Path, loads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "r")
    real = persistence.load_with_unbound

    def edit_while_loading(*a: Any, **k: Any) -> Any:
        out = real(*a, **k)
        with (repo / "checkout.py").open("a", encoding="utf-8") as f:
            f.write(_EXTRA_CALLER)
        return out

    monkeypatch.setattr(persistence, "load_with_unbound", edit_while_loading)
    assert _callers(repo) == 1  # this call answers for the tree it loaded
    assert graph_memo.kept_batch(repo.resolve()) is None
    monkeypatch.setattr(persistence, "load_with_unbound", real)
    assert _callers(repo) == 2  # and the next one sees the edit


def test_a_graph_kept_without_its_untraced_list_is_not_served_to_a_call_that_needs_it(
    tmp_path: Path, loads: list[str]
) -> None:
    repo = _repo(tmp_path / "r")
    (repo / "basket.py").write_text(
        "def total_for(basket, p):\n    return basket.cart_total(p)\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "basket")
    regression_gaps(str(repo), "cart_total")  # keeps the graph, without the untraced list
    match = blast_radius(str(repo), "py:cart.cart_total")["matches"][0]
    assert match["unresolved_calls"] is not None and match["unresolved_calls"]["count"] == 1
    assert loads == ["load_or_extract", "load_with_unbound"]


def test_the_doc_linked_graph_is_kept_too_and_relinked_when_a_doc_changes(
    tmp_path: Path, loads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.pkg.doc_link as doc_link

    links: list[int] = []
    real = doc_link.link_docs

    def counted(*a: Any, **k: Any) -> Any:
        links.append(1)
        return real(*a, **k)

    monkeypatch.setattr(doc_link, "link_docs", counted)
    repo = _repo(tmp_path / "r", git=False)
    (repo / "README.md").write_text("# Cart\n\n`cart_total` adds the prices.\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    assert blast_radius(str(repo), "py:cart.cart_total")["matches"][0]["doc_count"] == 1
    assert blast_radius(str(repo), "py:cart.cart_total")["matches"][0]["doc_count"] == 1
    assert len(links) == 1
    (repo / "GUIDE.md").write_text("# Guide\n\nCall `cart_total` first.\n", encoding="utf-8")
    assert blast_radius(str(repo), "py:cart.cart_total")["matches"][0]["doc_count"] == 2
    assert len(links) == 2


def test_docs_bound_from_external_sources_are_linked_live_every_call(
    tmp_path: Path, loads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.plugin.server as server_module

    linked: list[tuple[Any, ...]] = []

    def link(self: Any, batch: Any, root: Any, key: Any = None, sources: tuple[Any, ...] = ()) -> Any:
        linked.append(sources)
        return pkg.FactStore(pkg.FactBatch())  # a store that could be kept, if the rule allowed it

    monkeypatch.setattr(server_module, "_local_doc_sources", lambda repo: ("web", ("confluence",)))
    monkeypatch.setattr(server_module._Docs, "_link", link)
    repo = _repo(tmp_path / "r")
    blast_radius(str(repo), "py:cart.cart_total")
    blast_radius(str(repo), "py:cart.cart_total")
    assert linked == [("confluence",), ("confluence",)]  # pages outside the tree: never kept
    assert len(loads) == 1  # the code graph still is
