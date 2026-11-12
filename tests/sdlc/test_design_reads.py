"""sdlc-search-use P4: what a design lists to read, and why (D2, D9, D12, D19, D27–D36).

Three things change what codegen is shown:

* **B165 (D9, D28, D36).** A ticket naming more than three files made every one an edit target: on
  SSPN-376 that was 14 files for one fix, precision 1/14. With RCA's hypotheses in hand, only the
  named files they name stay edit targets; the rest are listed to read. With no RCA, or when RCA names
  none of them, nothing changes.
* **Search finds and completion (D27, D29, D34).** Up to two strong search finds and up to three
  one-hop neighbours of the files to edit (the completion probe's frozen ranking) are listed to read.
* **Reasons without paths (D32).** Codegen treats every path in the read section as read-only and
  drops it from the edit list (``codegen.py`` ``_paths_from``), so a reason naming a file to edit would
  silently take it away. Reasons name symbols, never paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.design_reads import (
    FINDS_CAP,
    READ_CAP,
    REFERENCE_BUDGET,
    Read,
    assemble,
    completion_reads,
    narrow,
    pick_finds,
    rca_files,
    safe_reason,
)
from orchestrator.sdlc.seed_signals import CompletionHit
from orchestrator.sdlc.source_paths import named_paths

FIVE = ["src/app/a.py", "src/app/b.py", "src/app/c.py", "src/app/d.py", "src/app/e.py"]


@pytest.fixture(autouse=True)
def _switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three are off by default after P4's graded run (see ``design_reads``); these tests pin what they
    do when switched on. The defaults have their own tests below, which clear the switches."""
    monkeypatch.setenv("SPINE_DESIGN_B165", "1")
    monkeypatch.setenv("SPINE_DESIGN_COMPLETION", "1")


def test_by_default_b165_does_not_narrow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPINE_DESIGN_B165")
    got = narrow(FIVE, {"src/app/c.py"})
    assert got.edit == FIVE and got.moved == [] and got.note == ""


def test_by_default_a_design_lists_no_completion_candidate_and_no_search_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc.design import _fallback_design

    monkeypatch.delenv("SPINE_DESIGN_COMPLETION")
    monkeypatch.delenv("SPINE_DESIGN_B165")
    _hybrid_on(monkeypatch)
    store, spec = _tree(tmp_path)
    design = _fallback_design(spec, None, store, tmp_path, rca_files={"src/app/a.py"})
    assert design["files_to_touch"] == FIVE  # no narrowing
    assert design["files_to_read"] == [] and design["reads_left_out"] == []
    assert design["completion"]["not_run"].startswith("off by default")
    assert not any("embedding model" in r for r in design["risks"])


# --- RCA's files --------------------------------------------------------------------------------


def test_rca_files_are_the_paths_its_hypotheses_name_in_claim_or_evidence() -> None:
    from orchestrator.sdlc.rca import Hypothesis

    as_dicts = [
        {"claim": "`run` in src/app/a.py drops the row", "evidence": ["src/app/b.py:12 calls it"]},
        {"claim": "a regression", "evidence": []},
    ]
    assert rca_files(as_dicts) == {"src/app/a.py", "src/app/b.py"}
    assert rca_files([Hypothesis(claim="see src/app/c.py", evidence=("nothing",))]) == {"src/app/c.py"}
    assert rca_files([]) == set()
    assert rca_files(None) is None  # RCA did not run, which is not "RCA named nothing"


# --- B165 ---------------------------------------------------------------------------------------


def test_with_more_than_three_named_files_only_those_rca_names_stay_edit_targets() -> None:
    got = narrow(FIVE, {"src/app/c.py"})
    assert got.edit == ["src/app/c.py"]
    assert got.moved == ["src/app/a.py", "src/app/b.py", "src/app/d.py", "src/app/e.py"]
    assert got.note and not named_paths(got.note)  # a risk line, and risks are an edit section


def test_four_named_files_is_already_more_than_three() -> None:
    got = narrow(FIVE[:4], {"src/app/c.py"})
    assert got.edit == ["src/app/c.py"] and len(got.moved) == 3


def test_rca_naming_a_file_by_a_shorter_path_still_counts() -> None:
    """RCA quotes what the traceback or the graph printed, often relative to a package root."""
    assert narrow(FIVE, {"app/c.py"}).edit == ["src/app/c.py"]
    assert narrow(FIVE, {"c.py"}).edit == ["src/app/c.py"]
    assert narrow(FIVE, {"pp/c.py"}).edit == FIVE  # a path-segment boundary, not a substring


def test_three_or_fewer_named_files_are_all_edit_targets_as_today() -> None:
    got = narrow(FIVE[:3], {"src/app/c.py"})
    assert got.edit == FIVE[:3] and got.moved == [] and got.note == ""


