"""The context-budget gate measures what codegen is shown, not the landing set (SSPN-185 / B89).

Codegen is shown the files the *design* proposes — one to six, median two over 27 recorded runs.
The gate used to weigh the landing set, which is the files of the first ten non-weak rows. Since the
investigation lists one row per file, ten rows are ten files: on a live run (`django__django-14140`,
2026-10-16) that was 328,788 bytes, 1.64x the budget, and the run was parked — although the first
five files, the size of a design, were 1.08x and a run of the same problem had reached codegen.

The gate runs before the design, and the design is a model's choice, so what it measures is a
*proxy* for the design's files: `landings.design_file_set`. These tests pin the proxy, the keyword
`assess` takes for it, and that the plan and both modes of the run give one verdict.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from orchestrator.pkg.store import FactStore
from orchestrator.sdlc.autorun import AutorunError, RunContext, _stage_investigate, _stage_validity
from orchestrator.sdlc.builddoc import build_plan
from orchestrator.sdlc.evidence import _tool_investigate, evidence_from_parts
from orchestrator.sdlc.landings import Landing
from orchestrator.sdlc.validity import Verdict, assess
from tests.sdlc.test_landing_set import BUDGET, TICKET, _repo

# Twelve files, one matching symbol each: the investigation lists ten of them, one row per file, so
# the landing set is ten files. ``pad_bytes`` sizes every file the same, which makes the verdict a
# function of how many files are measured and nothing else.
SMALL, TEN_FILES, HUGE = 5_000, 45_000, 70_000  # per-file bytes: 10 files fit / 10 over, 5 fit / 5 over


def _row(where: str, *, weak: bool = False) -> Landing:
    return Landing(name=where, where=where, kind="Function", callers=0, module="m", weak=weak)


def _ctx(tmp_path: Path, tag: str) -> RunContext:
    """A run over the repo at ``tmp_path``; ``tag`` keeps two runs' artifacts and approvals apart."""
    return RunContext(
        run_id=f"r-{tag}",
        source="file://spec.md",
        live=False,
        root=tmp_path,
        artifacts_dir=tmp_path / f"artifacts-{tag}",
        issue_key="SPN-1",
        spec={"title": TICKET, "summary": TICKET},
        approvals_dir=tmp_path / f"approvals-{tag}",
    )


def _run_verdict(ctx: RunContext, store: FactStore) -> str:
    """The verdict `_stage_validity` reaches; a refusal parks the run and raises."""
    with contextlib.suppress(AutorunError):
        _stage_validity(ctx, store=store, issue_type="Bug", emit=lambda _s: None)
    return ctx.verdict


# --- the proxy ------------------------------------------------------------------------------


def test_the_proxy_is_the_files_of_the_first_eight_non_weak_rows_at_most_five() -> None:
    from orchestrator.sdlc.landings import design_file_set

    rows = [_row(f"{n}.py:1") for n in "abcdefghij"]  # ten rows, ten files
    assert design_file_set(rows) == ("a.py", "b.py", "c.py", "d.py", "e.py")


def test_the_proxy_skips_weak_rows_without_counting_them() -> None:
    from orchestrator.sdlc.landings import design_file_set

    rows = [_row("w1.py:1", weak=True), _row("a.py:1"), _row("w2.py:1", weak=True), _row("b.py:1")]
    assert design_file_set(rows) == ("a.py", "b.py")
    assert design_file_set([_row("only.py:1", weak=True)]) == ()


def test_the_proxy_reads_rows_not_files_so_a_file_with_many_rows_uses_up_the_eight() -> None:
    from orchestrator.sdlc.landings import design_file_set

    rows = [_row(f"a.py:{i}") for i in range(1, 8)] + [_row("b.py:1"), _row("c.py:1")]
    # Eight rows: seven in a.py and the first of b.py. c.py is the ninth row and is not read.
    assert design_file_set(rows) == ("a.py", "b.py")


def test_the_proxy_keeps_first_seen_order_each_file_once() -> None:
    from orchestrator.sdlc.landings import design_file_set

    assert design_file_set([_row("z.py:1"), _row("a.py:1"), _row("z.py:9")]) == ("z.py", "a.py")


def test_the_proxy_accepts_graph_mode_facts_that_carry_no_weak_flag() -> None:
    """Graph mode drops weak rows before it builds `LandingFact`, which has no `weak` field."""
    from orchestrator.sdlc.evidence import LandingFact
    from orchestrator.sdlc.landings import design_file_set

    facts = [LandingFact(name="n", where=f"{n}.py:1", kind="Function", callers=0, module="m") for n in "abc"]
    assert design_file_set(facts) == ("a.py", "b.py", "c.py")


# --- the gate -------------------------------------------------------------------------------


