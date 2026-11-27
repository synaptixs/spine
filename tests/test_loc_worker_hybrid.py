"""`loc_worker._hybrid_arms`: a grid cell's ``model`` (pre-registration §4) runs the meaning leg with it."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.extractor import RepoCodeExtractor

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "swebench_localization" / "loc_worker.py"
_spec = importlib.util.spec_from_file_location("loc_worker", _SCRIPT)
assert _spec is not None and _spec.loader is not None
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)


def _files(inv: Any) -> list[str]:
    return list(dict.fromkeys(landing.where.rsplit(":", 1)[0] for landing in inv.landing))


def _arms(tmp_path: Path, cell: dict[str, Any]) -> dict[str, Any]:
    (tmp_path / "ledger.py").write_text(
        'def append_row(rows, row):\n    """Append one row to the accounting ledger."""\n'
        "    rows.append(row)\n"
    )
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    q = {"first": "Ledger rows go missing", "rest": "Rows in the accounting ledger are lost.", "hybrid": cell}
    out: dict[str, Any] = worker._hybrid_arms(q, store, tmp_path, _files)
    return out


def test_a_cell_without_a_model_runs_three_legs(tmp_path: Path) -> None:
    out = _arms(tmp_path, {"recipe": "B"})
    assert out["legs_used"] == ["name", "content", "graph"] and out["model"] is None


def test_a_cell_with_a_model_runs_the_meaning_leg_with_it(tmp_path: Path) -> None:
    out = _arms(tmp_path, {"recipe": "A", "name_weight": 2.0, "model": "stand-in"})
    assert out["legs_used"] == ["name", "content", "meaning", "graph"]
    assert out["model"] == "stand-in/hash-64"
    assert out["files_h"] == ["ledger.py"]


def test_an_unknown_model_stops_the_run_instead_of_scoring_three_legs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="potion-retrieval-32M"):
        _arms(tmp_path, {"model": "no-such-model"})


# --- the research-path arm (SSPN-430) ---------------------------------------------------------------------
#
# Every other arm calls ``build_investigation`` directly, and the hybrid arms hand it a retriever, so none of
# them can show whether autorun's research ranks with hybrid search: they read the same before and after that
# wiring. This arm runs the research path itself, at the shipped default.


def _research(tmp_path: Path, q: dict[str, Any]) -> dict[str, Any]:
    (tmp_path / "ledger.py").write_text(
        'def append_row(rows, row):\n    """Append one row to the accounting ledger."""\n'
        "    rows.append(row)\n"
    )
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    out: dict[str, Any] = worker._research_arm(q, store, tmp_path)
    return out


_TICKET = {"first": "Ledger rows go missing", "rest": "Rows in the accounting ledger are lost."}


@pytest.mark.hybrid_default
def test_the_research_arm_ranks_as_the_run_does_and_says_which_legs_ran(tmp_path: Path) -> None:
    out = _research(tmp_path, dict(_TICKET))
    assert out["files_research"] == ["ledger.py"]
    assert out["research_retrieval"] == "name, content, graph"


def test_with_hybrid_off_the_research_arm_is_the_lexical_ranking(tmp_path: Path) -> None:
    """Unmarked, so the suite holds hybrid off: the module's name still lands it, and no legs are named."""
    out = _research(tmp_path, dict(_TICKET))
    assert out["files_research"] == ["ledger.py"]
    assert out["research_retrieval"] == ""


def test_the_research_arm_searches_with_the_spec_when_one_is_given(tmp_path: Path) -> None:
    """Research is handed the spec intake wrote, not the ticket, so the arm can be handed it too."""
    spec = {"title": "append_row drops a row", "problem": "append_row loses the row it was given"}
    out = _research(tmp_path, {**_TICKET, "research": spec})
    assert out["files_research"] == ["ledger.py"]