def test_without_rca_every_named_file_stays_an_edit_target() -> None:
    got = narrow(FIVE, None)
    assert got.edit == FIVE and got.moved == [] and got.note == ""


def test_when_rca_names_none_of_them_all_stay_edit_targets_and_the_design_says_so() -> None:
    """D36: a design with no edit target is worse than a long one."""
    got = narrow(FIVE, {"src/elsewhere.py"})
    assert got.edit == FIVE and got.moved == []
    assert "RCA" in got.note and not named_paths(got.note)


def test_the_b165_switch_restores_todays_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_DESIGN_B165", "0")
    got = narrow(FIVE, {"src/app/c.py"})
    assert got.edit == FIVE and got.moved == [] and got.note == ""


def test_a_path_rca_names_with_a_line_number_still_matches() -> None:
    got = narrow(FIVE, rca_files([{"claim": "x", "evidence": ["src/app/c.py:40"]}]))
    assert got.edit == ["src/app/c.py"]


# --- Search finds -------------------------------------------------------------------------------


def test_search_finds_are_strong_hits_outside_the_edit_list_at_most_two() -> None:
    rows = [
        ("src/app/a.py", "run", False),  # an edit target already
        ("src/app/x.py", "parse", True),  # weak: one shared word
        ("src/app/y.py", "load", False),
        ("src/app/y.py", "save", False),  # same file twice
        ("src/app/z.py", "store", False),
        ("src/app/w.py", "write", False),  # past the cap
    ]
    finds = pick_finds(rows, {"src/app/a.py"})
    assert FINDS_CAP == 2
    assert [r.path for r in finds] == ["src/app/y.py", "src/app/z.py"]
    assert "`load`" in finds[0].reason


# --- Completion reads ---------------------------------------------------------------------------


def test_a_completion_read_names_the_link_by_symbol_and_keeps_the_location_as_evidence() -> None:
    hit = CompletionHit(
        file="src/app/helper.py",
        rank=4,
        edge_kind="CALLS",
        src="py:app.a.run",
        dst="py:app.helper.helper",
        at="src/app/a.py:3",
    )
    (read,) = completion_reads([hit], of_n=9)
    assert read.path == "src/app/helper.py"
    assert "`run`" in read.reason and "`helper`" in read.reason and "CALLS" in read.reason
    assert not named_paths(read.reason)  # D32
    assert "src/app/a.py:3" in read.evidence  # the build document may show where


# --- Order and cap ------------------------------------------------------------------------------


def _r(path: str) -> Read:
    return Read(path=path, reason="why")


def test_reads_keep_the_decided_order_and_stop_at_five_listing_the_rest() -> None:
    moved = [_r("m1.py"), _r("m2.py")]
    existing = [_r("e1.py")]
    completion = [_r("c1.py"), _r("c2.py"), _r("c3.py")]
    finds = [_r("f1.py")]
    reads, left_out = assemble([moved, existing, completion, finds], edit=["a.py"])
    assert READ_CAP == 5
    assert [r.path for r in reads] == ["m1.py", "m2.py", "e1.py", "c1.py", "c2.py"]
    assert left_out == ["c3.py", "f1.py"]


def test_a_read_is_never_an_edit_target_and_never_listed_twice() -> None:
    reads, left_out = assemble([[_r("a.py"), _r("b.py")], [_r("b.py"), _r("c.py")]], edit=["a.py"])
    assert [r.path for r in reads] == ["b.py", "c.py"] and left_out == []


def test_a_reason_that_names_a_path_is_replaced() -> None:
    assert not named_paths(safe_reason("called from src/app/a.py:3"))
    assert safe_reason("called by `run`") == "called by `run`"


# --- The design, end to end ---------------------------------------------------------------------


def _tree(root: Path) -> tuple[FactStore, dict[str, Any]]:
    """Five named files; `a.run` calls `helper.helper`, which no ticket names."""
    files = {
        "src/app/a.py": "def run(rows):\n    return helper(rows)\n",
        "src/app/b.py": "def b_fn():\n    return 1\n",
        "src/app/c.py": "def c_fn():\n    return 2\n",
        "src/app/d.py": "def d_fn():\n    return 3\n",
        "src/app/e.py": "def e_fn():\n    return 4\n",
        "src/app/helper.py": "def helper(rows):\n    '''Merge the ledger rows.'''\n    return rows\n",
    }
    batch = FactBatch()
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
        mod = "app." + Path(rel).stem
        fn = text.split("(", 1)[0].removeprefix("def ")
        batch.add_node(Node(f"py:{mod}", NodeKind.MODULE, mod, "python", Provenance(rel, 1, 3)))
        batch.add_node(Node(f"py:{mod}.{fn}", NodeKind.FUNCTION, fn, "python", Provenance(rel, 1, 3)))
        batch.add_edge(Edge(f"py:{mod}", f"py:{mod}.{fn}", EdgeKind.CONTAINS))
    batch.add_edge(
        Edge("py:app.a.run", "py:app.helper.helper", EdgeKind.CALLS, Provenance("src/app/a.py", 2))
    )
    spec = {
        "title": "Run drops ledger rows",
        "summary": "run loses rows when merging the ledger",
        "technical_notes": "Touches " + ", ".join(FIVE) + ".",
    }
    return FactStore(batch), spec