def test_assess_measures_the_context_files_when_it_is_given_them(tmp_path: Path) -> None:
    store = _repo(tmp_path, per_file=1, pad_bytes=TEN_FILES)
    ten = [f"f{i:02d}.py" for i in range(10)]
    spec = {"title": TICKET, "summary": TICKET}

    whole = assess(spec, store=store, landing=ten, issue_type="Bug", root=tmp_path, context_budget=BUDGET)
    assert whole.verdict is Verdict.TOO_BIG, "premise, and the unchanged behaviour without the keyword"

    cut = assess(
        spec,
        store=store,
        landing=ten,
        context_files=ten[:5],
        issue_type="Bug",
        root=tmp_path,
        context_budget=BUDGET,
    )
    assert cut.verdict is Verdict.PROCEED
    warning = next(f for f in cut.findings if f.check == "context-budget")
    assert "5 file(s)" in warning.detail, "the finding names how many files were measured"


def test_assess_still_refuses_when_the_context_files_alone_are_over(tmp_path: Path) -> None:
    store = _repo(tmp_path, per_file=1, pad_bytes=HUGE)
    five = [f"f{i:02d}.py" for i in range(5)]

    result = assess(
        {"title": TICKET, "summary": TICKET},
        store=store,
        landing=five,
        context_files=five,
        issue_type="Bug",
        root=tmp_path,
        context_budget=BUDGET,
    )
    assert result.verdict is Verdict.TOO_BIG


def test_an_empty_context_set_is_measured_as_empty_not_as_the_landing_set(tmp_path: Path) -> None:
    """`None` means "not given"; an empty tuple means the proxy found nothing to measure."""
    store = _repo(tmp_path, per_file=1, pad_bytes=HUGE)
    ten = [f"f{i:02d}.py" for i in range(10)]

    result = assess(
        {"title": TICKET, "summary": TICKET},
        store=store,
        landing=ten,
        context_files=(),
        issue_type="Bug",
        root=tmp_path,
        context_budget=BUDGET,
    )
    assert not any(f.check == "context-budget" for f in result.findings)


# --- the run, in both modes, and the plan ----------------------------------------------------


@pytest.mark.parametrize(
    ("pad_bytes", "verdict"),
    [(SMALL, "PROCEED"), (TEN_FILES, "PROCEED"), (HUGE, "TOO_BIG")],
    ids=["ten files fit", "ten files over, first five fit", "first five over"],
)
async def test_the_plan_and_both_modes_of_the_run_reach_one_verdict(
    tmp_path: Path, pad_bytes: int, verdict: str
) -> None:
    store = _repo(tmp_path, per_file=1, pad_bytes=pad_bytes)

    fallback = _ctx(tmp_path, "fallback")
    _stage_investigate(fallback, store=store, emit=lambda _s: None)
    assert len(fallback.landing) == 10, "premise: one row per file, so the landing set is ten files"

    graph = _ctx(tmp_path, "graph")
    rows = _tool_investigate(store=store, title=TICKET, problem=TICKET, root=tmp_path)["landing"]
    graph.evidence = evidence_from_parts(
        title=TICKET, problem=TICKET, issue_type="Bug", investigate={"landing": rows}, rca={}, blast={}
    )
    _stage_investigate(graph, store=store, emit=lambda _s: None)
    assert len(graph.landing) == 10

    plan = await build_plan({"title": TICKET, "summary": TICKET}, root=tmp_path, issue_type="Bug")

    assert _run_verdict(fallback, store) == verdict
    assert _run_verdict(graph, store) == verdict
    assert f"**Validity:** {verdict}" in plan


# --- what the proxy must not loosen -------------------------------------------------------------


async def test_a_file_the_ticket_names_is_still_measured_in_the_plan(tmp_path: Path) -> None:
    """A path in the ticket is shown to codegen whatever the ranking says, so the plan keeps weighing it;
    the design-sized proxy replaces the ranked rows, not the files the ticket names."""
    _repo(tmp_path, per_file=1, pad_bytes=SMALL)  # the ranked files, all small
    (tmp_path / "named_huge.py").write_text(
        "# " + "x" * 78 + "\n" * 1 + ("#" * 79 + "\n") * 5_000, encoding="utf-8"
    )
    spec = {"title": TICKET, "summary": f"{TICKET} — the change is in named_huge.py"}

    plan = await build_plan(spec, root=tmp_path, issue_type="Bug")

    assert "**Validity:** TOO_BIG" in plan


def test_with_no_landing_rows_the_gate_weighs_the_landing_as_before(tmp_path: Path) -> None:
    """No rows means nothing to estimate the design from: the gate falls back to today's behaviour
    rather than treating "no estimate" as "nothing to weigh"."""
    store = _repo(tmp_path, files=5, per_file=1, pad_bytes=HUGE)
    ctx = _ctx(tmp_path, "norows")
    ctx.landing = [f"f{i:02d}.py" for i in range(5)]  # five files of HUGE: over the refuse ratio together
    assert ctx.landing_facts == []

    assert _run_verdict(ctx, store) == "TOO_BIG"
