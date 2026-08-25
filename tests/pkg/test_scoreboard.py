"""The accuracy scoreboard and its CI gate.

The design decision under test is *which* metrics may be gated. Every number here is
deterministic run-to-run; what differs is what each is measured **against**. Corpus scores
come from committed fixtures and cannot move when someone writes code. Parity is measured
against the repository and ratchets. Invention is the odd one: its *rate* moves on ordinary
commits — adding `def handler(cb): return cb()` took it from 496 to 497 — but its *count* has
a correct value, zero, so it is gated absolutely rather than against a baseline. Gating the
rate would fail blameless PRs, which is how a gate gets switched off; gating the count cannot,
because there is no repository where a fabricated edge is acceptable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.pkg.accuracy import (
    BASELINE,
    GATES,
    build_scoreboard,
    compare_scoreboard,
    scoreboard_improvements,
)

REPO = Path(__file__).resolve().parents[2]


def _board(
    *, matched: int = 8, emitted: int = 10, expected: int = 10, shortfall: int = 5, invented: int = 0
) -> dict[str, Any]:
    """A healthy board. `invented` defaults to 0 because a fabricated edge now fails the gate,
    so a fixture carrying one would make every unrelated test in this file fail for the wrong
    reason."""
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
            "invention": {
                "gated": "strict",
                "count": invented,
                "total_calls": 15000,
                "languages": {"python": {"status": "measured", "invented": invented, "total_calls": 15000}},
            },
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


def test_invention_is_gated_at_zero_per_language() -> None:
    """The decision this phase turns on, and the one it reversed.

    Ungated until 2026-08-24 on the grounds that the population moves. That was right about
    the *rate* and wrong about the *count*: a `CALLS` edge to a name the caller bound itself
    is a defect at any commit, so there is nothing for a ratchet to ratchet.
    """
    assert GATES["invention"] == "strict"
    assert compare_scoreboard(_board(invented=0), _board(invented=0)) == []
    assert [r.metric for r in compare_scoreboard(_board(invented=0), _board(invented=1))] == ["invention"]


def test_the_invention_gate_ignores_the_baseline_entirely() -> None:
    """Comparing to a stored number is how a defect count becomes a metric people live with."""
    assert compare_scoreboard(_board(invented=496), _board(invented=496)) != []


def test_the_invention_rate_may_still_move_freely() -> None:
    """The original objection, preserved: only the count is gated, never the rate."""
    quiet = _board(invented=0)
    busy = _board(invented=0)
    busy["metrics"]["invention"]["total_calls"] = 90000
    busy["metrics"]["invention"]["languages"]["python"]["total_calls"] = 90000
    assert compare_scoreboard(quiet, busy) == []


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
    committed = json.loads((REPO / "src/orchestrator/pkg/scoreboard.json").read_text(encoding="utf-8"))
    assert compare_scoreboard(committed, build_scoreboard(REPO / "corpus", REPO)) == []


def test_undefined_scores_are_not_treated_as_drops() -> None:
    """None is not 0.0, and it is not a drop from None."""
    empty = _board(matched=0, emitted=0, expected=0)
    assert compare_scoreboard(empty, empty) == []
    assert scoreboard_improvements(empty, empty) == []


# ---- the baseline must travel with the wheel (phase 6) --------------------


def test_the_baseline_lives_inside_the_package() -> None:
    """It is quoted by the build document, which runs on installed Spines too.

    `pyproject.toml` builds `src/orchestrator` only, so a copy at the repo root would be
    invisible to a pip install — and the build document would fall back to a qualitative
    caveat in exactly the deployment where the reader cannot go and measure it themselves.
    """
    import orchestrator

    package_root = Path(orchestrator.__file__).resolve().parent
    assert BASELINE.is_file()
    assert BASELINE.is_relative_to(package_root), f"{BASELINE} would not ship in the wheel"
