"""GroundingVerifier v0: SHACL conformance + stale-fact detection."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator.pkg import (
    Edge,
    EdgeKind,
    FactBatch,
    GroundingVerifier,
    Node,
    NodeKind,
    Provenance,
    RepoCodeExtractor,
    facts_to_graph,
)

NS = "https://ontology.example.com/enterprise/"

# A shape over the round-trip vocabulary: every Function must carry a name.
SHAPES = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix : <https://ontology.example.com/enterprise/> .
@prefix shapes: <https://ontology.example.com/shapes/enterprise/> .

shapes:FunctionShape a sh:NodeShape ;
  sh:targetClass :Function ;
  sh:property [
    sh:path :hasName ;
    sh:minCount 1 ;
    sh:message "Function must have a name." ;
  ] .
"""


def _batch() -> FactBatch:
    b = FactBatch()
    b.add_node(Node("py:m", NodeKind.MODULE, "m", "python", Provenance("m.py", 1)))
    b.add_node(Node("py:m.f", NodeKind.FUNCTION, "f", "python", Provenance("m.py", 1, 2)))
    b.add_edge(Edge("py:m", "py:m.f", EdgeKind.CONTAINS, Provenance("m.py", 1)))
    return b


# ---- RDF materialisation ----------------------------------------------------


def test_facts_to_graph_emits_individuals_and_edges() -> None:
    g = facts_to_graph(_batch())
    turtle = g.serialize(format="turtle")
    assert "a :Function" in turtle and "a :Module" in turtle
    assert ':hasName "f"' in turtle
    assert ":contains" in turtle
    assert ':hasFile "m.py"' in turtle


# ---- SHACL ------------------------------------------------------------------


def test_shacl_conforming_batch_yields_no_findings(tmp_path: Path) -> None:
    shapes = tmp_path / "shapes.ttl"
    shapes.write_text(SHAPES, encoding="utf-8")
    verifier = GroundingVerifier(_batch(), shapes_path=shapes)
    assert verifier.shacl_findings() == []


def test_shacl_violation_maps_to_provenance(tmp_path: Path) -> None:
    shapes = tmp_path / "shapes.ttl"
    # Stricter shape the batch can't satisfy: Functions must carry an endLine.
    shapes.write_text(
        SHAPES.replace(":hasName ;", ":endLine ;").replace("must have a name", "must have an endLine"),
        encoding="utf-8",
    )
    batch = FactBatch()
    batch.add_node(Node("py:m.g", NodeKind.FUNCTION, "g", "python", Provenance("m.py", 7)))  # no end_line
    verifier = GroundingVerifier(batch, shapes_path=shapes)

    (finding,) = verifier.shacl_findings()
    assert finding.rule == "shacl_violation"
    assert "endLine" in finding.message
    assert (finding.file, finding.line, finding.symbol_id) == ("m.py", 7, "py:m.g")


def test_no_shapes_path_means_no_shacl_findings() -> None:
    assert GroundingVerifier(_batch()).shacl_findings() == []


# ---- staleness --------------------------------------------------------------


def _extracted_repo(tmp_path: Path) -> tuple[Path, FactBatch]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    return 1\n\n\ndef g():\n    return 2\n", encoding="utf-8")
    return repo, RepoCodeExtractor().extract(repo)


