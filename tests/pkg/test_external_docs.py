"""External docs: the cache, the page → DocPage bridge, read-time binding, de-dup (SSPN-80)."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import orchestrator.pkg.external_docs as ext
from orchestrator.pkg import load_or_extract
from orchestrator.pkg.doc_source import html_to_text
from orchestrator.pkg.external_docs import (
    ExternalPage,
    bind_external,
    collapse_stats,
    doc_pages,
    failure_path,
    page_text,
    read_source,
    record_failure,
    source_cache_dir,
    standing,
    write_pull,
)
from orchestrator.pkg.facts import EdgeKind
from orchestrator.pkg.repos import DocSource

_SRC = Path(__file__).resolve().parents[2] / "src" / "orchestrator"
_WIKI = DocSource("wiki", "atlassian", "confluence", roots=("1",))
_JIRA = DocSource("tickets", "atlassian", "jira", jql="project = X")
_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _page(pid: str, text: str, *, kind: str = "confluence", title: str = "") -> ExternalPage:
    return ExternalPage(id=pid, title=title or f"Page {pid}", text=text, url=f"https://wiki/{pid}", kind=kind)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "lib" / "client.py").write_text(
        "class Client:\n    def ask(self, q):\n        return q\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# Reference\n\n`Client` answers questions.\n", encoding="utf-8")
    return repo


# ---- the cache ------------------------------------------------------------------------------


def test_a_pull_writes_pages_sorted_by_id_and_a_manifest(tmp_path: Path) -> None:
    dest = tmp_path / "cache" / "wiki"
    written = write_pull(dest, [_page("2", "b"), _page("1", "a")], {"pulled_at": "2026-09-01T12:00:00Z"})
    lines = (dest / "pages.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["1", "2"]
    assert set(json.loads(lines[0])) == {"id", "title", "url", "text", "kind"}
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == written
    assert manifest["status"] == "ok" and len(manifest["content_sha256"]) == 64


def test_a_crash_mid_write_leaves_the_last_good_pull_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "cache" / "wiki"
    write_pull(dest, [_page("1", "good")], {"pulled_at": "2026-09-01T12:00:00Z"})
    before = (dest / "pages.jsonl").read_bytes()

    def crash(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="disk full"):
        write_pull(dest, [_page("1", "half"), _page("2", "written")], {"pulled_at": "2026-09-02T12:00:00Z"})
    monkeypatch.undo()
    assert (dest / "pages.jsonl").read_bytes() == before
    # no temporary folder is left beside it
    assert sorted(p.name for p in dest.parent.iterdir()) == ["wiki"]


def test_a_crash_between_the_two_renames_still_reads_the_old_pull(tmp_path: Path) -> None:
    dest = tmp_path / "cache" / "wiki"
    write_pull(dest, [_page("1", "good")], {"pulled_at": "2026-09-01T12:00:00Z"})
    os.replace(dest, dest.parent / ".wiki.previous")  # the state after the first rename only
    cached = read_source(dest, _WIKI)
    assert cached.status == "ok" and [p.text for p in cached.pages] == ["good"]


def test_a_failure_keeps_the_last_good_folder_and_says_so(tmp_path: Path) -> None:
    dest = tmp_path / "cache" / "wiki"
    write_pull(dest, [_page("1", "good")], {"pulled_at": "2026-09-01T12:00:00Z"})
    before = sorted((p.name, p.read_bytes()) for p in dest.iterdir())
    path = record_failure(dest, "403 Forbidden", moment=_T0)
    assert path == failure_path(dest) and path.parent == dest.parent  # beside, never inside
    assert sorted((p.name, p.read_bytes()) for p in dest.iterdir()) == before
    cached = read_source(dest, _WIKI)
    assert cached.status == "failed" and [p.id for p in cached.pages] == ["1"]
    row = standing(cached, now=_T0)
    assert row["error"] == "last pull failed: 403 Forbidden; showing data from 2026-09-01T12:00:00Z"


def test_a_later_good_pull_clears_the_failure(tmp_path: Path) -> None:
    dest = tmp_path / "cache" / "wiki"
    record_failure(dest, "timeout")
    write_pull(dest, [_page("1", "good")], {"pulled_at": "2026-09-01T12:00:00Z"})
    assert not failure_path(dest).exists()
    assert read_source(dest, _WIKI).status == "ok"


def test_never_pulled_and_failed_without_data(tmp_path: Path) -> None:
    dest = tmp_path / "cache" / "wiki"
    never = standing(read_source(dest, _WIKI), now=_T0)
    assert (
        never["status"] == "never_pulled" and never["pulled_at"] is None and "never pulled" in never["note"]
    )
    record_failure(dest, "server down")
    failed = standing(read_source(dest, _WIKI), now=_T0)
    assert failed["status"] == "failed"
    assert failed["error"] == "last pull failed: server down; no earlier pull to show"


def test_a_corrupt_cache_is_a_failed_standing_not_an_exception(tmp_path: Path) -> None:
    dest = tmp_path / "cache" / "wiki"
    dest.mkdir(parents=True)
    (dest / "manifest.json").write_text("{nope", encoding="utf-8")
    cached = read_source(dest, _WIKI)
    assert cached.status == "failed" and "cache unreadable" in (cached.error or "")


def test_stale_after_seven_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "cache" / "wiki"
    monkeypatch.setattr(ext, "_now", lambda: _T0)
    write_pull(dest, [_page("1", "x")], {"pulled_at": ext.utc_stamp()})
    cached = read_source(dest, _WIKI)
    fresh = standing(cached, now=_T0 + timedelta(days=7))
    stale = standing(cached, now=_T0 + timedelta(days=7, hours=1))
    assert (fresh["stale"], fresh["age_days"]) == (False, 7.0)
    assert stale["stale"] is True and stale["age_days"] == 7.0
    assert standing(cached)["stale"] is False  # the default clock is the injected one


def test_the_cache_is_keyed_by_checkout_and_repo_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ext.ENV_CACHE_DIR, str(tmp_path / "c"))
    a = source_cache_dir(tmp_path / "one", "app", "wiki")
    b = source_cache_dir(tmp_path / "two", "app", "wiki")
    assert a.parent.parent == tmp_path / "c" and a.name == "wiki"
    assert a.parent.name.endswith("-app") and len(a.parent.name) == 16 + len("-app")
    assert a != b
    monkeypatch.delenv(ext.ENV_CACHE_DIR)
    assert source_cache_dir(tmp_path, "app", "wiki").parts[-5:-2] == (".cache", "orchestrator", "docs")


# ---- page → DocPage ------------------------------------------------------------------------


def test_confluence_pages_split_by_heading_with_d18_ids_jira_stays_whole() -> None:
    pages = [
        _page("7", "Intro\n\n# Setup\n\nRun it.\n\n```\n# not a heading\n```\n\n## Usage\n\nCall it."),
        _page("ABC-1", "ABC-1: A ticket\n\n# Heading inside\n\nbody", kind="jira"),
    ]
    titles = [d.title for d, _p in doc_pages(pages, "atlassian")]
    assert titles == [
        "mcp:atlassian/7",
        "mcp:atlassian/7#setup",
        "mcp:atlassian/7#usage",
        "mcp:atlassian/ABC-1",
    ]


def test_an_html_body_is_flattened_by_the_local_html_reader() -> None:
    html = "<h1>Setup</h1><p>Create a <code>Client</code>.</p><pre># comment\ncode()</pre>"
    text = page_text(html)
    assert text == html_to_text(html)
    assert text.startswith("# Setup") and "`Client`" in text
    assert page_text("# Markdown\n\n<not html>") == "# Markdown\n\n<not html>"


def test_html_to_text_is_the_file_reader_behaviour(tmp_path: Path) -> None:
    from orchestrator.pkg.doc_source import read_doc_pages

    html = "<html><body><h2>API</h2><p>Use <code>Client</code></p></body></html>"
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    pages = read_doc_pages(tmp_path, sections=False)
    assert [p.text for p in pages] == [html_to_text(html)]


# ---- read-time binding ----------------------------------------------------------------------


def _cache(tmp_path: Path, repo: Path, source: DocSource, pages: list[ExternalPage]) -> Path:
    base = tmp_path / "cache"
    write_pull(source_cache_dir(repo, "app", source.name, base=base), pages, {"pulled_at": ext.utc_stamp()})
    return base


def test_a_page_naming_a_symbol_binds_with_the_unique_anchor_rule(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _cache(
        tmp_path, repo, _WIKI, [_page("1", "# Guide\n\nCreate a `Client`, then call `nowhere_at_all`.")]
    )
    batch = load_or_extract(repo)
    binding = bind_external(batch, repo, "app", [_WIKI], cache_base=base)
    assert [n.id for n in binding.nodes] == ["doc:mcp:atlassian/1#guide"]
    assert [(e.src, e.dst, e.kind) for e in binding.edges] == [
        ("doc:mcp:atlassian/1#guide", "py:lib.client.Client", EdgeKind.MENTIONS)
    ]
    assert binding.meta["doc:mcp:atlassian/1#guide"] == {
        "origin": "mcp:atlassian",
        "source": "wiki",
        "url": "https://wiki/1",
        "title": "Page 1",
    }
    assert [f.mention for f in binding.drift] == ["nowhere_at_all"]
    (row,) = binding.standings
    assert (row["pages"], row["sections"], row["bound_sections"], row["collapsed_into_repo_docs"]) == (
        1,
        1,
        1,
        0,
    )
    # read-only on the batch it was given
    assert not any(n.id.startswith("doc:mcp:") for n in batch.nodes)


def test_an_identical_section_collapses_into_the_repo_doc(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    mirrored = "# Reference\n\n`Client`   answers questions.\n"  # whitespace differs, text does not
    base = _cache(tmp_path, repo, _WIKI, [_page("1", mirrored + "\n# Extra\n\nMore on `Client`.")])
    binding = bind_external(load_or_extract(repo), repo, "app", [_WIKI], cache_base=base)
    assert [n.id for n in binding.nodes] == ["doc:mcp:atlassian/1#extra"]
    assert binding.also_in == {"doc:README.md#reference": ["mcp:atlassian/wiki"]}
    assert binding.standings[0]["collapsed_into_repo_docs"] == 1
    assert collapse_stats(
        repo, read_source(source_cache_dir(repo, "app", "wiki", base=base), _WIKI).pages, "atlassian"
    ) == {
        "sections": 2,
        "identical_to_repo_sections": 1,
    }


def test_a_rename_after_the_pull_drops_the_mention_without_a_re_pull(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _cache(tmp_path, repo, _WIKI, [_page("1", "# Guide\n\nCreate a `Client`.")])
    assert bind_external(load_or_extract(repo), repo, "app", [_WIKI], cache_base=base).edges
    (repo / "lib" / "client.py").write_text("class Customer:\n    pass\n", encoding="utf-8")
    binding = bind_external(load_or_extract(repo), repo, "app", [_WIKI], cache_base=base)
    assert binding.edges == []
    assert [f.mention for f in binding.drift] == ["Client"]


def test_no_sources_or_nothing_pulled_binds_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    batch = load_or_extract(repo)
    assert bind_external(batch, repo, "app", []).standings == []
    empty = bind_external(batch, repo, "app", [_WIKI, _JIRA], cache_base=tmp_path / "nothing")
    assert empty.nodes == [] and [s["status"] for s in empty.standings] == ["never_pulled", "never_pulled"]


# ---- layering -------------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_pkg_and_knowledge_never_import_the_network_side() -> None:
    offenders = [
        f"{path.relative_to(_SRC)} imports {mod}"
        for folder in ("pkg", "knowledge")
        for path in sorted((_SRC / folder).rglob("*.py"))
        for mod in _imports(path)
        if mod.startswith(("orchestrator.mcp", "orchestrator.intake"))
    ]
    assert offenders == []


def test_knowledge_never_reads_external_docs() -> None:
    """D8: understand/state are reproducible in CI, which has no credentials to pull with."""
    offenders = [
        str(path.relative_to(_SRC))
        for path in sorted((_SRC / "knowledge").rglob("*.py"))
        if "orchestrator.pkg.external_docs" in _imports(path)
    ]
    assert offenders == []


def test_importing_external_docs_loads_no_mcp_module() -> None:
    code = (
        "import sys, orchestrator.pkg.external_docs\n"
        "net = ('orchestrator.mcp', 'orchestrator.intake', 'mcp')\n"
        "bad = sorted(m for m in sys.modules if m.startswith(net))\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
