"""Loading and merging several repositories into one graph.

The exit criteria for E2 Phase 2, as tests: merge without collision, no dangling edges, reuse
the cache of a repository that did not change, deterministic regardless of declaration order,
and a dirty repository visible rather than absorbed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.pkg.persistence import load_or_extract_repos
from orchestrator.pkg.repos import RepoSet, from_mapping
from orchestrator.pkg.scoping import unscope_id

SOURCE = "class Cart:\n    def add(self):\n        return helper()\n\n\ndef helper():\n    return 1\n"


def _git_repo(root: Path, body: str = SOURCE) -> Path:
    """A real git repo with one commit — the cache only trusts a clean tree at a known SHA."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "shop.py").write_text(body, encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, env={**__import__("os").environ, **env})
    return root


def _two_repos(tmp_path: Path) -> RepoSet:
    _git_repo(tmp_path / "svc-a")
    _git_repo(tmp_path / "svc-b")
    return from_mapping({"svc-a": "svc-a", "svc-b": "svc-b"}, base=tmp_path)


# ---- exit criterion 1: merge without collision -----------------------------


def test_two_repos_defining_the_same_symbols_stay_two_nodes(tmp_path: Path) -> None:
    """Unscoped, `merge` is `add_node` in a loop and these collapse into one set, silently."""
    merged = load_or_extract_repos(_two_repos(tmp_path), cache_dir=tmp_path / "cache")
    ids = {n.id for n in merged.batch.nodes}
    assert "py:svc-a@shop.Cart" in ids
    assert "py:svc-b@shop.Cart" in ids
    scopes = {unscope_id(n.id)[0] for n in merged.batch.nodes if not n.external}
    assert scopes == {"svc-a", "svc-b"}


# ---- exit criterion 2: nothing dangles -------------------------------------


def test_the_merged_graph_has_no_dangling_edges(tmp_path: Path) -> None:
    merged = load_or_extract_repos(_two_repos(tmp_path), cache_dir=tmp_path / "cache")
    ids = {n.id for n in merged.batch.nodes}
    dangling = [(e.src, e.dst) for e in merged.batch.edges if e.src not in ids or e.dst not in ids]
    assert dangling == []


def test_an_intra_repo_call_stays_inside_its_own_repo(tmp_path: Path) -> None:
    """`Cart.add` calls `helper` in *its* repo, not the identically-named one next door."""
    merged = load_or_extract_repos(_two_repos(tmp_path), cache_dir=tmp_path / "cache")
    calls = {(e.src, e.dst) for e in merged.batch.edges if e.kind.value == "CALLS"}
    assert ("py:svc-a@shop.Cart.add", "py:svc-a@shop.helper") in calls
    assert ("py:svc-a@shop.Cart.add", "py:svc-b@shop.helper") not in calls


# ---- exit criterion 3: the unchanged repo is reused ------------------------


def test_a_repo_that_did_not_change_is_served_from_cache(tmp_path: Path) -> None:
    """Per-repo caches, merged on read: change one of two and only one re-extracts."""
    repo_set = _two_repos(tmp_path)
    cache = tmp_path / "cache"
    first = load_or_extract_repos(repo_set, cache_dir=cache)
    assert [r.cached for r in first.repos] == [False, False], "cold — nothing cached yet"

    second = load_or_extract_repos(repo_set, cache_dir=cache)
    assert [r.cached for r in second.repos] == [True, True], "warm — both reused"

    (tmp_path / "svc-b" / "shop.py").write_text(SOURCE + "\n\ndef added():\n    return 2\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path / "svc-b", check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "two"],
        cwd=tmp_path / "svc-b",
        check=True,
    )
    third = load_or_extract_repos(repo_set, cache_dir=cache)
    by_key = {r.key: r for r in third.repos}
    assert by_key["svc-a"].cached is True, "unchanged repo must be reused"
    assert by_key["svc-b"].cached is False, "changed repo must re-extract"


# ---- exit criterion 4: determinism -----------------------------------------


