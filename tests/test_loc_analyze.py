"""`analyze.py`: the research-path table, and the run header's environment, including files written before it.

SSPN-430's research figures were tallied by a script outside the repository; this table is the tracked
version. A results file written before the harness recorded its environment must still read (BENCHMARK.md
cites one), with the missing fields printed as "not recorded" rather than guessed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "swebench_localization" / "analyze.py"
_spec = importlib.util.spec_from_file_location("loc_analyze_under_test", _SCRIPT)
assert _spec is not None and _spec.loader is not None
analyze: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyze)

_OLD_HEADER = {
    "header": True,
    "mode": "run",
    "spine_sha": "0bb018a5a77bafb39b6fedc6d73814f42cf0b052",
    "hybrid_cell": {"recipe": "B", "name_weight": "1"},
    "dataset_rev": "c104f840cc67f8b6eec6f759ebc8b2693d585d4a",
    "dataset_file_sha256": None,
    "ids_file_sha256": None,
    "harness_sha256": "4" * 64,
    "started_utc": "2026-11-27T00:25:23Z",
    "total": 4,
}
_NEW_HEADER: dict[str, Any] = {
    **_OLD_HEADER,
    "environment": {
        "embed_model": "BAAI/bge-small-en-v1.5@5c38ec7c",
        "env": {"SPINE_HYBRID_AUTORUN": "0"},
        "front_ends": ["tree_sitter", "tree_sitter_c"],
        "spine_version": "3.72.0",
    },
    "env_sha256": "0123456789ab",
    "research_specs_sha256": "f" * 64,
}


def _row(n: int, **research: Any) -> dict[str, Any]:
    return {
        "instance_id": f"proj__proj-{n}",
        "repo": "proj/proj",
        "status": "scored",
        "n_gold": 2,  # section 5 divides by the multi-file count; a real run always has some
        "quotes_gold": False,
        "rank_a": 1,
        "rank_b": 1,
        "rank_bm25": 2,
        "all_gold_in_10_b": True,
        "chance": {"1": 0.1, "3": 0.3, "5": 0.5, "10": 1.0},
        **research,
    }


def _research_rows() -> list[dict[str, Any]]:
    four = "name, content, meaning (BAAI/bge-small-en-v1.5@5c38ec7c), graph"
    return [
        _row(1, has_research_spec=True, files_research=["a.py"], rank_research=1, research_retrieval=four),
        _row(
            2,
            has_research_spec=True,
            files_research=["b.py", "a.py"],
            rank_research=2,
            research_retrieval=four,
        ),
        _row(3, has_research_spec=True, files_research=["b.py"], rank_research=None, research_retrieval=""),
        _row(4, has_research_spec=True, research_error="RuntimeError: research fell over"),
        _row(5, has_research_spec=False, files_research=["a.py"], rank_research=1, research_retrieval=four),
    ]


def _padded(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two rows without a research arm, one quoting its gold file and one not, from the header's environment.

    Sections 4 and 5 divide by the size of those subsets, which a real run never leaves empty.
    """
    fill = {"env_sha256": _NEW_HEADER["env_sha256"]}
    return [*rows, _row(90, quotes_gold=True, **fill), _row(91, **fill)]


