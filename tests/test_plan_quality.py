"""`scripts/plan_quality/`: the grader that every `sdlc plan` quality number comes from (SSPN-389, D13).

The grader exists because hand-graded scorecards carried miscounts into Jira. Its guards are the
point, so they are what these tests pin: a parse that drops a line is an error, a deep-search rank is
withheld unless the probe reproduces what RCA printed, and an answer key only counts single-bug commits.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_DIR = Path(__file__).resolve().parents[1] / "scripts" / "plan_quality"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"plan_quality_{name}", _DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FILES = [
    "src/app/core.py",
    "src/app/store.py",
    "src/app/render.py",
    "src/other/core.py",
    "tests/test_core.py",
]


def _run_dir(tmp_path: Path, *, fault_lines: list[str] | None = None) -> Path:
    """A minimal graded run: every stage output in the shape the real commands print."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "ticket.md").write_text("# T-1: the render is wrong\n\nIt happens in `app/render.py`.\n")
    (run / "answer-key.json").write_text(
        json.dumps({"fix_files": ["src/app/store.py", "src/app/render.py"], "changed_symbols": {}})
    )
    (run / "investigate.md").write_text(
        "## Where it lands in the code\n"
        "- `render` (Function, 3 caller(s)) — src/app/render.py:10 — found by: name (#1)\n"
        "- `core` (Module, 0 caller(s)) — src/app/core.py:1 — found by: content (#2)\n"
    )
    lines = fault_lines or [
        "- `src/app/render.py` — name (#1)",
        "- `src/app/store.py` — content (#2), graph (#5)",
        "- `src/app/core.py` — meaning (#3)",
    ]
    (run / "rca-det.md").write_text(
        "## Fault site\n_ranked:_\n"
        + "\n".join(lines)
        + "\n\n## Root-cause hypotheses\n"
        + "1. **[medium]** The fault is in `src/app/render.py`.\n\n## Next step\n"
    )
    (run / "build.md").write_text(
        "## 3. Root cause\n**Fault site:** `src/app/render.py` — named by the ticket.\n\n"
        "## 4. PKG — what the graph knows\n"
        "| symbol | kind | where | callers | module |\n|---|---|---|---|---|\n"
        "| `render` | Function | `src/app/render.py:10` | 3 | `app.render` |\n"
        "| `core` | Module | `src/app/core.py:1` | 0 | `app.core` |\n\n"
        "**Areas:** x\n\n"
        "## 5. Blast radius\n```mermaid\nflowchart TD\n"
        '  subgraph target["what this ticket changes"]\n    n0["src/app/render.py"]\n  end\n```\n\n'
        "## 6. Design\n\n## 7. Files\n**Changed**\n\n| file | size |\n|---|---|\n"
        "| `src/app/render.py` | 1,000 b |\n| `src/app/core.py` | 2,000 b |\n\n## 8. Acceptance criteria\n"
    )
    (run / "design-det.md").write_text("## Files to edit\n- app/render.py\n- src/app/core.py\n\n## Risks\n")
    return run


def test_resolve_takes_a_path_as_written_or_its_one_suffix_match_and_refuses_a_guess() -> None:
    g = _load("grade")
    assert g.resolve("src/app/store.py", FILES) == "src/app/store.py"
    assert g.resolve("app/store.py", FILES) == "src/app/store.py"
    assert g.resolve("core.py", FILES) is None  # two files end in /core.py: a guess, refused
    assert g.resolve("app/missing.py", FILES) is None


