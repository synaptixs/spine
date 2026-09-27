"""``orchestrator mcp ingest-docs`` for ``rag`` sources: shapes, strategies, bounds, failure (SSPN-82).

The servers are in-process fakes (``_rag_servers``) replaying each one's published answer shape;
the code graph is a real extraction of a three-file repository, so the query order is the order
the real graph produces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.mcp.doc_pull import ingest_docs
from orchestrator.mcp.models import MCPToolResult
from orchestrator.mcp.rag_pull import Chunk, normalise
from orchestrator.pkg import load_or_extract
from orchestrator.pkg.external_docs import (
    QUERIES_FILE,
    chunk_id,
    read_source,
    source_cache_dir,
)
from orchestrator.pkg.repos import DocSource
from tests.mcp._rag_servers import (
    Doc,
    FakeBedrock,
    FakeChroma,
    FakeQdrant,
    FakeRag,
    FakeRagie,
    FakeSearchFetch,
)

_GUIDE = "Create a `Client` first, then call `Client.ask`."
_OPS = "The `Server` restarts nightly."
_DOCS: list[Doc] = [
    ("g1", _GUIDE, {"source": "guides/client.md", "title": "Client guide"}),
    ("o1", _OPS, {"source": "https://wiki/ops", "title": "Ops"}),
]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "lib" / "client.py").write_text("class Client:\n    def ask(self, q):\n        return q\n")
    (repo / "lib" / "server.py").write_text(
        "from lib.client import Client\n\nclass Server:\n    def run(self):\n        return Client().ask(1)\n"
    )
    return repo


async def _pull(fake: FakeRag, source: DocSource, tmp_path: Path, **kw: Any) -> tuple[dict[str, Any], Path]:
    repo = _repo(tmp_path)
    (row,) = await ingest_docs(fake.registry(), repo, "app", [source], cache_base=tmp_path / "c", **kw)
    return row, source_cache_dir(repo, "app", source.name, base=tmp_path / "c")


def _rag(server: str, **kw: Any) -> DocSource:
    return DocSource("kb", server, "rag", **kw)


# ---- the normaliser: five shapes, one result ------------------------------------------------


def _result_of(fake: FakeRag, name: str, args: dict[str, Any]) -> MCPToolResult:
    return fake.answer(name, args)


def _parsed(result: MCPToolResult) -> list[Chunk]:
    try:
        data = json.loads(result.text) if result.text.strip() else None
    except json.JSONDecodeError:
        data = None
    return normalise(data, result)


def test_every_server_shape_normalises_to_the_same_chunks() -> None:
    """Q41: the same two passages, read through each server's own answer shape."""
    both = "e"  # matches both passages' text
    shapes = {
        "chroma query": _result_of(
            FakeChroma(_DOCS), "chroma_query_documents", {"query_texts": [both], "n_results": 5}
        ),
        "chroma get": _result_of(FakeChroma(_DOCS), "chroma_get_documents", {"limit": 10, "offset": 0}),
        "qdrant": _result_of(FakeQdrant(_DOCS), "qdrant-find", {"query": both}),
        "bedrock": _result_of(FakeBedrock(_DOCS), "QueryKnowledgeBases", {"query": both}),
        "ragie": _result_of(FakeRagie(_DOCS), "retrieve", {"query": both}),
    }
    for label, result in shapes.items():
        chunks = _parsed(result)
        assert [c.text for c in chunks] == [_GUIDE, _OPS], label
        if label != "ragie":  # bare text carries no metadata to take a source from
            assert [c.source for c in chunks] == ["guides/client.md", "https://wiki/ops"], label
    # ids: the server's own where it gives one, sha1(text)[:12] where it does not
    assert [c.chunk_id for c in _parsed(shapes["chroma query"])] == ["g1", "o1"]
    for label in ("qdrant", "bedrock", "ragie"):
        assert [c.chunk_id for c in _parsed(shapes[label])] == [chunk_id(_GUIDE), chunk_id(_OPS)], label
    assert [c.score for c in _parsed(shapes["bedrock"])] == [0.9, 0.8]
    assert _parsed(shapes["chroma get"])[0].title == "Client guide"


def test_qdrant_header_line_and_several_entries_in_one_block() -> None:
    joined = (
        "Results for the query 'client'"
        "<entry><content>one</content><metadata>null</metadata></entry>\n"
        '<entry><content>two</content><metadata>{"source": "a.md"}</metadata></entry>'
    )
    chunks = normalise(None, MCPToolResult(text=joined, blocks=(joined,)))
    assert [(c.text, c.source) for c in chunks] == [("one", ""), ("two", "a.md")]


def test_a_fetch_answer_and_a_structured_only_answer_normalise() -> None:
    fetch = {"id": "d-9", "title": "Guide", "text": "Use `Client`.", "url": "https://kb/9", "metadata": {}}
    (one,) = normalise(fetch, MCPToolResult(text=json.dumps(fetch)))
    assert (one.chunk_id, one.title, one.source, one.page().url) == (
        "d-9",
        "Guide",
        "https://kb/9",
        "https://kb/9",
    )
    structured = {"result": ["first passage", "second passage"]}
    got = normalise(None, MCPToolResult(text="first passagesecond passage", structured=structured))
    assert [c.text for c in got] == ["first passage", "second passage"]


