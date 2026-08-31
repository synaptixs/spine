"""`pkg accuracy` — precision and recall against hand-labelled ground truth.

The rules under test are the ones that were *decided* rather than obvious: what an external
node costs, what an empty expected set scores, and the invariant that annotating a fact never
moves the number it annotates.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg.accuracy import CorpusError, score_case, score_corpus

SOURCE = """\
def helper() -> int:
    return 1


def caller() -> int:
    return helper()
"""

NODES = [
    {"id": "py:app.mod", "kind": "Module"},
    {"id": "py:app.mod.helper", "kind": "Function"},
    {"id": "py:app.mod.caller", "kind": "Function"},
]
EDGES = [
    {"src": "py:app.mod", "dst": "py:app.mod.helper", "kind": "CONTAINS"},
    {"src": "py:app.mod", "dst": "py:app.mod.caller", "kind": "CONTAINS"},
    {"src": "py:app.mod.caller", "dst": "py:app.mod.helper", "kind": "CALLS"},
]


def _case(
    tmp_path: Path,
    *,
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    source: str = SOURCE,
    language: str = "python",
    **extra: Any,
) -> Path:
    case = tmp_path / language / "c1"
    repo = case / ".repo" / "app"
    repo.mkdir(parents=True)
    (repo / "mod.py").write_text(source, encoding="utf-8")
    spec: dict[str, Any] = {
        "language": language,
        "case": "c1",
        "root": ".repo",
        "nodes": NODES if nodes is None else nodes,
        "edges": EDGES if edges is None else edges,
        **extra,
    }
    (case / "expected.json").write_text(json.dumps(spec), encoding="utf-8")
    return case


def _score(report: Any, group: str, kind: str) -> Any:
    return next(s for s in getattr(report, group) if s.kind == kind)


def test_a_fully_labelled_case_scores_one(tmp_path: Path) -> None:
    report = score_case(_case(tmp_path))
    for group, kind in (("nodes", "Function"), ("edges", "CALLS"), ("edges", "CONTAINS")):
        score = _score(report, group, kind)
        assert score.precision == 1.0, f"{group}/{kind}"
        assert score.recall == 1.0, f"{group}/{kind}"
    assert report.missing == ()
    assert report.unlabelled == ()


def test_an_unfound_fact_costs_recall_not_precision(tmp_path: Path) -> None:
    """A labelled edge the extractor cannot see is a miss, not a wrong answer."""
    edges = [*EDGES, {"src": "py:app.mod.helper", "dst": "py:app.mod.caller", "kind": "CALLS"}]
    report = score_case(_case(tmp_path, edges=edges))

    calls = _score(report, "edges", "CALLS")
    assert calls.precision == 1.0
    assert calls.recall == 0.5
    assert "py:app.mod.helper -CALLS-> py:app.mod.caller" in report.missing


def test_an_unlabelled_emission_costs_precision_not_recall(tmp_path: Path) -> None:
    """A fact the extractor invents is a wrong answer, not a miss."""
    edges = [e for e in EDGES if e["kind"] != "CALLS"]
    report = score_case(_case(tmp_path, edges=edges))

    calls = _score(report, "edges", "CALLS")
    assert calls.precision == 0.0
    assert calls.recall is None, "nothing was expected, so recall is undefined — not zero"
    assert "py:app.mod.caller -CALLS-> py:app.mod.helper" in report.unlabelled


def test_an_edge_to_an_external_target_counts_against_precision(tmp_path: Path) -> None:
    """The decided rule: external *nodes* are placeholders, but an edge to one is a claim.

    Excluding both is what scored a call to a local variable (`build -CALLS-> py:cls`) at
    1.0 precision on the first corpus run.
    """
    source = "import json\n\n\ndef dump(x: dict) -> str:\n    return json.dumps(x)\n"
    nodes = [{"id": "py:app.mod", "kind": "Module"}, {"id": "py:app.mod.dump", "kind": "Function"}]
    edges = [{"src": "py:app.mod", "dst": "py:app.mod.dump", "kind": "CONTAINS"}]
    report = score_case(_case(tmp_path, nodes=nodes, edges=edges, source=source))

    calls = _score(report, "edges", "CALLS")
    assert calls.emitted == 1, "the call to the external json.dumps must be counted"
    assert calls.precision == 0.0
    assert any("py:json.dumps" in u for u in report.unlabelled)


def test_external_nodes_stay_out_of_the_node_ratio(tmp_path: Path) -> None:
    """The other half of the same rule — an external node needs no label."""
    source = "import json\n\n\ndef dump(x: dict) -> str:\n    return json.dumps(x)\n"
    nodes = [{"id": "py:app.mod", "kind": "Module"}, {"id": "py:app.mod.dump", "kind": "Function"}]
    edges = [{"src": "py:app.mod", "dst": "py:app.mod.dump", "kind": "CONTAINS"}]
    report = score_case(_case(tmp_path, nodes=nodes, edges=edges, source=source))

    assert _score(report, "nodes", "Function").precision == 1.0
    assert not any("py:json" in u and "-" not in u for u in report.unlabelled)


def test_a_kind_with_nothing_expected_scores_none_not_one(tmp_path: Path) -> None:
    """Vacuous perfection is the easiest way to publish a misleading number."""
    report = score_case(_case(tmp_path, nodes=NODES, edges=EDGES))
    with pytest.raises(StopIteration):
        _score(report, "edges", "IMPLEMENTS")

    edges = [e for e in EDGES if e["kind"] != "CALLS"]
    stripped = score_case(_case(tmp_path / "b", edges=edges))
    assert _score(stripped, "edges", "CALLS").recall is None


def test_annotating_a_gap_does_not_change_the_score(tmp_path: Path) -> None:
    """`known_gaps` explains a miss; it must never suppress one."""
    edges = [*EDGES, {"src": "py:app.mod.helper", "dst": "py:app.mod.caller", "kind": "CALLS"}]
    plain = score_case(_case(tmp_path / "a", edges=edges))
    annotated = score_case(
        _case(
            tmp_path / "b",
            edges=edges,
            known_gaps=[
                {
                    "edge": {"src": "py:app.mod.helper", "dst": "py:app.mod.caller", "kind": "CALLS"},
                    "why": "understood, still a miss",
                }
            ],
        )
    )
    assert _score(plain, "edges", "CALLS").recall == _score(annotated, "edges", "CALLS").recall
    assert annotated.known_gaps == 1


def test_known_gap_naming_an_unlabelled_edge_is_an_error(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        known_gaps=[{"edge": {"src": "py:a", "dst": "py:b", "kind": "CALLS"}, "why": "not in edges"}],
    )
    with pytest.raises(CorpusError, match="known_gaps names an edge absent"):
        score_case(case)


def test_false_positive_that_is_also_labelled_true_is_an_error(tmp_path: Path) -> None:
    """A fact cannot be both true and invented."""
    case = _case(tmp_path, false_positives=[{"edge": EDGES[2], "why": "contradicts 'edges'"}])
    with pytest.raises(CorpusError, match="also in 'edges'"):
        score_case(case)


def test_an_unknown_kind_is_an_error(tmp_path: Path) -> None:
    case = _case(tmp_path, nodes=[{"id": "py:app.mod", "kind": "Widget"}])
    with pytest.raises(CorpusError, match="not a NodeKind"):
        score_case(case)


def test_a_missing_or_unparseable_case_file_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(CorpusError, match="no expected.json"):
        score_case(tmp_path / "empty")

    case = _case(tmp_path)
    (case / "expected.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CorpusError, match="invalid JSON"):
        score_case(case)


def test_a_root_that_is_not_a_directory_is_an_error(tmp_path: Path) -> None:
    case = _case(tmp_path)
    spec = json.loads((case / "expected.json").read_text())
    spec["root"] = "nope"
    (case / "expected.json").write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(CorpusError, match="is not a directory"):
        score_case(case)


def test_provenance_is_checked_only_where_a_label_opts_in(tmp_path: Path) -> None:
    """Provenance is out of the match key, so drift is reported, never scored."""
    bare = score_case(_case(tmp_path / "a"))
    assert bare.provenance_checked == 0
    assert bare.provenance_drift == ()

    nodes = [{**NODES[1], "at": "app/mod.py:999"}, NODES[0], NODES[2]]
    drifted = score_case(_case(tmp_path / "b", nodes=nodes))
    assert drifted.provenance_checked == 1
    assert any("labelled app/mod.py:999" in d for d in drifted.provenance_drift)
    assert _score(drifted, "nodes", "Function").precision == 1.0, "drift must not touch the score"


def test_corpus_walks_every_case_and_filters_by_language(tmp_path: Path) -> None:
    _case(tmp_path / "a", language="python")
    _case(tmp_path / "b", language="python")

    assert len(score_corpus(tmp_path).cases) == 2
    only = score_corpus(tmp_path, language="python")
    assert [c.language for c in only.cases] == ["python", "python"]
    assert score_corpus(tmp_path, language="nothing-here").cases == ()


def test_a_case_whose_front_end_is_missing_is_skipped_not_scored_zero(tmp_path: Path) -> None:
    """The bug that failed CI: an absent optional extra is not a regression.

    A front-end whose extra is not installed emits nothing, so its cases would score 0.00 on
    every kind — indistinguishable from the graph collapsing. CI has no `typescript` extra, so
    a baseline generated on a machine that does would fail every build on a machine that does
    not, for a reason nobody changed.
    """
    _case(tmp_path, language="typescript-but-not-installed")
    report = score_corpus(tmp_path)

    assert report.cases == (), "an unavailable front-end must not be scored"
    # The line names what was missing, because "skipped" alone does not tell a reader on a
    # bare machine which extra to install.
    assert report.skipped == ("typescript-but-not-installed/c1 (needs typescript-but-not-installed)",)
    assert report.skipped_languages == ("typescript-but-not-installed",)


def test_the_gate_ignores_a_language_the_current_run_could_not_measure(tmp_path: Path) -> None:
    """The other half: a baseline richer than the current environment is not a regression."""
    from orchestrator.pkg.accuracy import compare_scoreboard

    baseline: dict[str, Any] = {
        "metrics": {
            "corpus": {
                "languages": {
                    "typescript": {"edges": {"CALLS": {"expected": 4, "emitted": 4, "matched": 4}}}
                },
                "skipped_languages": [],
            }
        }
    }
    current: dict[str, Any] = {"metrics": {"corpus": {"languages": {}, "skipped_languages": ["typescript"]}}}
    assert compare_scoreboard(baseline, current) == []

    # …but a language that vanished for any *other* reason is still a regression.
    current_no_skip: dict[str, Any] = {"metrics": {"corpus": {"languages": {}, "skipped_languages": []}}}
    assert compare_scoreboard(baseline, current_no_skip) != []


def test_totals_sum_across_cases(tmp_path: Path) -> None:
    _case(tmp_path / "one")
    _case(tmp_path / "two")
    totals = score_corpus(tmp_path).totals()
    calls = next(s for s in totals["python"]["edges"] if s.kind == "CALLS")
    assert calls.expected == 2, "one labelled CALLS edge per case"
    assert calls.recall == 1.0


def test_an_empty_corpus_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="no expected.json found"):
        score_corpus(tmp_path)


# ---- the parity oracle (phase 3) -----------------------------------------


def _repo_with_routes(root: Path, body: str) -> Path:
    pkg = root / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "api.py").write_text(body, encoding="utf-8")
    return root


def test_parity_reports_shortfall_when_the_graph_misses_a_route(tmp_path: Path) -> None:
    from orchestrator.pkg.accuracy import score_parity

    _repo_with_routes(tmp_path, 'import x\n\n\n@router.get(f"/a/{x}")\ndef a():\n    return []\n')
    report = score_parity(tmp_path)

    assert report.declared == 1
    assert report.in_graph == 0
    assert report.shortfall == 1
    assert [c.file for c in report.short_files] == ["app/api.py"]


def test_parity_separates_surplus_from_shortfall(tmp_path: Path) -> None:
    """Never averaged into one ratio — this repo scores 68 declared against 70 emitted, and
    a combined 1.03 would read as a recall figure while hiding both phenomena."""
    from orchestrator.pkg.accuracy import score_parity

    _repo_with_routes(tmp_path, '@router.get("/ok")\ndef ok():\n    return []\n')
    report = score_parity(tmp_path)

    assert report.shortfall == 0
    assert report.surplus == 0
    assert report.declared == report.in_graph == 1


def test_parity_needs_no_corpus_and_no_tests(tmp_path: Path) -> None:
    """The third oracle's whole reason to exist: source only."""
    from orchestrator.pkg.accuracy import score_parity

    _repo_with_routes(tmp_path, "def plain() -> int:\n    return 1\n")
    report = score_parity(tmp_path)
    assert report.counts == ()
    assert report.shortfall == 0


