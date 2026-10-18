"""SSPN-204: the split is deterministic and the sealed half stays sealed until it is asked for by name."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "intake_quality_cli", Path(__file__).parents[2] / "scripts" / "intake_quality.py"
)
assert _SPEC and _SPEC.loader
cli = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cli)


def _runs(tmp_path: Path, n: int = 6) -> Path:
    for i in range(n):
        run = tmp_path / f"repo__issue-{i}"
        (run / "intake-cache").mkdir(parents=True)
        (run / "issue.md").write_text(f"Title {i}\nThe widget should render case {i} correctly.\n")
        spec = {
            "acceptance_criteria": [f"The widget should render case {i} correctly."],
            "proposed_criteria": [],
        }
        (run / "intake-cache" / "x.json").write_text(json.dumps({"specs": [spec]}))
    (tmp_path / "repo__no-cache").mkdir()
    (tmp_path / "repo__no-cache" / "issue.md").write_text("no intake output")
    return tmp_path


def test_the_split_is_deterministic_halves_and_ignores_runs_without_intake_output(tmp_path: Path) -> None:
    runs = _runs(tmp_path)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert cli.main(["split", "--runs", str(runs), "--seed", "7", "--out", str(a)]) == 0
    assert cli.main(["split", "--runs", str(runs), "--seed", "7", "--out", str(b)]) == 0
    assert a.read_text() == b.read_text()
    split = json.loads(a.read_text())
    assert split["n"] == 6 and len(split["dev"]) == 3 and len(split["sealed"]) == 3
    assert (
        not set(split["dev"]) & set(split["sealed"])
        and "repo__no-cache" not in split["dev"] + split["sealed"]
    )


def test_a_different_seed_gives_a_different_split(tmp_path: Path) -> None:
    runs = _runs(tmp_path, n=12)
    outs = []
    for seed in ("1", "2"):
        out = tmp_path / f"s{seed}.json"
        cli.main(["split", "--runs", str(runs), "--seed", seed, "--out", str(out)])
        outs.append(json.loads(out.read_text())["dev"])
    assert outs[0] != outs[1]


def test_the_sealed_half_is_refused_unless_unsealed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _runs(tmp_path)
    split = tmp_path / "split.json"
    cli.main(["split", "--runs", str(runs), "--seed", "7", "--out", str(split)])
    assert cli.main(["score", "--runs", str(runs), "--split", str(split), "--half", "sealed"]) == 2
    assert "refused" in capsys.readouterr().err
    assert (
        cli.main(["score", "--runs", str(runs), "--split", str(split), "--half", "sealed", "--unseal"]) == 0
    )


def test_the_dev_half_is_scored(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs = _runs(tmp_path)
    split = tmp_path / "split.json"
    cli.main(["split", "--runs", str(runs), "--seed", "7", "--out", str(split)])
    out = tmp_path / "score.json"
    assert (
        cli.main(["score", "--runs", str(runs), "--split", str(split), "--half", "dev", "--json", str(out)])
        == 0
    )
    data = json.loads(out.read_text())
    assert data["n"] == 3 and data["total"]["stated"] == 3 and data["rates"]["faithful_share"] == 1.0
    assert "dev: 3 issues" in capsys.readouterr().out
