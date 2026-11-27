"""`loc_harness`: the run header records the environment; a resume refuses a file from another run.

The harness writes the evidence behind BENCHMARK.md's localization figure. Before SSPN-434 a resume kept
every scored row of the old file and wrote a fresh header above them, so a resume after the checkout, the
grid cell or the installed extras changed mixed two environments under one header. The tree checkout, the
worker process, git and the dataset are replaced here, so no mirror, Spine worker or network is used.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_HARNESS = Path(__file__).resolve().parents[1] / "scripts" / "swebench_localization" / "loc_harness.py"

_PROBE = {
    "embed_model": "none",
    "env": {"SPINE_EMBED_MODEL": "none"},
    "front_ends": ["tree_sitter", "tree_sitter_c"],
    "spine_version": "9.9.9",
}
_HEAD = "a" * 40


def _row(n: int) -> dict[str, Any]:
    return {
        "instance_id": f"proj__proj-{n}",
        "repo": "proj/proj",
        "base_commit": "0" * 40,
        "problem_statement": "Ledger rows go missing\nRows in the accounting ledger are lost.",
        "patch": "diff --git a/pkg/ledger.py b/pkg/ledger.py\n",
    }


class Harness:
    """``loc_harness`` loaded by path, with its outside world replaced and every call recorded."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.syspath_prepend(str(_HARNESS.parent))
        spec = importlib.util.spec_from_file_location("loc_harness_resume_under_test", _HARNESS)
        assert spec is not None and spec.loader is not None
        self.module: Any = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.monkeypatch, self.tmp = monkeypatch, tmp_path
        self.out = tmp_path / "run.jsonl"
        self.probe: Any = SimpleNamespace(returncode=0, stdout=json.dumps(_PROBE, sort_keys=True), stderr="")
        self.sh_calls: list[list[str]] = []
        self.worker_calls: list[str] = []
        self.worker_extra: dict[str, Any] = {}

        def sh(args: list[str], env: Any = None, cwd: Any = None) -> Any:
            self.sh_calls.append(list(args))
            if "--probe" in args:
                return self.probe
            if "rev-parse" in args:
                return SimpleNamespace(returncode=0, stdout=_HEAD + "\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def build_tree(repo: str, base: str, dest: Path, tmp: Path) -> tuple[bool, str]:
            dest.mkdir(parents=True)
            return True, ""

        def worker_once(tree: Path, cache: Path, qfile: Path) -> dict[str, Any]:
            self.worker_calls.append(json.loads(qfile.read_text())["first"])
            files = ["pkg/ledger.py"]
            return {
                "files_a": files, "files_b": files, "files_bm25": files, "n_py_files": 2, "nodes": 4,
                "extract_s": 0.0, "investigate_s": 0.0, "bm25_s": 0.0, **self.worker_extra,
            }  # fmt: skip

        monkeypatch.setattr(self.module, "sh", sh)
        monkeypatch.setattr(self.module, "build_tree", build_tree)
        monkeypatch.setattr(self.module, "worker_once", worker_once)
        monkeypatch.setattr(self.module, "load_rows", lambda dataset_file=None: [_row(1), _row(2)])

    def main(self, *extra: str) -> None:
        argv = ["loc_harness.py", "--mode", "run", "--ids", "proj__proj-1,proj__proj-2"]
        argv += ["--out", str(self.out), "--spine", str(self.tmp), *extra]
        self.monkeypatch.setattr(sys, "argv", argv)
        self.module.main()

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.out.read_text().splitlines()]

    def rewrite_header(self, **changes: Any) -> None:
        """Change (or, with ``None`` as the value of a ``drop_`` key, remove) fields of the file's header."""
        lines = self.lines()
        for key, value in changes.items():
            if key.startswith("drop_"):
                lines[0].pop(key[len("drop_") :], None)
            else:
                lines[0][key] = value
        self.out.write_text("".join(json.dumps(line) + "\n" for line in lines))


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Harness:
    return Harness(monkeypatch, tmp_path)


# --- the header records the environment ------------------------------------------------------------------


def test_the_header_records_the_environment_its_digest_and_the_specs_file(harness: Harness) -> None:
    specs = harness.tmp / "specs.json"
    specs.write_text(json.dumps({"proj__proj-1": {"title": "t", "problem": "p"}}))
    harness.main("--research-specs", str(specs))
    header = harness.lines()[0]
    assert header["environment"] == _PROBE
    assert header["env_sha256"] == harness.module.loc_lib.env_digest(_PROBE)
    assert header["research_specs_sha256"] == hashlib.sha256(specs.read_bytes()).hexdigest()


def test_without_a_specs_file_the_header_says_so(harness: Harness) -> None:
    harness.main()
    assert harness.lines()[0]["research_specs_sha256"] is None