def test_the_merged_graph_does_not_depend_on_declaration_order(tmp_path: Path) -> None:
    _git_repo(tmp_path / "svc-a")
    _git_repo(tmp_path / "svc-b")
    cache = tmp_path / "cache"
    forward = load_or_extract_repos(
        from_mapping({"svc-a": "svc-a", "svc-b": "svc-b"}, base=tmp_path), cache_dir=cache
    )
    reverse = load_or_extract_repos(
        from_mapping({"svc-b": "svc-b", "svc-a": "svc-a"}, base=tmp_path), cache_dir=cache
    )
    assert [n.id for n in forward.batch.nodes] == [n.id for n in reverse.batch.nodes]
    assert [(e.src, e.dst) for e in forward.batch.edges] == [(e.src, e.dst) for e in reverse.batch.edges]
    assert forward.cache_key() == reverse.cache_key()


# ---- exit criterion 5: a dirty repo is visible -----------------------------


def test_one_dirty_repo_makes_the_whole_merged_graph_untrusted(tmp_path: Path) -> None:
    """Not a per-repo verdict: the graph is one artifact, and a join into the dirty one is wrong
    wherever it lands. A merged graph that looks identical either way is how '0 findings' comes
    to mean 'nothing ran'."""
    repo_set = _two_repos(tmp_path)
    clean = load_or_extract_repos(repo_set, cache_dir=tmp_path / "cache")
    assert clean.trusted is True
    assert clean.untrusted_keys == ()
    assert clean.cache_key() == (("svc-a", clean.repos[0].sha or ""), ("svc-b", clean.repos[1].sha or ""))

    (tmp_path / "svc-b" / "shop.py").write_text(SOURCE + "\n# uncommitted\n", encoding="utf-8")
    dirty = load_or_extract_repos(repo_set, cache_dir=tmp_path / "cache")
    assert dirty.trusted is False
    assert dirty.untrusted_keys == ("svc-b",)
    assert dirty.cache_key() == (), "an untrusted graph must not name itself"


def test_a_non_git_directory_is_untrusted_not_merely_uncached(tmp_path: Path) -> None:
    _git_repo(tmp_path / "svc-a")
    (tmp_path / "loose").mkdir()
    (tmp_path / "loose" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    merged = load_or_extract_repos(
        from_mapping({"svc-a": "svc-a", "loose": "loose"}, base=tmp_path), cache_dir=tmp_path / "cache"
    )
    assert merged.trusted is False
    assert merged.untrusted_keys == ("loose",)


# ---- the CLI surface -------------------------------------------------------
#
# Deliberately minimal: it commits to no UX, but a capability nobody can invoke is inert, which
# is the failure pattern GraphIR Phase 4 was reverted for.


def test_the_cli_reports_per_repo_state_and_shouts_when_untrusted(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from orchestrator.cli import app

    _git_repo(tmp_path / "svc-a")
    _git_repo(tmp_path / "svc-b")
    cfg = tmp_path / ".spine" / "repos.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("repos:\n  svc-a: ../svc-a\n  svc-b: ../svc-b\n", encoding="utf-8")

    clean = CliRunner().invoke(app, ["pkg", "extract", "--repos", str(cfg)])
    assert clean.exit_code == 0, clean.output
    assert "Scanned 2 repos" in clean.output
    assert "NOT REPRODUCIBLE" not in clean.output

    (tmp_path / "svc-b" / "shop.py").write_text(SOURCE + "\n# uncommitted\n", encoding="utf-8")
    dirty = CliRunner().invoke(app, ["pkg", "extract", "--repos", str(cfg)])
    assert dirty.exit_code == 0
    assert "UNTRUSTED" in dirty.output
    assert "NOT REPRODUCIBLE" in dirty.output
    assert "svc-b" in dirty.output


def test_a_bad_config_exits_non_zero_with_the_reason(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from orchestrator.cli import app

    cfg = tmp_path / "repos.yaml"
    cfg.write_text("repos:\n  svc: ./nowhere\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["pkg", "extract", "--repos", str(cfg)])
    assert result.exit_code == 1
    assert "not a directory" in result.output
