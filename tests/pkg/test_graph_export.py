"""Whole-graph exports: complete, valid, and byte-identical on re-export.

The byte-equality tests are the point of this file. Every exit criterion in the G5 spec
claims "identical commit → identical bytes", and that only stays true if something checks
it — unstable ``set``/``dict`` iteration is the usual way it silently stops being true.

``ET.parse`` calls below carry ``noqa: S314``. The rule guards against XML-attack payloads in
*untrusted* input; here the input is a file this test wrote moments earlier from an in-process
fixture, so there is no untrusted data and no reason to add ``defusedxml`` as a dependency.
Parsing with a real XML parser is the whole point — it is what proves the writer emits a
well-formed document, which asserting on strings would not.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.graph_export import (
    GRAPH_FORMATS,
    WRITERS,
    export_dot,
    export_graphml,
    export_json,
)


def _batch() -> FactBatch:
    """A small graph with the awkward cases: XML-hostile names, a dangling edge, no provenance."""
    b = FactBatch()
    b.add_node(Node(id="py:app.mod", kind=NodeKind.MODULE, name="app.mod", language="python"))
    b.add_node(
        Node(
            id="py:app.mod.parse",
            kind=NodeKind.FUNCTION,
            name="parse",
            language="python",
            provenance=Provenance(file="app/mod.py", line=10, end_line=20),
        )
    )
    # A name that must be escaped in XML and quoted in DOT.
    b.add_node(
        Node(
            id='cpp:Widget<T&>::run("x")',
            kind=NodeKind.FUNCTION,
            name='run("x") & <T>',
            language="cpp",
            provenance=Provenance(file="src/w.cpp", line=3),
        )
    )
    b.add_node(Node(id="py:external.thing", kind=NodeKind.TYPE, name="thing", external=True))
    b.add_edge(Edge(src="py:app.mod", dst="py:app.mod.parse", kind=EdgeKind.CONTAINS))
    b.add_edge(
        Edge(
            src="py:app.mod.parse",
            dst='cpp:Widget<T&>::run("x")',
            kind=EdgeKind.CALLS,
            provenance=Provenance(file="app/mod.py", line=12),
        )
    )
    # Dangling: no node declares this id.
    b.add_edge(Edge(src="py:app.mod.parse", dst="py:nowhere.at.all", kind=EdgeKind.CALLS))
    return b


@pytest.mark.parametrize("fmt", GRAPH_FORMATS)
def test_reexport_is_byte_identical(fmt: str, tmp_path: Path) -> None:
    """The property the spec sells: same facts in, same bytes out. Diffable, reviewable."""
    a, c = tmp_path / f"a.{fmt}", tmp_path / f"c.{fmt}"
    WRITERS[fmt](_batch(), a)
    WRITERS[fmt](_batch(), c)
    assert a.read_bytes() == c.read_bytes()


@pytest.mark.parametrize("fmt", GRAPH_FORMATS)
def test_export_is_complete_not_truncated(fmt: str, tmp_path: Path) -> None:
    """Exports carry every node and every edge — unlike the bounded *visual* surfaces."""
    counts = WRITERS[fmt](_batch(), tmp_path / f"g.{fmt}")
    assert counts["nodes"] == 4
    assert counts["edges"] == 3


def test_graphml_is_well_formed_and_declares_every_edge_endpoint(tmp_path: Path) -> None:
    """Strict readers reject an edge whose endpoint isn't declared, so the dangling id
    must appear as a node rather than being dropped with the edge."""
    path = tmp_path / "g.graphml"
    counts = export_graphml(_batch(), path)
    root = ET.parse(path).getroot()  # noqa: S314 — self-written file; raises on malformed XML

    ns = "{http://graphml.graphdrawing.org/xmlns}"
    declared = {n.get("id") for n in root.iter(f"{ns}node")}
    endpoints = {e.get("source") for e in root.iter(f"{ns}edge")} | {
        e.get("target") for e in root.iter(f"{ns}edge")
    }
    assert endpoints <= declared, "an edge references a node the file never declares"
    assert "py:nowhere.at.all" in declared
    assert counts["dangling"] == 1


def test_graphml_escapes_xml_hostile_names(tmp_path: Path) -> None:
    """`Widget<T&>::run("x")` must survive as data, not break the document."""
    path = tmp_path / "g.graphml"
    export_graphml(_batch(), path)
    raw = path.read_text(encoding="utf-8")
    assert "<T&>" not in raw, "raw ampersand/angle bracket leaked into the XML"
    parsed = ET.parse(path).getroot()  # noqa: S314 — self-written file
    names = {d.text for d in parsed.iter("{http://graphml.graphdrawing.org/xmlns}data")}
    assert 'run("x") & <T>' in names


def test_dangling_placeholder_carries_no_kind(tmp_path: Path) -> None:
    """We know the id and nothing else. Inventing a NodeKind for it would extend the
    universal vocabulary for a renderer's convenience — what invariant #1 forbids."""
    path = tmp_path / "g.json"
    export_json(_batch(), path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    placeholder = next(n for n in doc["nodes"] if n["id"] == "py:nowhere.at.all")
    assert placeholder["dangling"] is True
    assert "kind" not in placeholder


def test_json_carries_edges_unlike_pkg_extract_json(tmp_path: Path) -> None:
    """`pkg extract --json` omits edges; a graph projection without edges isn't a graph."""
    path = tmp_path / "g.json"
    export_json(_batch(), path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["summary"]["edges"] == 3
    kinds = {e["kind"] for e in doc["edges"]}
    assert kinds == {"CONTAINS", "CALLS"}
    contains = next(e for e in doc["edges"] if e["kind"] == "CONTAINS")
    assert (contains["src"], contains["dst"]) == ("py:app.mod", "py:app.mod.parse")


def test_json_records_provenance_and_groundedness(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    export_json(_batch(), path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    parse = next(n for n in doc["nodes"] if n["id"] == "py:app.mod.parse")
    assert parse["provenance"] == {"file": "app/mod.py", "line": 10, "end_line": 20}
    assert parse["grounded"] is True
    external = next(n for n in doc["nodes"] if n["id"] == "py:external.thing")
    assert external["grounded"] is False


def test_dot_quotes_ids_and_declares_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "g.dot"
    export_dot(_batch(), path)
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("digraph pkg {")
    assert raw.rstrip().endswith("}")
    # The quote inside the C++ id must be backslash-escaped, not terminate the DOT string.
    assert r"cpp:Widget<T&>::run(\"x\")" in raw
    assert '"py:nowhere.at.all" [label="py:nowhere.at.all", dangling="true"];' in raw


def test_empty_graph_still_writes_a_valid_file(tmp_path: Path) -> None:
    """A repo with no extractable facts must produce a readable empty export, not a crash."""
    for fmt in GRAPH_FORMATS:
        path = tmp_path / f"empty.{fmt}"
        counts = WRITERS[fmt](FactBatch(), path)
        assert counts == {"nodes": 0, "edges": 0, "dangling": 0}
        assert path.read_text(encoding="utf-8").strip()
    ET.parse(tmp_path / "empty.graphml")  # noqa: S314 — self-written file
    assert json.loads((tmp_path / "empty.json").read_text(encoding="utf-8"))["nodes"] == []


# ---- the recorded-intent tier reaches the export (spec phase 1) ---------------
#
# `pkg export` applied `link_docs` as a post-pass and not `link_intents`, so Intent nodes and
# SERVES edges were absent from every export. The comprehension test plan recorded that as
# "Intent nodes: 0 / SERVES edges: 0" and concluded the facts were unreachable. They were only
# never added: nothing here filters by kind.


def _repo_with_history(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def handler():\n    return 1\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)

    git("init", "--quiet")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    git("add", "-A")
    git("commit", "--quiet", "-m", "PROJ-7 add the handler")
    (repo / "n.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    git("add", "-A")
    # A second distinct number: one alone is indistinguishable from a standard and is declined
    # by design (spec §6.1), so a single-ticket fixture would test the rejection, not the join.
    git("commit", "--quiet", "-m", "PROJ-8 add another")
    return repo


def test_the_exporters_do_not_filter_by_kind(tmp_path: Path) -> None:
    """The reason the facts were missing was that nothing added them, not that anything
    rejected them. If a kind filter ever appears here, this is the test that says so."""
    from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind

    batch = FactBatch()
    batch.add_node(Node("py:m.f", NodeKind.FUNCTION, "f", "python", Provenance("m.py", 1)))
    batch.add_node(Node("intent:PROJ-7", NodeKind.INTENT, "PROJ-7", "", None))
    batch.add_edge(Edge("py:m.f", "intent:PROJ-7", EdgeKind.SERVES, Provenance("m.py", 1)))

    out = tmp_path / "g.json"
    export_json(batch, out)
    data = json.loads(out.read_text(encoding="utf-8"))

    assert any(n["kind"] == "Intent" for n in data["nodes"])
    assert any(e["kind"] == "SERVES" for e in data["edges"])


def test_an_intent_node_survives_carrying_no_provenance(tmp_path: Path) -> None:
    """An Intent is not a place in a file, so it is ungrounded by construction.

    Any consumer assuming every node has a `file:line` breaks on this kind — the exporter
    included, which is why it is asserted rather than assumed.
    """
    from orchestrator.pkg.facts import FactBatch, Node, NodeKind

    batch = FactBatch()
    batch.add_node(Node("intent:PROJ-7", NodeKind.INTENT, "PROJ-7", "", None))

    out = tmp_path / "g.json"
    export_json(batch, out)
    node = json.loads(out.read_text(encoding="utf-8"))["nodes"][0]

    assert node["kind"] == "Intent"
    assert not node.get("file")
    assert node.get("grounded") is False


def test_link_intents_attributes_a_symbol_to_its_commits_key(tmp_path: Path) -> None:
    """End to end on a real repository: blame → commit message → key → SERVES."""
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.intent_link import link_intents

    repo = _repo_with_history(tmp_path)
    batch = RepoCodeExtractor().extract(repo)
    coverage = link_intents(batch, repo)

    assert coverage.prefixes_used == ("PROJ",)
    assert coverage.intents == 2
    assert coverage.symbols_attributed >= 1
    assert any(n.id == "intent:PROJ-7" for n in batch.nodes)


# ---- issue keys vs things that merely look like them (spec §6.1) --------------


def test_a_standard_is_not_read_as_a_ticket(tmp_path: Path) -> None:
    """`SHA-256`, `ISO-8601`, `UTF-16` and `CVE-2024` matched the generic key pattern.

    Five of 37 intents on this repository were of that shape — a `SERVES` edge asserting a
    symbol was changed for a ticket nobody ever filed. The join was right; reading every match
    as an issue key was not.
    """
    import subprocess

    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.intent_link import link_intents

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)

    git("init", "--quiet")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    # One key per commit: `_all_commit_keys` reads the *first* match in a message, so a
    # standard sharing a commit with a real key never surfaces at all. Giving each its own
    # commit is what puts the discriminator under test rather than that incidental filter.
    for i, message in enumerate(
        [
            "PROJ-1 add the first thing",
            "PROJ-2 add the second",
            "hash it with SHA-256 throughout",
            "format dates as ISO-8601",
        ]
    ):
        (repo / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "--quiet", "-m", message)

    coverage = link_intents(RepoCodeExtractor().extract(repo), repo)

    assert coverage.prefixes_used == ("PROJ",)
    assert "SHA" in coverage.prefixes_rejected
    assert "ISO" in coverage.prefixes_rejected


def test_a_repository_with_no_discernible_tracker_gets_no_intents(tmp_path: Path) -> None:
    """One number is indistinguishable from a standard, so a lone prefix wins nothing.

    Silence over fiction: a coin-flip between `SHA-256` and a real key is not an answer.
    """
    import subprocess

    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.intent_link import link_intents

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)

    git("init", "--quiet")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (repo / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "--quiet", "-m", "encode with UTF-8 throughout")

    coverage = link_intents(RepoCodeExtractor().extract(repo), repo)

    assert coverage.prefixes_used == ()
    assert coverage.intents == 0


def test_an_explicit_prefix_overrides_the_inference(tmp_path: Path) -> None:
    """The inference is the default, not the mechanism. A repo with two trackers says so."""
    import subprocess

    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.intent_link import link_intents

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)

    git("init", "--quiet")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (repo / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "--quiet", "-m", "OPS-9 wire the thing")

    # One number, so the inference would decline it. Named explicitly, it counts.
    coverage = link_intents(RepoCodeExtractor().extract(repo), repo, prefixes=["OPS"])

    assert coverage.prefixes_used == ("OPS",)
    assert coverage.intents == 1


# ---- TypeScript receiver typing (call-resolution spec, Option A) --------------


def _ts_repo(tmp_path: Path, callers: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "handler.ts").write_text(
        'export class Handler {\n  run(): string { return "x"; }\n}\n', encoding="utf-8"
    )
    (repo / "app" / "callers.ts").write_text(
        'import { Handler } from "./handler";\n\n' + callers, encoding="utf-8"
    )
    return repo


def _ts_calls(repo: Path) -> set[tuple[str, str]]:
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.facts import EdgeKind

    batch = RepoCodeExtractor().extract(repo)
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def test_a_method_on_an_annotated_parameter_resolves(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    repo = _ts_repo(tmp_path, "export function f(h: Handler): string { return h.run(); }\n")
    assert ("ts:app/callers.f", "ts:app/handler.Handler.run") in _ts_calls(repo)


def test_receiving_an_instance_is_not_calling_its_constructor(tmp_path: Path) -> None:
    """The precision bug the corpus caught within a minute of the feature working.

    `new Handler().run()` reaches the constructor *and* the method. `h.run()` on an annotated
    parameter reaches only the method — the caller was handed the instance. Emitting the type
    edge for both read as a call to `Handler` that never happens, and cost 0.20 precision.
    """
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    repo = _ts_repo(tmp_path, "export function f(h: Handler): string { return h.run(); }\n")
    assert ("ts:app/callers.f", "ts:app/handler.Handler") not in _ts_calls(repo)


def test_constructing_then_calling_reaches_both(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    repo = _ts_repo(tmp_path, "export function f(): string { return new Handler().run(); }\n")
    calls = _ts_calls(repo)
    assert ("ts:app/callers.f", "ts:app/handler.Handler.run") in calls
    assert ("ts:app/callers.f", "ts:app/handler.Handler") in calls


def test_a_method_the_type_does_not_have_is_not_invented(tmp_path: Path) -> None:
    """The whole reason resolution is deferred to `finalize`.

    One file can resolve `h: Handler` and cannot know whether `Handler` has a `missing`.
    Minting the id anyway is the fabrication that cost 497 edges in Python — so the check is
    existence in the merged graph, and absence means no edge.
    """
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    repo = _ts_repo(tmp_path, "export function f(h: Handler): string { return h.missing(); }\n")
    calls = _ts_calls(repo)
    assert not any(dst.endswith(".missing") for _src, dst in calls)
    assert not any("missing" in dst for _src, dst in calls)


def test_an_unknown_receiver_type_resolves_to_nothing(tmp_path: Path) -> None:
    """Skip rather than guess: a receiver whose type is not stated stays unresolved."""
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    repo = _ts_repo(tmp_path, "export function f(h): string { return h.run(); }\n")
    assert ("ts:app/callers.f", "ts:app/handler.Handler.run") not in _ts_calls(repo)