def _hybrid_on(monkeypatch: pytest.MonkeyPatch, model: str = "stand-in") -> None:
    monkeypatch.setenv("SPINE_HYBRID_SEARCH", "1")
    monkeypatch.setenv("SPINE_EMBED_MODEL", model)


def test_the_design_lists_a_neighbour_of_its_edit_target_to_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc.design import _fallback_design

    _hybrid_on(monkeypatch)
    store, spec = _tree(tmp_path)
    design = _fallback_design(spec, None, store, tmp_path, rca_files={"src/app/a.py"})
    assert design["files_to_touch"] == ["src/app/a.py"]
    reads = design["files_to_read"]
    assert reads[:4] == ["src/app/b.py", "src/app/c.py", "src/app/d.py", "src/app/e.py"]  # B165 first
    assert "src/app/helper.py" in reads[4:] or "src/app/helper.py" in design["reads_left_out"]
    assert design["completion"]["not_run"] == ""
    assert design["completion"]["shown"] >= 1
    assert all(not named_paths(design["read_reasons"][p]) for p in reads)


def test_without_a_meaning_leg_completion_does_not_run_and_the_design_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc.design import _fallback_design

    _hybrid_on(monkeypatch, model="none")
    store, spec = _tree(tmp_path)
    design = _fallback_design(spec, None, store, tmp_path, rca_files={"src/app/a.py"})
    assert "src/app/helper.py" not in design["files_to_read"] + design["reads_left_out"]
    assert "embedding model" in design["completion"]["not_run"]
    assert any("embedding model" in r for r in design["risks"])


def test_the_completion_switch_turns_it_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.design import _fallback_design

    _hybrid_on(monkeypatch)
    monkeypatch.setenv("SPINE_DESIGN_COMPLETION", "0")
    store, spec = _tree(tmp_path)
    design = _fallback_design(spec, None, store, tmp_path, rca_files={"src/app/a.py"})
    assert "src/app/helper.py" not in design["files_to_read"] + design["reads_left_out"]
    assert "SPINE_DESIGN_COMPLETION" in design["completion"]["not_run"]


def test_a_strong_search_find_outside_the_named_files_is_listed_to_read(tmp_path: Path) -> None:
    """D29, through the design: the ticket names two files, and its words land strongly on a third."""
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.sdlc.design import _fallback_design

    for rel, text in {
        "src/app/a.py": "def run(rows):\n    return rows\n",
        "src/app/b.py": "def b_fn():\n    return 1\n",
        "src/app/ledger.py": "def merge_ledger_rows(rows):\n    return rows\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    spec = {
        "title": "merge_ledger_rows drops rows",
        "summary": "merge_ledger_rows loses ledger rows",
        "technical_notes": "Touches src/app/a.py and src/app/b.py.",
    }
    design = _fallback_design(spec, None, store, tmp_path)
    assert design["files_to_touch"] == ["src/app/a.py", "src/app/b.py"]
    assert design["files_to_read"] == ["src/app/ledger.py"]
    assert "`merge_ledger_rows`" in design["read_reasons"]["src/app/ledger.py"]


def test_the_gate_weighs_exactly_the_files_the_design_will_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSPN-231: the validity gate and the design must agree, RCA's narrowing included."""
    from orchestrator.sdlc.design import gate_file_set, produce_design

    _hybrid_on(monkeypatch)
    store, spec = _tree(tmp_path)
    design = await_design(
        produce_design(spec, overview=None, store=store, root=tmp_path, rca_files={"src/app/a.py"})
    )
    gate = gate_file_set(spec, store=store, root=tmp_path, rca_files={"src/app/a.py"})
    assert list(gate) == design["files_to_touch"] == ["src/app/a.py"]


def await_design(coro: Any) -> dict[str, Any]:
    import asyncio

    return asyncio.run(coro)