def test_parity_on_a_missing_repo_is_an_error() -> None:
    from orchestrator.pkg.accuracy import score_parity

    with pytest.raises(CorpusError, match="not a directory"):
        score_parity("/nope/does/not/exist")


# ---- the invention gate: zero per language, not zero-versus-baseline -------


def _board(languages: dict[str, Any]) -> dict[str, Any]:
    return {"metrics": {"invention": {"gated": "strict", "languages": languages}}}


def test_a_fabricated_edge_fails_the_gate() -> None:
    """Proved by making it fail, not by watching it pass.

    A gate nobody has seen fail is a gate nobody knows is wired up — this is the check that
    the four front-end fixes are actually held in place by something.
    """
    from orchestrator.pkg.accuracy import compare_scoreboard

    clean = _board({"cpp": {"status": "measured", "invented": 0}})
    assert compare_scoreboard(clean, clean) == []

    broken = _board({"cpp": {"status": "measured", "invented": 3}})
    regressions = compare_scoreboard(clean, broken)
    assert [r.metric for r in regressions] == ["invention"]
    assert "cpp" in regressions[0].detail


def test_the_gate_is_absolute_not_relative_to_the_baseline() -> None:
    """A non-zero baseline must not license a non-zero current.

    Gating against a stored number is how a defect count becomes a metric everyone agrees to
    live with. There is no repository where a fabricated edge is acceptable.
    """
    from orchestrator.pkg.accuracy import compare_scoreboard

    dirty_baseline = _board({"go": {"status": "measured", "invented": 5}})
    assert compare_scoreboard(dirty_baseline, dirty_baseline) != []