def _tables(header: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    text: str = analyze.tables(header, _padded(rows))
    return text


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


# --- the research table ------------------------------------------------------------------------------------


def test_the_research_table_counts_first_and_anywhere_by_what_research_was_handed() -> None:
    text = _tables(_NEW_HEADER, _research_rows())
    lines = _lines(text)
    assert "## 7. Research path (autorun's `build_evidence`)" in lines
    assert "| the spec intake wrote | 3 | 1 | 1 (0.333) | 2 (0.667) |" in lines
    assert "| the ticket text | 1 | 0 | 1 (1.000) | 1 (1.000) |" in lines
    assert "| all | 4 | 1 | 2 (0.500) | 3 (0.750) |" in lines


def test_a_research_error_is_counted_apart_and_never_as_a_miss() -> None:
    text = _tables(_NEW_HEADER, _research_rows())
    assert (
        "Research errors (not scored, not misses): 1 — proj__proj-4: RuntimeError: research fell over" in text
    )


def test_the_research_table_names_the_legs_that_ranked_each_row() -> None:
    text = _tables(_NEW_HEADER, _research_rows())
    assert (
        "Legs named: name, content, meaning (BAAI/bge-small-en-v1.5@5c38ec7c), graph on 3; "
        "none (lexical ranking) on 1" in text
    )


def test_a_file_without_a_research_arm_has_no_research_table() -> None:
    assert "## 7." not in _tables(_NEW_HEADER, [_row(1), _row(2)])


def test_a_row_without_the_spec_flag_counts_as_ticket_text() -> None:
    """Files from before ``has_research_spec`` existed: research was handed the ticket then."""
    rows = [_row(1, files_research=["a.py"], rank_research=1, research_retrieval="")]
    assert "| the ticket text | 1 | 0 | 1 (1.000) | 1 (1.000) |" in _lines(_tables(_NEW_HEADER, rows))


def test_a_group_with_only_research_errors_shows_them_without_a_rate() -> None:
    rows = [_row(1, has_research_spec=False, research_error="ModelError: no files")]
    lines = _lines(_tables(_NEW_HEADER, rows))
    assert "| the ticket text | 0 | 1 | 0 | 0 |" in lines
    assert not any(line.startswith("| the spec intake wrote") for line in lines)


# --- the header's environment ------------------------------------------------------------------------------


def test_the_header_line_states_the_recorded_environment() -> None:
    text = _tables(_NEW_HEADER, [_row(1, env_sha256="0123456789ab")])
    assert (
        "Environment 0123456789ab: Spine 3.72.0; embedding model BAAI/bge-small-en-v1.5@5c38ec7c; "
        "front-ends tree_sitter, tree_sitter_c; switches SPINE_HYBRID_AUTORUN=0; research specs ffffffffffff"
        in text
    )
    assert "Rows whose environment digest differs from the header's: 0" in text


def test_a_header_from_before_the_environment_was_recorded_reads_as_not_recorded() -> None:
    text = _tables(_OLD_HEADER, [_row(1)])
    assert (
        "Environment not recorded: Spine not recorded; embedding model not recorded; "
        "front-ends not recorded; switches not recorded; research specs not recorded" in text
    )
    assert "Rows whose environment digest differs from the header's: not recorded" in text


def test_no_switches_and_no_specs_file_are_said_as_such() -> None:
    header = {
        **_NEW_HEADER,
        "environment": {**_NEW_HEADER["environment"], "env": {}},
        "research_specs_sha256": None,
    }
    text = _tables(header, [_row(1, env_sha256="0123456789ab")])
    assert "switches none set; research specs none given" in text


def test_rows_from_another_environment_are_counted() -> None:
    rows = [_row(1, env_sha256="0123456789ab"), _row(2, env_sha256="ba9876543210"), _row(3)]
    text = _tables(_NEW_HEADER, rows)
    assert "Rows whose environment digest differs from the header's: 1 (not recorded on 1)" in text


def test_a_results_file_from_before_this_change_still_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The format of SSPN-430's acceptance files: no environment, no research errors, no research time."""
    rows = [_row(1, has_research_spec=True, files_research=["a.py"], rank_research=1, research_retrieval="")]
    results = tmp_path / "old.jsonl"
    results.write_text("".join(json.dumps(line) + "\n" for line in [_OLD_HEADER, *_padded(rows)]))
    monkeypatch.setattr(sys, "argv", ["analyze.py", str(results), "--allow-partial"])
    analyze.main()
    out = capsys.readouterr().out
    assert "Environment not recorded" in out
    assert "| the spec intake wrote | 1 | 0 | 1 (1.000) | 1 (1.000) |" in _lines(out)
    assert not any(line.startswith("| the ticket text") for line in _lines(out))
