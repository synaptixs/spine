"""`loc_worker._hybrid_arms`: a grid cell's ``model`` (pre-registration §4) runs the meaning leg with it."""

from __future__ import annotations

import importlib.util
import json
import sys
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
    """Research is handed the spec intake wrote, not the ticket, so the arm can be handed it too.

    The spec and the ticket land on different files here, so an arm that ignored the spec would land the
    ledger and fail this.
    """
    (tmp_path / "invoice.py").write_text(
        'def render_total(lines):\n    """Sum the invoice lines."""\n    return sum(lines)\n'
    )
    spec = {"title": "render_total gives the wrong invoice total", "problem": "render_total sums the invoice"}
    assert _research(tmp_path, dict(_TICKET))["files_research"] == ["ledger.py"]
    assert _research(tmp_path, {**_TICKET, "research": spec})["files_research"] == ["invoice.py"]


def test_the_worker_reports_the_research_arm_beside_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The arm is measured only if the worker's one line of output carries it."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "ledger.py").write_text(
        'def append_row(rows, row):\n    """Append one row to the accounting ledger."""\n'
        "    rows.append(row)\n"
    )
    q = tmp_path / "q.json"
    q.write_text(json.dumps({**_TICKET, "hybrid": {}}))
    monkeypatch.setattr(sys, "argv", ["loc_worker.py", str(tree), str(tmp_path / "cache"), str(q)])
    assert worker.main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["files_research"] == ["ledger.py"]
    assert out["research_retrieval"] == ""


# --- a research arm that raises, its own timing, and the environment each line came from (SSPN-434) -------


def _run_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> dict[str, Any]:
    """Run ``worker.main`` on a one-file tree and return its one line of output."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "ledger.py").write_text(
        'def append_row(rows, row):\n    """Append one row to the accounting ledger."""\n'
        "    rows.append(row)\n"
    )
    q = tmp_path / "q.json"
    q.write_text(json.dumps({**_TICKET, "hybrid": {}}))
    monkeypatch.setattr(sys, "argv", ["loc_worker.py", str(tree), str(tmp_path / "cache"), str(q)])
    assert worker.main() == 0
    line: dict[str, Any] = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    return line


def test_a_research_arm_that_raises_keeps_every_other_arm_and_names_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One exception in research must not lose the instance: the baseline arms are still scored."""
    import orchestrator.sdlc.evidence as evidence

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("research fell over")

    monkeypatch.setattr(evidence, "build_evidence", boom)
    out = _run_worker(tmp_path, monkeypatch, capsys)
    assert out["research_error"] == "RuntimeError: research fell over"
    assert "files_research" not in out and "research_retrieval" not in out
    for arm in ("files_a", "files_b", "files_v", "files_bm25", "files_h"):
        assert out[arm] == ["ledger.py"], arm
    assert "research_s" in out


def test_a_research_arm_that_succeeds_records_no_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run_worker(tmp_path, monkeypatch, capsys)
    assert "research_error" not in out
    assert out["files_research"] == ["ledger.py"]


def test_research_is_timed_apart_from_the_other_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``investigate_s`` is the A, B, V and hybrid arms; ``research_s`` is the research arm alone."""
    import time

    def slow_research(q: dict[str, Any], store: Any, tree: Path) -> dict[str, Any]:
        time.sleep(1.5)
        return {"files_research": []}

    monkeypatch.setattr(worker, "_research_arm", slow_research)
    out = _run_worker(tmp_path, monkeypatch, capsys)
    assert out["research_s"] >= 1.5
    assert out["investigate_s"] < 1.5


_PROBE_KEYS = {"spine_version", "front_ends", "embed_model", "env"}


def _probe(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[str, dict[str, Any]]:
    monkeypatch.setattr(sys, "argv", ["loc_worker.py", "--probe"])
    assert worker.main() == 0
    text = capsys.readouterr().out.strip()
    probe: dict[str, Any] = json.loads(text)
    return text, probe


def test_the_probe_prints_the_documented_environment_with_sorted_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from orchestrator import __version__

    monkeypatch.setenv("SPINE_HYBRID_AUTORUN", "0")
    monkeypatch.setenv("SPINE_EMBED_MODEL", "none")
    monkeypatch.setenv("SPINE_MODEL_DIR", "/models")
    monkeypatch.setenv("SPINE_UNRELATED", "x")
    text, probe = _probe(monkeypatch, capsys)
    assert set(probe) == _PROBE_KEYS
    assert text == json.dumps(probe, sort_keys=True)
    assert probe["spine_version"] == __version__
    assert probe["embed_model"] == "none"
    assert probe["env"]["SPINE_HYBRID_AUTORUN"] == "0"
    assert probe["env"]["SPINE_EMBED_MODEL"] == "none"
    assert probe["env"]["SPINE_MODEL_DIR"] == "/models"
    assert "SPINE_UNRELATED" not in probe["env"] and "HOME" not in probe["env"]
    assert list(probe["env"]) == sorted(probe["env"])


def test_the_probe_lists_the_installed_front_ends_sorted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from orchestrator.pkg.persistence import _GRAMMAR_MODULES

    _, probe = _probe(monkeypatch, capsys)
    installed = sorted(m for m in _GRAMMAR_MODULES if importlib.util.find_spec(m) is not None)
    assert probe["front_ends"] == installed


def test_a_front_end_that_is_not_installed_is_left_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI's environment has every grammar installed, so hide one: the probe must ask, not list them all."""
    real = importlib.util.find_spec

    def find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        return None if name == "tree_sitter_go" else real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    assert "tree_sitter_go" not in _probe(monkeypatch, capsys)[1]["front_ends"]