def test_an_unmeasured_language_is_not_gated() -> None:
    """Its 0 means 'not examined'. Gating on it would report health nobody checked."""
    from orchestrator.pkg.accuracy import compare_scoreboard

    board = _board(
        {
            "java": {"status": "not-applicable", "invented": 0},
            "rust": {"status": "unwalked", "invented": 0},
        }
    )
    assert compare_scoreboard(board, board) == []


# ---- documentation drift, and why the gate is on the rate (WI-2 phase 3) ----


def _drift_repo(tmp_path: Path, *, doc: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def real_symbol():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").write_text(doc, encoding="utf-8")
    return repo


def test_drift_counts_claims_the_graph_cannot_support(tmp_path: Path) -> None:
    from orchestrator.pkg.accuracy import score_drift

    repo = _drift_repo(tmp_path, doc="# Guide\n\nCall `absent_symbol` after `real_symbol`.\n")
    report = score_drift(repo)
    assert report.count == 1 and report.docs >= 1 and report.measured
    # The denominator is every claim, so the bound one counts too.
    assert report.mentions >= 2 and report.rate is not None and report.rate < 1


def test_no_documentation_is_not_a_clean_result(tmp_path: Path) -> None:
    """Zero drift on zero docs must be legible as 'nothing measured' (§9)."""
    from orchestrator.pkg.accuracy import score_drift

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    report = score_drift(repo)
    assert report.count == 0 and report.docs == 0
    assert not report.measured


def test_drift_never_fails_a_build() -> None:
    """The gate shipped 2026-08-31 and was withdrawn the same day, one PR later.

    It failed a documentation change — the work it existed to protect — and fixing its
    denominator did not rescue it, because about a tenth of the population cannot bind by
    construction. Recorded and trended instead; see GATES for what would make it gate material.
    """
    from orchestrator.pkg.accuracy import GATES, compare_scoreboard

    assert GATES["drift"] is False
    much_worse = {"metrics": {"drift": {"count": 500, "mentions": 1000, "docs": 100}}}
    was = {"metrics": {"drift": {"count": 10, "mentions": 1000, "docs": 100}}}
    assert [r for r in compare_scoreboard(was, much_worse) if r.metric == "drift"] == []


def test_the_denominator_is_claims_made_not_sections() -> None:
    """A section count does not move when prose inside a section is edited.

    That was the first defect: any added claim raised the figure with nothing able to dilute
    it, and the result was not bounded by 1, so it was not a rate.
    """
    from orchestrator.pkg.accuracy import DriftReport

    report = DriftReport(count=10, docs=100, mentions=1000)
    assert report.rate == Fraction(1, 100)

    # The old shape: more drift claims than sections, a "rate" above 1.
    assert DriftReport(count=893, docs=1532, mentions=0).rate is None


def test_an_edited_section_dilutes_when_it_adds_bound_claims() -> None:
    """What the corrected denominator buys, even though it is no longer gated on."""
    from orchestrator.pkg.accuracy import DriftReport

    before = DriftReport(count=10, docs=100, mentions=1000)
    after = DriftReport(count=10, docs=100, mentions=1200)  # same section, 200 more claims
    assert after.rate is not None and before.rate is not None and after.rate < before.rate


def test_the_gate_number_is_the_number_state_reports(tmp_path: Path) -> None:
    """A reader who sees one number in the report and another in the gate can act on neither."""
    from orchestrator.knowledge.current_state import load_current_state
    from orchestrator.pkg.accuracy import score_drift

    repo = _drift_repo(tmp_path, doc="# Guide\n\nCall `absent_symbol` and `also_absent`.\n")
    state, _ = load_current_state(repo)
    assert score_drift(repo).count == state.doc_drift_total