def test_a_rendered_design_keeps_every_edit_target_through_codegens_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The D32 trap, end to end: codegen drops from the edit list any path the read section names."""
    from orchestrator.sdlc.codegen import _design_read_paths, _paths_from
    from orchestrator.sdlc.design import _fallback_design, render_design_md

    _hybrid_on(monkeypatch)
    store, spec = _tree(tmp_path)
    design = _fallback_design(spec, None, store, tmp_path, rca_files={"src/app/a.py"})
    md = render_design_md(spec, design)
    assert _paths_from({}, md, tmp_path) == design["files_to_touch"]
    assert _design_read_paths(md, tmp_path) == design["files_to_read"]


def test_each_read_is_rendered_with_its_reason() -> None:
    from orchestrator.sdlc.design import render_design_md
    from orchestrator.sdlc.scope import READ_HEADING

    md = render_design_md(
        {"title": "t"},
        {
            "approach": "A",
            "files_to_touch": ["src/a.py"],
            "files_to_read": ["src/b.py"],
            "read_reasons": {"src/b.py": "called by `run`"},
        },
    )
    assert f"## {READ_HEADING}\n- src/b.py — called by `run`" in md


# --- Codegen ------------------------------------------------------------------------------------


def test_codegen_shows_at_most_five_reads_within_the_budget_excerpted_at_their_anchor(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _reference_files
    from orchestrator.sdlc.scope import READ_HEADING

    lines = []
    for i in range(7):
        rel = f"src/r{i}.py"
        body = "".join(f"def filler_{i}_{j}():\n    return {j}\n\n" for j in range(800))
        body += f"def target_{i}():\n    return 'here'\n"
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / rel).write_text(body, encoding="utf-8")
        lines.append(f"- {rel} — called by `target_{i}`")
    design = f"## {READ_HEADING}\n" + "\n".join(lines) + "\n"

    block = _reference_files(tmp_path, design, [])

    shown = [i for i in range(7) if f"src/r{i}.py" in block.split("left out")[0]]
    assert len(shown) == READ_CAP
    assert "def target_0" in block  # the excerpt lands on the anchor, not on the file's top
    assert "left out" in block and "src/r6.py" in block.split("left out")[1]
    assert len(block.encode("utf-8")) <= REFERENCE_BUDGET + 2_000  # the cap, plus the framing text


# --- The build document -------------------------------------------------------------------------


def test_the_plan_runs_rca_before_the_gate_and_the_design_and_hands_both_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D28: the build document ran RCA after design, so design could not use it."""
    import asyncio

    from orchestrator.sdlc.builddoc import build_plan
    from orchestrator.sdlc.design import gate_file_set, produce_design
    from orchestrator.sdlc.rca import Hypothesis, build_rca

    _store_unused, spec = _tree(tmp_path)
    order: list[str] = []
    seen: dict[str, Any] = {}
    real_rca, real_design, real_gate = build_rca, produce_design, gate_file_set

    async def _rca(*a: Any, **k: Any) -> Any:
        order.append("rca")
        report = await real_rca(*a, **k)
        report.hypotheses = [Hypothesis(claim="`run` in src/app/a.py drops the row")]
        return report

    async def _design(*a: Any, **k: Any) -> Any:
        order.append("design")
        seen["design"] = k.get("rca_files")
        return await real_design(*a, **k)

    def _gate(*a: Any, **k: Any) -> Any:
        order.append("gate")
        seen["gate"] = k.get("rca_files")
        return real_gate(*a, **k)

    monkeypatch.setattr("orchestrator.sdlc.rca.build_rca", _rca)
    monkeypatch.setattr("orchestrator.sdlc.design.produce_design", _design)
    monkeypatch.setattr("orchestrator.sdlc.design.gate_file_set", _gate)
    md = asyncio.run(build_plan(spec, root=tmp_path, issue_type="Bug"))

    assert order.index("rca") < order.index("gate") < order.index("design")
    assert seen["design"] == seen["gate"] == {"src/app/a.py"}
    seven = md.split("## 7. Files", 1)[1].split("## 8.", 1)[0]
    assert "**Read**" in seven and "src/app/b.py" in seven


def test_a_story_plan_runs_no_rca_and_keeps_todays_rule(tmp_path: Path) -> None:
    import asyncio

    from orchestrator.sdlc.builddoc import build_plan

    _store_unused, spec = _tree(tmp_path)
    md = asyncio.run(build_plan(spec, root=tmp_path, issue_type="Story"))
    seven = md.split("## 7. Files", 1)[1].split("## 8.", 1)[0]
    assert all(f in seven.split("**Read**")[0] for f in FIVE)  # all five still changed


def test_a_default_plan_does_not_repeat_that_completion_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off by default is a decision, not a finding: §7 says nothing about it on every plan."""
    import asyncio

    from orchestrator.sdlc.builddoc import build_plan

    monkeypatch.delenv("SPINE_DESIGN_COMPLETION")
    monkeypatch.delenv("SPINE_DESIGN_B165")
    _store_unused, spec = _tree(tmp_path)
    md = asyncio.run(build_plan(spec, root=tmp_path, issue_type="Bug"))
    seven = md.split("## 7. Files", 1)[1].split("## 8.", 1)[0]
    assert "did not run" not in seven and "**Read**" not in seven
