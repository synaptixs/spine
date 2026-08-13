"""`pkg accuracy` — precision and recall against hand-labelled ground truth.

The rules under test are the ones that were *decided* rather than obvious: what an external
node costs, what an empty expected set scores, and the invariant that annotating a fact never
moves the number it annotates.
"""

from __future__ import annotations

import json
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
    _case(tmp_path, language="python")
    _case(tmp_path, language="other")

    assert len(score_corpus(tmp_path).cases) == 2
    only = score_corpus(tmp_path, language="python")
    assert [c.language for c in only.cases] == ["python"]


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