def test_bare_text_that_looks_like_a_number_is_still_prose() -> None:
    (chunk,) = normalise(42, MCPToolResult(text="42", blocks=("42",)))
    assert chunk.text == "42"


def test_a_zero_hit_qdrant_answer_is_no_chunk() -> None:
    """The header alone is what ``qdrant-find`` returns for a miss — it is framing, not a passage."""
    header = "Results for the query 'lib'"
    assert normalise(None, MCPToolResult(text=header, blocks=(header,))) == []
    miss = "No information found for the query 'lib'"
    assert normalise(None, MCPToolResult(text=miss, blocks=(miss,))) == []


# ---- strategy: enumerate vs query -----------------------------------------------------------


def _corpus(n: int) -> list[Doc]:
    return [(f"d{i:03}", f"passage {i} about `Client`", {}) for i in range(n)]


async def test_a_list_tool_walks_the_corpus_in_pages(tmp_path: Path) -> None:
    fake = FakeChroma(_corpus(250), read_only={"chroma_get_documents": True})
    row, dest = await _pull(fake, _rag("chroma", collection="docs"), tmp_path)
    assert row["status"] == "ok", row
    assert (row["strategy"], row["pulled"], row["bound"], row["truncated"]) == (
        "enumerate",
        250,
        "250 of 250",
        False,
    )
    assert [c[1]["offset"] for c in fake.calls] == [0, 100, 200]
    assert all(c[0] == "chroma_get_documents" and c[1]["collection_name"] == "docs" for c in fake.calls)
    cached = read_source(dest, _rag("chroma"))
    assert cached.strategy == "enumerate" and cached.enumerated
    assert {p.kind for p in cached.pages} == {"chunk"} and cached.queries == {}
    assert not (dest / QUERIES_FILE).exists()


async def test_an_enumeration_stops_at_max_chunks_and_says_so(tmp_path: Path) -> None:
    fake = FakeChroma(_corpus(250), read_only={"chroma_get_documents": True})
    row, _dest = await _pull(fake, _rag("chroma", collection="docs", max_chunks=120), tmp_path)
    assert (row["pulled"], row["cap"], row["truncated"]) == (120, 120, True)
    assert row["bound"] == "120 of at least 200 (cap reached)"


async def test_without_a_list_tool_one_query_per_module_and_class_most_called_first(tmp_path: Path) -> None:
    fake = FakeQdrant(_DOCS, only=("qdrant-find",))
    source = _rag("qdrant", max_queries=2, trust_read_only=("qdrant-find",))
    row, dest = await _pull(fake, source, tmp_path)
    assert row["status"] == "ok", row
    # Client is imported and called (2 callers); the rest have none and fall back to id order
    assert [c[1]["query"] for c in fake.calls] == ["Client", "lib"]
    assert row["strategy"] == "query"
    assert row["bound"] == "queried 2 of 5 symbols (cap reached) → 1 chunk(s)"
    cached = read_source(dest, source)
    assert cached.queries == {"Client": (chunk_id(_GUIDE),), "lib": ()}
    assert not cached.enumerated
    manifest = cached.manifest
    assert manifest["strategy"] == "query" and manifest["counts"]["queried"] == 2
    assert manifest["counts"]["candidates"] == 5 and manifest["plan"]["tool"] == "qdrant-find"


async def test_query_args_follow_the_discovered_spelling(tmp_path: Path) -> None:
    chroma = FakeChroma(_DOCS, only=("chroma_query_documents",), read_only={"chroma_query_documents": True})
    await _pull(chroma, _rag("chroma", collection="docs", top_k=3, max_queries=1), tmp_path / "a")
    assert chroma.calls == [
        ("chroma_query_documents", {"collection_name": "docs", "query_texts": ["Client"], "n_results": 3})
    ]
    bedrock = FakeBedrock(_DOCS, read_only={"QueryKnowledgeBases": True})
    await _pull(bedrock, _rag("bedrock", collection="KB1", top_k=4, max_queries=1), tmp_path / "b")
    assert bedrock.calls == [
        ("QueryKnowledgeBases", {"knowledge_base_id": "KB1", "query": "Client", "number_of_results": 4})
    ]


async def test_top_k_bounds_a_server_that_takes_no_count(tmp_path: Path) -> None:
    many: list[Doc] = [(f"x{i}", f"`Client` note {i}", {}) for i in range(12)]
    fake = FakeQdrant(many, only=("qdrant-find",), read_only={"qdrant-find": True})
    row, dest = await _pull(fake, _rag("qdrant", top_k=3, max_queries=1), tmp_path)
    assert row["pulled"] == 3 and len(read_source(dest, _rag("qdrant")).queries["Client"]) == 3


