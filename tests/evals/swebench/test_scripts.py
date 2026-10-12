"""The two scripts that drive the harness: their flags reach the code that acts on them."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from orchestrator.evals.swebench.ledger import Ledger, LedgerRow

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _instance_line(instance_id: str) -> str:
    return json.dumps(
        {
            "instance_id": instance_id,
            "repo": "psf/requests",
            "version": "1.1",
            "base_commit": "b" * 40,
            "problem_statement": "p",
            "image": "swebench/x:latest",
            "image_digest": "sha256:" + "a" * 64,
            "test_patch_files": [],
        }
    )


def _row(instance_id: str, bucket: str = "submitted", spent: float = 1.0) -> LedgerRow:
    return LedgerRow(instance_id=instance_id, repo="psf/requests", bucket=bucket, reason="r", spent_usd=spent)


# --- swebench_run.py ----------------------------------------------------------------------


@pytest.fixture
def run_script(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, dict[str, Any]]:
    module = _load("swebench_run")
    seen: dict[str, Any] = {}

    async def fake_sweep(instances: Any, **kw: Any) -> Any:
        seen.update(kw)
        seen["instances"] = [i.generation.instance_id for i in instances]
        from orchestrator.evals.swebench.sweep import SweepResult

        return SweepResult()

    monkeypatch.setattr(module, "sweep", fake_sweep)
    monkeypatch.setattr(module, "CliDocker", object)
    return module, seen


def _run_args(tmp_path: Path, *extra: str) -> list[str]:
    instances = tmp_path / "instances.jsonl"
    instances.write_text(_instance_line("psf__requests-1111") + "\n", encoding="utf-8")
    return [
        "--instances", str(instances), "--workdir", str(tmp_path / "work"),
        "--max-cost", "10", "--max-total", "100", *extra,
    ]  # fmt: skip


def test_generator_errors_are_not_retried_unless_asked(
    run_script: tuple[ModuleType, dict[str, Any]], tmp_path: Path
) -> None:
    module, seen = run_script
    module.main(_run_args(tmp_path))
    assert seen["retry_generator_errors"] is False

    module.main(_run_args(tmp_path, "--retry-generator-errors"))
    assert seen["retry_generator_errors"] is True


def test_pass_env_is_repeatable_and_reaches_the_config(
    run_script: tuple[ModuleType, dict[str, Any]], tmp_path: Path
) -> None:
    module, seen = run_script
    module.main(_run_args(tmp_path, "--pass-env", "AWS_PROFILE", "--pass-env", "AWS_REGION"))
    assert seen["cfg"].pass_env == ("AWS_PROFILE", "AWS_REGION")

    module.main(_run_args(tmp_path))
    assert seen["cfg"].pass_env == ()


def test_the_total_is_described_as_a_projection_not_a_hard_stop(
    run_script: tuple[ModuleType, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    module, _ = run_script
    with pytest.raises(SystemExit):
        module.main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "projection" in text and "hard stop" not in text


# --- swebench_report.py -------------------------------------------------------------------


def _report_setup(tmp_path: Path, ids: list[str], rows: list[LedgerRow], report: dict[str, Any]) -> list[str]:
    instances = tmp_path / "instances.jsonl"
    instances.write_text("".join(_instance_line(i) + "\n" for i in ids), encoding="utf-8")
    work = tmp_path / "work"
    book = Ledger(work / "ledger.jsonl")
    for row in rows:
        book.append(row)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return ["--instances", str(instances), "--workdir", str(work), "--report", str(report_path)]


def test_the_report_script_prints_the_six_buckets_and_writes_the_graded_book(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load("swebench_report")
    argv = _report_setup(
        tmp_path,
        ["a__a-1", "b__b-2", "p__p-3"],
        [_row("a__a-1"), _row("b__b-2"), _row("p__p-3", "parked")],
        {"resolved_ids": ["a__a-1"], "unresolved_ids": ["b__b-2"]},
    )

    assert module.main(argv) == 0

    out = capsys.readouterr().out
    for bucket in ("resolved", "unresolved", "parked", "generator-error", "budget-stop", "grader-error"):
        assert bucket in out
    assert "33.3%" in out  # 1 of 3, the whole denominator
    written = json.loads((tmp_path / "work" / "results.json").read_text(encoding="utf-8"))
    assert written["summary"]["counts"]["resolved"] == 1 and written["summary"]["total"] == 3
    # The append-only ledger is left alone; the settled book is a separate file.
    first = (tmp_path / "work" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(first)["bucket"] == "submitted"


def test_the_report_script_refuses_an_incomplete_ledger_and_says_which_instance(tmp_path: Path) -> None:
    module = _load("swebench_report")
    argv = _report_setup(tmp_path, ["a__a-1", "b__b-2"], [_row("a__a-1")], {"resolved_ids": ["a__a-1"]})

    with pytest.raises(SystemExit, match="b__b-2"):
        module.main(argv)

    assert not (tmp_path / "work" / "results.json").exists()