def test_fresh_source_has_no_stale_findings(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    assert GroundingVerifier(batch).stale_findings(repo) == []


def test_removed_symbol_is_flagged_stale(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    (repo / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")  # g() deleted

    findings = GroundingVerifier(batch).stale_findings(repo, ["m.py"])
    assert [f.symbol_id for f in findings] == ["py:m.g"]
    assert findings[0].rule == "stale_fact" and "no longer defines" in findings[0].message


def test_deleted_file_makes_all_its_facts_stale(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    (repo / "m.py").unlink()
    stale = {f.symbol_id for f in GroundingVerifier(batch).stale_findings(repo)}
    assert {"py:m", "py:m.f", "py:m.g"} <= stale


# ---- freshness is per-language, not per-Python (WI-2 phase 1) ----------------
#
# `stale_findings` re-extracted every changed file with `PythonExtractor`, so a Go or
# TypeScript file parsed as Python, raised, and every fact in it was reported stale — on
# the PR-review path, whose only targets are other people's repositories. Nothing here
# caught it: `src/` is Python-only and both walkers skip `corpus/`'s dot-prefixed fixture
# roots, so the non-Python path had no test at all. These are that test.
#
# Each asserts **zero** findings on a file nobody touched, and asserts the file has
# recorded facts first — without that guard `stale_findings` skips it as unknown and the
# test passes having checked nothing, which is the failure mode it exists to catch.

_SOURCES: dict[str, tuple[str, str, str]] = {
    # suffix: (grammar module to skip on, filename, source)
    ".go": ("tree_sitter_go", "main.go", 'package main\n\nfunc Greet() string {\n\treturn "hi"\n}\n'),
    ".ts": (
        "tree_sitter_typescript",
        "app.ts",
        "export function greet(name: string): string {\n  return `hi ${name}`;\n}\n",
    ),
    ".java": (
        "tree_sitter_java",
        "App.java",
        'package app;\n\npublic class App {\n  public String greet() { return "hi"; }\n}\n',
    ),
    ".cs": (
        "tree_sitter_c_sharp",
        "App.cs",
        'namespace App;\n\npublic class Greeter {\n  public string Greet() => "hi";\n}\n',
    ),
    ".c": ("tree_sitter_c", "main.c", 'const char *greet(void) {\n  return "hi";\n}\n'),
    ".cpp": (
        "tree_sitter_cpp",
        "main.cpp",
        '#include <string>\n\nstd::string greet() {\n  return "hi";\n}\n',
    ),
    ".sql": ("sqlglot", "schema.sql", "CREATE TABLE users (id INT PRIMARY KEY, email TEXT);\n"),
}


@pytest.mark.parametrize("suffix", sorted(_SOURCES))
def test_an_unmodified_file_is_never_stale_in_any_language(suffix: str, tmp_path: Path) -> None:
    """The bug, per front-end: Python read 0 while Go read 3 and TypeScript 2."""
    grammar, name, source = _SOURCES[suffix]
    pytest.importorskip(grammar, reason=f"install the extra providing {grammar}")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / name).write_text(source, encoding="utf-8")
    batch = RepoCodeExtractor().extract(repo)

    # Guard first: no facts means the assertion below proves nothing.
    assert [n for n in batch.nodes if n.provenance and n.provenance.file == name], (
        f"{name} produced no grounded facts — the freshness assertion would be vacuous"
    )

    verifier = GroundingVerifier(batch)
    assert verifier.stale_findings(repo, [name]) == []
    assert verifier.skipped_freshness == []  # a front-end exists; nothing to skip


def test_go_package_spans_files_and_still_reads_fresh(tmp_path: Path) -> None:
    """Go is the one front-end whose module is the directory, not the file.

    Re-extracting one file of a package is therefore the case most likely to diverge from
    the whole-repo pass — if per-file ids differ from repo-pass ids anywhere, here first.
    """
    pytest.importorskip("tree_sitter_go", reason="install the 'go' extra")
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.go").write_text(
        'package pkg\n\nfunc A() string {\n\treturn "a"\n}\n', encoding="utf-8"
    )
    (repo / "pkg" / "b.go").write_text(
        'package pkg\n\nfunc B() string {\n\treturn "b"\n}\n', encoding="utf-8"
    )

    verifier = GroundingVerifier(RepoCodeExtractor().extract(repo))
    assert verifier.stale_findings(repo, ["pkg/a.go", "pkg/b.go"]) == []


def test_a_language_with_no_front_end_is_skipped_not_judged(tmp_path: Path) -> None:
    """Silence over fiction — and on a base install this is *every* non-Python file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "lib.rs").write_text('pub fn greet() -> &\'static str { "hi" }\n', encoding="utf-8")
    # Rust has no front-end, so nothing extracts it: assert the recorded fact by hand,
    # which is also what a graph carried over from a build that *did* know the file looks
    # like.
    batch = FactBatch()
    batch.add_node(Node("rs:lib.greet", NodeKind.FUNCTION, "greet", "code", Provenance("lib.rs", 1)))

    verifier = GroundingVerifier(batch)
    assert verifier.stale_findings(repo, ["lib.rs"]) == []
    assert verifier.skipped_freshness == ["lib.rs"]


def test_a_file_its_own_front_end_cannot_parse_is_still_stale(tmp_path: Path) -> None:
    """Unchanged behaviour, asserted so the WI-2 fix cannot quietly relax it.

    A broken file is genuinely unverifiable, and the aggressive answer is the safe one —
    unlike the no-front-end case above, where nothing is wrong with the file at all.
    """
    repo, batch = _extracted_repo(tmp_path)
    (repo / "m.py").write_text("def f(:\n", encoding="utf-8")  # syntax error

    stale = {f.symbol_id for f in GroundingVerifier(batch).stale_findings(repo, ["m.py"])}
    assert {"py:m", "py:m.f", "py:m.g"} <= stale


# ---- documentation drift (phase 3) ------------------------------------------


def test_doc_findings_flag_stale_symbol_claims(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    (repo / "README.md").write_text(
        "`f` is the entry point; the old `deleted_helper` is gone. See `docs/x.md`.\n",
        encoding="utf-8",
    )
    findings = GroundingVerifier(batch).doc_findings(repo)
    mentions = {m.group(1) for f in findings if (m := re.search(r"references `([^`]+)`", f.message))}
    assert "deleted_helper" in mentions  # doc claims a symbol the graph lacks
    assert "f" not in mentions  # a real symbol doesn't drift
    assert "docs/x.md" not in mentions  # a path is filtered by symbolish_drift
    assert all(f.rule == "doc_drift" and f.file == "README.md" for f in findings)


def test_doc_findings_empty_without_docs(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    assert GroundingVerifier(batch).doc_findings(repo) == []


def test_extracting_a_file_with_a_bad_escape_prints_nothing(tmp_path: Path, capsys: object) -> None:
    """Spine reads the target's code; it does not run it, and it is not their linter.

    `ast.parse` compiles, and compiling emits `SyntaxWarning` for an invalid escape sequence in
    the source being read. Extracting the five pinned benchmark repositories printed ~60 lines of
    it before any result — which is what a reader following BENCHMARK.md would have seen, and
    would reasonably have read as "something is broken".
    """
    import warnings as _warnings

    repo = tmp_path / "repo"
    repo.mkdir()
    # A regex-ish string with a bare backslash: valid Python, warns on compile.
    (repo / "m.py").write_text('PATTERN = "\\d+"\n\n\ndef f():\n    return PATTERN\n', encoding="utf-8")

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        batch = RepoCodeExtractor().extract(repo)

    assert [w for w in caught if issubclass(w.category, SyntaxWarning)] == []
    assert any(n.name == "f" for n in batch.nodes), "and it still extracted the file"