async def test_search_then_fetch_each_result_up_to_top_k(tmp_path: Path) -> None:
    fake = FakeSearchFetch(_DOCS, read_only={"search": True, "fetch": True})
    row, dest = await _pull(fake, _rag("kb", top_k=5, max_queries=2), tmp_path)
    assert row["status"] == "ok", row
    assert [c[0] for c in fake.calls] == ["search", "fetch", "search"]
    (page,) = read_source(dest, _rag("kb")).pages
    assert (page.id, page.title, page.text) == ("g1", "Client guide", _GUIDE)


async def test_the_graph_is_built_once_and_only_when_a_query_pull_runs(tmp_path: Path) -> None:
    built: list[int] = []

    def graph() -> Any:
        built.append(1)
        return load_or_extract(_repo(tmp_path / "g"))

    fake = FakeRagie(_DOCS, read_only={"retrieve": True})
    repo = _repo(tmp_path)
    two = [_rag("ragie", max_queries=1), DocSource("kb2", "ragie", "rag", max_queries=1)]
    await ingest_docs(fake.registry(), repo, "app", two, cache_base=tmp_path / "c", graph=graph)
    assert built == [1]
    built.clear()
    rows = await ingest_docs(
        fake.registry(), repo, "app", two, cache_base=tmp_path / "c", graph=graph, dry_run=True
    )
    assert built == [] and [r["status"] for r in rows] == ["planned", "planned"]
    assert rows[0]["tools"][0]["tool"] == "retrieve" and rows[0]["strategy"] == "query"


# ---- guard and ambiguity at pull time: nothing is called -------------------------------------


async def test_an_unannotated_tool_is_refused_at_pull_and_nothing_is_called(tmp_path: Path) -> None:
    fake = FakeQdrant(_DOCS, only=("qdrant-find",))  # no readOnlyHint, no trust_read_only
    row, dest = await _pull(fake, _rag("qdrant"), tmp_path)
    assert row["status"] == "refused" and "qdrant:qdrant-find (read_only=None" in row["error"]
    assert fake.calls == [] and read_source(dest, _rag("qdrant")).status == "failed"


async def test_an_ambiguous_server_is_refused_naming_both_and_nothing_is_called(tmp_path: Path) -> None:
    class TwoFinders(FakeQdrant):
        schemas = {**FakeQdrant.schemas, "semantic_search": FakeQdrant.schemas["qdrant-find"]}

    fake = TwoFinders(_DOCS)
    row, _dest = await _pull(
        fake, _rag("qdrant", trust_read_only=("qdrant-find", "semantic_search")), tmp_path
    )
    assert row["status"] == "refused"
    assert "qdrant:qdrant-find, qdrant:semantic_search" in row["error"] and fake.calls == []


# ---- failure keeps the last good pull ------------------------------------------------------


async def test_an_error_body_mid_pull_keeps_the_last_good_cache(tmp_path: Path) -> None:
    source = _rag("ragie", max_queries=3)
    good = FakeRagie(_DOCS, read_only={"retrieve": True})
    first, dest = await _pull(good, source, tmp_path)
    assert first["status"] == "ok"
    before = sorted(p.id for p in read_source(dest, source).pages)
    bad = FakeRagie(_DOCS, read_only={"retrieve": True}, soft_fail_after=1)
    (row,) = await ingest_docs(bad.registry(), tmp_path / "repo", "app", [source], cache_base=tmp_path / "c")
    assert row["status"] == "failed" and "401 Unauthorized" in row["error"]
    cached = read_source(dest, source)
    assert cached.status == "failed" and sorted(p.id for p in cached.pages) == before
    assert set(cached.queries) == {
        "Client",
        "lib",
        "client",
    }  # the last good pull's queries.json, swapped with it


def test_cli_dry_run_reports_the_discovered_plan_and_exits_2_on_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import orchestrator.cli.registry as cli_registry
    from orchestrator.cli import app

    monkeypatch.setenv("ORCHESTRATOR_DOCS_CACHE_DIR", str(tmp_path / "cache"))
    repo = _repo(tmp_path)
    (repo / ".spine").mkdir()
    (repo / ".spine" / "repos.yaml").write_text(
        "repos:\n  app: ..\ndocs:\n  app:\n"
        "    - {name: kb, server: chroma, rag: {collection: docs, trust_read_only: [chroma_get_documents]}}\n"
        "    - {name: notes, server: chroma, rag: {collection: docs, tool: chroma_query_documents}}\n",
        encoding="utf-8",
    )
    fake = FakeChroma(_DOCS)
    monkeypatch.setattr(cli_registry, "_mcp_build_registry", lambda _configs: fake.registry())
    monkeypatch.setattr(cli_registry, "_mcp_load_configs", lambda _path=None: [])
    ok = CliRunner().invoke(app, ["mcp", "ingest-docs", "--repo", str(repo), "--source", "kb", "--dry-run"])
    assert ok.exit_code == 0, ok.output
    (row,) = json.loads(ok.output)["sources"]
    assert (row["status"], row["strategy"], row["tools"][0]["tool"]) == (
        "planned",
        "enumerate",
        "chroma_get_documents",
    )
    refused = CliRunner().invoke(app, ["mcp", "ingest-docs", "--repo", str(repo), "--source", "notes"])
    assert refused.exit_code == 2 and "chroma_query_documents" in refused.output
    assert fake.calls == []