def test_the_probe_names_the_resolved_model_or_why_it_cannot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SPINE_EMBED_MODEL", "stand-in")
    assert _probe(monkeypatch, capsys)[1]["embed_model"] == "stand-in/hash-64"
    monkeypatch.setenv("SPINE_EMBED_MODEL", "no-such-model")
    assert _probe(monkeypatch, capsys)[1]["embed_model"].startswith("error: ")


def test_two_probes_of_one_environment_are_identical(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _probe(monkeypatch, capsys)[0] == _probe(monkeypatch, capsys)[0]


def test_each_scored_line_carries_the_digest_of_its_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A row from another environment is visible by its digest, which the harness compares with its own."""
    monkeypatch.setenv("SPINE_EMBED_MODEL", "none")
    text, _ = _probe(monkeypatch, capsys)
    out = _run_worker(tmp_path, monkeypatch, capsys)
    assert out["env_sha256"] == worker.loc_lib.env_digest(json.loads(text))
    assert len(out["env_sha256"]) == 12 and int(out["env_sha256"], 16) >= 0


def test_the_digest_does_not_depend_on_key_order() -> None:
    a = {"env": {"A": "1", "B": "2"}, "spine_version": "1"}
    b = {"spine_version": "1", "env": {"B": "2", "A": "1"}}
    assert worker.loc_lib.env_digest(a) == worker.loc_lib.env_digest(b)


def test_the_digest_changes_with_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lexical = worker.loc_lib.env_digest(_probe(monkeypatch, capsys)[1])
    monkeypatch.setenv("SPINE_HYBRID_AUTORUN", "0")
    assert worker.loc_lib.env_digest(_probe(monkeypatch, capsys)[1]) != lexical


# --- the harness hands the arm its spec and scores it -----------------------------------------------------

_HARNESS = _SCRIPT.parent / "loc_harness.py"


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    """``loc_harness`` with the tree checkout and the worker process replaced, so no mirror or Spine runs.

    Also returns the queries the harness handed the worker, in order.
    """
    monkeypatch.syspath_prepend(str(_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("loc_harness_under_test", _HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def build_tree(repo: str, base: str, dest: Path, tmp: Path) -> tuple[bool, str]:
        dest.mkdir(parents=True)
        return True, ""

    sent: list[dict[str, Any]] = []

    def worker_once(tree: Path, cache: Path, qfile: Path) -> dict[str, Any]:
        sent.append(json.loads(qfile.read_text()))
        files = ["pkg/ledger.py"]
        return {
            "files_a": files, "files_b": files, "files_bm25": files, "files_research": ["pkg/invoice.py"],
            "research_retrieval": "name, content, graph", "n_py_files": 2, "nodes": 4,
            "extract_s": 0.0, "investigate_s": 0.0, "bm25_s": 0.0,
        }  # fmt: skip

    monkeypatch.setattr(module, "build_tree", build_tree)
    monkeypatch.setattr(module, "worker_once", worker_once)
    return module, sent


_ROW = {
    "instance_id": "proj__proj-1",
    "repo": "proj/proj",
    "base_commit": "0" * 40,
    "problem_statement": "Ledger rows go missing\nRows in the accounting ledger are lost.",
    "patch": "diff --git a/pkg/invoice.py b/pkg/invoice.py\n",
}


def test_the_harness_hands_the_worker_the_spec_research_was_given(harness: tuple[Any, list[Any]]) -> None:
    module, sent = harness
    module.RESEARCH_SPECS = {"proj__proj-1": {"title": "render_total is wrong", "problem": "the total"}}
    module.score_one(dict(_ROW), "run")
    assert sent[-1]["research"] == {"title": "render_total is wrong", "problem": "the total"}


def test_the_harness_scores_the_research_arm(harness: tuple[Any, list[Any]]) -> None:
    module, sent = harness
    row = module.score_one(dict(_ROW), "run")
    assert "research" not in sent[-1]
    assert row["files_research"] == ["pkg/invoice.py"] and row["rank_research"] == 1
    assert row["research_retrieval"] == "name, content, graph"
    assert row["has_research_spec"] is False
