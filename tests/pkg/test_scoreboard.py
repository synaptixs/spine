"""The accuracy scoreboard and its CI gate.

The design decision under test is *which* metrics may be gated. Every number here is
deterministic run-to-run; what differs is what each is measured **against**. Corpus scores
come from committed fixtures and cannot move when someone writes code. Invention is measured
against the repository itself and moves on ordinary commits — adding `def handler(cb):
return cb()` took it from 496 to 497. Gating that fails blameless PRs, which is how a gate
gets switched off.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.pkg.accuracy import (
    GATES,
    build_scoreboard,
    compare_scoreboard,
    scoreboard_improvements,
)

REPO = Path(__file__).resolve().parents[2]


def _board(
    *, matched: int = 8, emitted: int = 10, expected: int = 10, shortfall: int = 5, invented: int = 496
) -> dict[str, Any]:
    return {
        "version": 1,
        "metrics": {
            "corpus": {
                "gated": "strict",
                "languages": {
                    "python": {
                        "edges": {"CALLS": {"expected": expected, "emitted": emitted, "matched": matched}}
                    }
                },
            },
            "parity": {"gated": "ratchet", "shortfall": shortfall, "surplus": 7},
            "invention": {"gated": False, "count": invented, "total_calls": 15000},
        },
    }


# ---- what is gated, and what is not (AC 3, 5, 6, 8) -----------------------


def test_a_corpus_precision_drop_is_a_regression() -> None:
    before, after = _board(matched=8, emitted=10), _board(matched=6, emitted=10)
    (found,) = [r for r in compare_scoreboard(before, after) if "precision" in r.detail]
    assert found.metric == "corpus"
    assert "python/edges/CALLS precision" in found.detail
    assert (found.was, found.now) == ("0.8000", "0.6000")


def test_a_corpus_recall_drop_is_a_regression() -> None:
    before, after = _board(matched=8, expected=10), _board(matched=6, expected=10)
    assert any("recall" in r.detail for r in compare_scoreboard(before, after))


def test_a_corpus_improvement_is_not_a_regression() -> None:
    before, after = _board(matched=6), _board(matched=8)
    assert compare_scoreboard(before, after) == []
    assert any("precision" in i for i in scoreboard_improvements(before, after))


def test_a_kind_disappearing_is_a_regression_not_silence() -> None:
    """An absent population is not 'unchanged' and not zero — it used to exist."""
    after = _board()
    after["metrics"]["corpus"]["languages"]["python"]["edges"] = {}
    (found,) = compare_scoreboard(_board(), after)
    assert "disappeared" in found.detail


def test_parity_shortfall_ratchets_one_way() -> None:
    """Rising means the graph fell behind the source. Falling is someone fixing it."""
    assert any(r.metric == "parity" for r in compare_scoreboard(_board(shortfall=5), _board(shortfall=6)))
    assert compare_scoreboard(_board(shortfall=5), _board(shortfall=4)) == []
    assert any("parity" in i for i in scoreboard_improvements(_board(shortfall=5), _board(shortfall=4)))


def test_invention_never_fails_the_build() -> None:
    """The decision this phase turns on: measured against a moving population, so ungated.

    One ordinary new file with a callback parameter moved it 496 -> 497. Gating it fails a
    PR that adds a perfectly normal function.
    """
    assert compare_scoreboard(_board(invented=496), _board(invented=5000)) == []
    assert GATES["invention"] is False


def test_every_metric_records_whether_it_is_gated() -> None:
    board = _board()
    for name, metric in board["metrics"].items():
        assert "gated" in metric, f"{name} does not say whether it is gated"
    assert GATES["corpus"] == "strict"
    assert GATES["parity"] == "ratchet"
    assert GATES["runtime"] is False


def test_an_identical_board_has_no_regressions() -> None:
    board = _board()
    assert compare_scoreboard(board, board) == []
    assert scoreboard_improvements(board, board) == []


# ---- the artefact (AC 1, 7) ------------------------------------------------


def test_the_scoreboard_is_deterministic() -> None:
    """Same tree, byte-identical file — otherwise every commit shows a spurious diff."""
    first = build_scoreboard(REPO / "corpus", REPO)
    second = build_scoreboard(REPO / "corpus", REPO)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_runtime_oracle_is_absent_unless_asked_for() -> None:
    """A command CI runs by default must not execute the repository's test suite."""
    assert "runtime" not in build_scoreboard(REPO / "corpus", REPO)["metrics"]


def test_the_committed_baseline_matches_the_tree() -> None:
    """The gate's own contract: --check must pass on the tree that produced the baseline.

    Today's 496 invented edges and 5 missing endpoints are baselined IN. The gate stops
    things getting worse; it does not demand they already be perfect.
    """
    committed = json.loads((REPO / "corpus/scoreboard.json").read_text(encoding="utf-8"))
    assert compare_scoreboard(committed, build_scoreboard(REPO / "corpus", REPO)) == []


def test_undefined_scores_are_not_treated_as_drops() -> None:
    """None is not 0.0, and it is not a drop from None."""
    empty = _board(matched=0, emitted=0, expected=0)
    assert compare_scoreboard(empty, empty) == []
    assert scoreboard_improvements(empty, empty) == []