def test_the_probe_runs_the_worker_in_spines_own_environment(harness: Harness) -> None:
    harness.main()
    probes = [call for call in harness.sh_calls if "--probe" in call]
    assert probes == [
        [str(harness.tmp / ".venv/bin/python"), str(_HARNESS.parent / "loc_worker.py"), "--probe"]
    ]


def test_a_probe_that_fails_refuses_to_start(harness: Harness) -> None:
    """A results file is evidence only with its header: a run that cannot say its environment never starts."""
    harness.probe = SimpleNamespace(
        returncode=1, stdout="", stderr="Traceback\nModuleNotFoundError: No module named 'orchestrator'"
    )
    with pytest.raises(SystemExit, match=r"(?s)probe.*No module named 'orchestrator'"):
        harness.main()
    assert not harness.out.exists() and harness.worker_calls == []


def test_a_probe_that_prints_its_json_and_then_exits_nonzero_refuses_to_start(harness: Harness) -> None:
    """The exit code decides, not the output: a probe that crashed after printing is not trusted."""
    harness.probe = SimpleNamespace(returncode=139, stdout=json.dumps(_PROBE), stderr="Segmentation fault")
    with pytest.raises(SystemExit, match=r"probe failed \(exit 139\)"):
        harness.main()
    assert not harness.out.exists()


@pytest.mark.parametrize("stdout", ["not json", "[1, 2]"])
def test_a_probe_that_prints_no_json_object_refuses_to_start(harness: Harness, stdout: str) -> None:
    harness.probe = SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    with pytest.raises(SystemExit, match="probe"):
        harness.main()
    assert not harness.out.exists() and harness.worker_calls == []


# --- a resume refuses a file from another run --------------------------------------------------------------


def test_a_resume_of_the_same_run_keeps_its_rows_and_scores_only_the_rest(harness: Harness) -> None:
    harness.main()
    lines = harness.lines()
    harness.out.write_text("".join(json.dumps(line) + "\n" for line in lines[:2]))  # proj-2 never finished
    harness.worker_calls.clear()
    harness.main()
    assert len(harness.worker_calls) == 1
    assert sorted(line["instance_id"] for line in harness.lines()[1:]) == ["proj__proj-1", "proj__proj-2"]


_ROW_DECIDING = [
    "mode",
    "spine_sha",
    "hybrid_cell",
    "dataset_rev",
    "dataset_file_sha256",
    "ids_file_sha256",
    "harness_sha256",
    "env_sha256",
    "research_specs_sha256",
]


@pytest.mark.parametrize("field", _ROW_DECIDING)
def test_a_resume_is_refused_when_a_row_deciding_field_differs(harness: Harness, field: str) -> None:
    harness.main()
    harness.rewrite_header(**{field: "from-another-run"})
    before = harness.out.read_bytes()
    harness.worker_calls.clear()
    with pytest.raises(SystemExit, match=rf"{field}.*new --out"):
        harness.main()
    assert harness.out.read_bytes() == before and harness.worker_calls == []


def test_a_resume_onto_a_file_from_before_the_environment_was_recorded_is_refused(harness: Harness) -> None:
    """A field the old header lacks is a mismatch: the file cannot show it came from this environment."""
    harness.main()
    harness.rewrite_header(drop_environment=None, drop_env_sha256=None, drop_research_specs_sha256=None)
    with pytest.raises(SystemExit, match=r"env_sha256, research_specs_sha256"):
        harness.main()


def test_a_resume_onto_a_file_without_a_header_is_refused(harness: Harness) -> None:
    harness.main()
    harness.out.write_text("".join(json.dumps(line) + "\n" for line in harness.lines()[1:]))
    with pytest.raises(SystemExit, match="no header"):
        harness.main()


def test_fields_that_do_not_decide_a_row_do_not_block_a_resume(harness: Harness) -> None:
    harness.main()
    harness.rewrite_header(started_utc="2000-01-01T00:00:00Z", total=99, environment={"stale": True})
    harness.main()
    assert harness.lines()[0]["total"] == 2


# --- each row carries what the worker said about itself ----------------------------------------------------


@pytest.mark.parametrize("mode", ["run", "dry"])
def test_a_scored_row_carries_the_research_error_its_time_and_the_environment_digest(
    harness: Harness, mode: str
) -> None:
    harness.worker_extra = {
        "research_error": "RuntimeError: boom",
        "research_s": 1.5,
        "env_sha256": "0123456789ab",
    }
    row = harness.module.score_one(_row(1), mode)
    assert row["research_error"] == "RuntimeError: boom"
    assert row["research_s"] == 1.5
    assert row["env_sha256"] == "0123456789ab"


def test_a_row_from_a_worker_that_reports_none_of_them_has_none_of_them(harness: Harness) -> None:
    row = harness.module.score_one(_row(1), "run")
    assert not {"research_error", "research_s", "env_sha256"} & set(row)