def test_each_stage_is_graded_from_its_own_output_and_hits_are_split_named_vs_found(tmp_path: Path) -> None:
    g = _load("grade")
    r = g.grade_run(_run_dir(tmp_path), FILES)
    assert r["ticket_named"] == ["src/app/render.py"]
    ranking = r["stages"]["search: rca fault-site ranking"]
    assert ranking["fix_hits"] == {"src/app/render.py": 1, "src/app/store.py": 2}
    assert ranking["recall"] == "2/2"
    assert ranking["precision"] == "2/3"
    # store.py is a fix file the ticket never named: found by search, not read off the ticket.
    assert ranking["fix_hits_not_named_in_ticket"] == ["src/app/store.py"]
    hyp = r["stages"]["rca det: hypotheses"]
    assert hyp["recall"] == "1/2" and hyp["fix_hits_not_named_in_ticket"] == []
    design = r["stages"]["design det"]
    assert design["n"] == 2 and design["recall"] == "1/2" and design["precision"] == "1/2"
    assert r["evidence_rows"] == {
        "rows": 2,
        "in_fix_file": 1,
        "changed_symbols_listed": [],
        "zero_caller_rows": 1,
    }


def test_a_fault_site_line_the_parser_cannot_read_is_an_error_not_a_dropped_file(tmp_path: Path) -> None:
    g = _load("grade")
    # A line whose evidence the parser does not expect. The first grader's regex skipped such
    # lines silently and reported a 6-file ranking that RCA had printed with 10.
    run = _run_dir(
        tmp_path, fault_lines=["- `src/app/render.py` — name (#1)", "- src/app/store.py (no backticks)"]
    )
    with pytest.raises(g.ParseError, match="parsed 1 but the section has 2"):
        g.grade_run(run, FILES)


def test_an_empty_section_is_an_error_never_a_zero(tmp_path: Path) -> None:
    g = _load("grade")
    run = _run_dir(tmp_path)
    (run / "design-det.md").write_text("## Files to edit\n\n## Risks\n")
    with pytest.raises(g.ParseError, match="design det: parsed to nothing"):
        g.grade_run(run, FILES)


def test_deep_search_ranks_are_withheld_unless_the_probe_reproduces_what_rca_printed(tmp_path: Path) -> None:
    g = _load("grade")
    run = _run_dir(tmp_path)
    rows = [
        {"rank": 1, "name": "render", "file": "src/app/render.py"},
        {"rank": 2, "name": "save", "file": "src/app/store.py"},
        {"rank": 3, "name": "core", "file": "src/app/core.py"},
    ]
    deep = {
        "hits": 3,
        "files": ["src/app/render.py", "src/app/store.py", "src/app/core.py"],
        "files_within_rca_limit_50": ["src/app/render.py", "src/app/store.py", "src/app/core.py"],
        "rows": rows,
    }
    (run / "search-deep.json").write_text(json.dumps(deep))
    ok = g.grade_run(run, FILES)["deep_search"]
    assert ok["reproduces_rca_top10"] is True
    assert ok["fix_file_rank"] == {"src/app/store.py": 2, "src/app/render.py": 1}

    deep["files_within_rca_limit_50"] = ["src/app/core.py", "src/app/render.py", "src/app/store.py"]
    (run / "search-deep.json").write_text(json.dumps(deep))
    bad = g.grade_run(run, FILES)["deep_search"]
    assert bad["reproduces_rca_top10"] is False
    assert "fix_file_rank" not in bad and "mismatch" in bad


def test_rca_sees_only_the_files_of_its_first_fifty_hits() -> None:
    p = _load("probe_search")
    rows = [{"file": f"src/f{i % 3}.py"} for i in range(60)] + [{"file": "src/late.py"}]
    assert p.distinct_files(rows, limit=50) == ["src/f0.py", "src/f1.py", "src/f2.py"]
    assert p.distinct_files(rows) == ["src/f0.py", "src/f1.py", "src/f2.py", "src/late.py"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src" / "app").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    # CI has no git identity; the commits here need one of their own.
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "app" / "store.py").write_text(
        "def save(x):\n    return x\n\n\ndef load(x):\n    return x\n"
    )
    (repo / "src" / "app" / "render.py").write_text("def render(x):\n    return x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _commit(repo: Path, rel: str, text: str, msg: str) -> str:
    (repo / rel).write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "--short", "HEAD").strip()


