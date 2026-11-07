"""`hybrid_bar.py`: the pre-registered bars, and the one sealed read (D15, D22)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "swebench_localization" / "hybrid_bar.py"
_spec = importlib.util.spec_from_file_location("hybrid_bar", _SCRIPT)
assert _spec is not None and _spec.loader is not None
bar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bar)


def test_the_sign_test_is_the_exact_binomial_tail() -> None:
    assert bar.sign_p_one_sided(0, 0) == 1.0
    assert bar.sign_p_one_sided(9, 1) == pytest.approx(11 / 1024)
    assert bar.sign_p_one_sided(20, 10) == pytest.approx(0.0494, abs=1e-4)


def _row(h: int | None, v: int | None, **extra: int | None) -> dict[str, object]:
    base = {
        "rank_bm25": None,
        "rank_rca0": None,
        "rank_rca1": None,
        "rank_design0": None,
        "rank_design1": None,
    }
    return {"status": "scored", "rank_h": h, "rank_v": v, **base, **extra}


def test_the_investigate_bar_needs_the_test_and_both_guards() -> None:
    gains = [_row(5, None) for _ in range(12)]
    assert bar.verdicts(gains)["investigate"]["met"] is True
    # The same top-10 gain, but three issues fall out of the top-1: the guard fails the bar.
    lost_top1 = gains + [_row(2, 1) for _ in range(3)]
    verdict = bar.verdicts(lost_top1)["investigate"]
    assert verdict["top10"]["p"] < 0.05 and verdict["top1_guard"]["net"] == -3 and verdict["met"] is False


def test_design_is_judged_on_membership_not_rank() -> None:
    rows = [_row(1, 1, rank_design1=5, rank_design0=None) for _ in range(10)]
    assert bar.verdicts(rows)["design"]["met"] is True


def _results(tmp_path: Path, ids_sha: str) -> Path:
    path = tmp_path / "results.jsonl"
    rows = [{"header": True, "ids_file_sha256": ids_sha, "hybrid_cell": {"recipe": "B"}}]
    rows += [_row(5, None) for _ in range(12)]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, check=False)


def test_the_sealed_pool_is_read_once_and_only_the_sealed_pool(tmp_path: Path) -> None:
    log = tmp_path / "reads.log"
    wrong = _run(str(_results(tmp_path, "0" * 64)), "--sealed", "--log", str(log))
    assert wrong.returncode != 0 and "not the sealed pool" in wrong.stderr
    first = _run(str(_results(tmp_path, bar.SEALED_POOL_SHA256)), "--sealed", "--log", str(log))
    assert first.returncode == 0 and json.loads(first.stdout)["investigate"]["met"] is True
    second = _run(str(_results(tmp_path, bar.SEALED_POOL_SHA256)), "--sealed", "--log", str(log))
    assert second.returncode != 0 and "already read" in second.stderr


def test_a_results_file_without_every_arm_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps({"status": "scored", "rank_h": 1}))
    assert "refused" in _run(str(path)).stderr