def test_an_answer_key_names_the_functions_the_fix_commits_changed(tmp_path: Path) -> None:
    k = _load("make_key")
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "--short", "HEAD").strip()
    h = _commit(
        repo, "src/app/store.py", "def save(x):\n    return x + 1\n\n\ndef load(x):\n    return x\n", "fix"
    )
    key = k.key_from(repo, "T-1", base, commits=[h])
    assert key["fix_files"] == ["src/app/store.py"]
    assert key["changed_symbols"] == {"src/app/store.py": ["save"]}
    assert key["target"] == base


def test_only_single_bug_commits_build_an_answer_key(tmp_path: Path) -> None:
    s = _load("select_set")
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "--short", "HEAD").strip()
    _commit(repo, "src/app/store.py", "def save(x):\n    return 2\n", "fix(app): save (B1, SSPN-1)")
    # A fix-forward that names two bugs, and a release cut naming many: neither can be credited
    # to SSPN-1 alone, so neither may add files to its key or move its base.
    _commit(
        repo,
        "src/app/render.py",
        "def render(x):\n    return 2\n",
        "fix: B1 and B2 together (SSPN-1, SSPN-2)",
    )
    _commit(
        repo, "src/app/render.py", "def render(x):\n    return 3\n", "chore(release): cut (SSPN-1, SSPN-3)"
    )
    cands = [{"key": "SSPN-1", "summary": "[B1] save is wrong"}, {"key": "SSPN-2", "summary": "[B2] render"}]
    out = s.select(repo, cands, ref="main", exclude=set(), seed=1, n=12, dev=8)
    picked = {c["key"]: c for c in out["picked"]}
    assert set(picked) == {"SSPN-1"}
    assert picked["SSPN-1"]["fix_files"] == ["src/app/store.py"]
    assert picked["SSPN-1"]["base"] == base
    assert dict(out["report"])["SSPN-2"] == "no single-bug commit names it"


def test_the_draw_is_seeded_and_split_dev_then_held_out(tmp_path: Path) -> None:
    s = _load("select_set")
    repo = _repo(tmp_path)
    cands = []
    for i in range(1, 16):
        _commit(repo, "src/app/store.py", f"def save(x):\n    return {i}\n", f"fix: B{i} (SSPN-{i})")
        cands.append({"key": f"SSPN-{i}", "summary": f"[B{i}] bug"})
    a = s.select(repo, cands, ref="main", exclude={"SSPN-15"}, seed=7, n=12, dev=8)
    b = s.select(repo, cands, ref="main", exclude={"SSPN-15"}, seed=7, n=12, dev=8)
    assert [c["key"] for c in a["picked"]] == [c["key"] for c in b["picked"]]
    assert a["qualified"] == 14 and len(a["picked"]) == 12
    assert [c["split"] for c in a["picked"]] == ["dev"] * 8 + ["held-out"] * 4
    assert "SSPN-15" not in {c["key"] for c in a["picked"]}
    # The seed decides the draw: a first-n cut would make every seed pick the same tickets in the
    # same order, which is choosing by commit date, not drawing.
    orders = {
        tuple(
            c["key"] for c in s.select(repo, cands, ref="main", exclude=set(), seed=k, n=12, dev=8)["picked"]
        )
        for k in range(5)
    }
    assert len(orders) > 1


def test_aggregate_adds_hits_and_sizes_across_tickets() -> None:
    a = _load("aggregate")
    stage = "design det"
    g1 = {
        "graded_files": ["a", "b"],
        "ticket_named": ["a"],
        "stages": {stage: {"n": 4, "fix_hits": {"a": 1}, "fix_hits_not_named_in_ticket": []}},
    }
    g2 = {
        "graded_files": ["c"],
        "ticket_named": [],
        "stages": {stage: {"n": 1, "fix_hits": {"c": 1}, "fix_hits_not_named_in_ticket": ["c"]}},
    }
    rows = a.stage_totals({"T-1": g1, "T-2": g2})
    assert rows[stage] == {"hits": 2, "fix_total": 3, "unnamed": 1, "listed": 5, "covered": 2, "tickets": 2}
