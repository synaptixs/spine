"""The context-budget gate weighs the files the design will propose, not a guess at them (SSPN-231).

Codegen is shown the design's files. In the autorun path the design is deterministic
(`_stage_design` -> `produce_design(llm=None)` -> `_fallback_design`), so the gate can ask the same
function instead of estimating with `landings.design_file_set`. On 40 fresh benchmark runs the proxy
parked five tickets at 1.5x-2.6x the budget although, for four of them, the files the design would
have proposed were small or fit alone; and it never counted a file the ticket names, which the plan
(`build_plan`) and the design itself both do.

Three groups: the helper is the design's own file set; the run and the plan reach one verdict that
matches it; and the refusal line sits at 2.0x, because designs of 1.85x and 1.9x the budget resolved
when they ran with excerpting (SSPN-159 left that line to be re-tested; the sweep is in SSPN-231).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from orchestrator.pkg.store import FactStore
from orchestrator.sdlc.autorun import AutorunError, RunContext, _stage_investigate, _stage_validity
from orchestrator.sdlc.builddoc import build_plan
from orchestrator.sdlc.validity import Verdict, assess
from tests.sdlc.test_landing_set import BUDGET, TICKET, _repo

# Per-file bytes. Twelve files that each match the ticket's words, so the proxy's first five are
# 5 x BIG = 2.5x the budget: over the refusal line at both 1.5x and 2.0x.
SMALL, BIG = 5_000, 100_000
NAMED = "small_named.py"


def _named_small(tmp_path: Path) -> None:
    (tmp_path / NAMED).write_text("def tiny() -> int:\n    return 0\n", encoding="utf-8")


def _named_huge(tmp_path: Path, name: str = "named_huge.py", lines: int = 7_000) -> None:
    """~80 bytes a line: 7,000 lines is 560 KB, 2.8x the budget on its own."""
    (tmp_path / name).write_text("# " + "x" * 78 + "\n" + ("#" * 79 + "\n") * lines, encoding="utf-8")


def _spec(named: str | None = None) -> dict[str, str]:
    summary = TICKET if named is None else f"{TICKET} — the change is in {named}"
    return {"title": TICKET, "summary": summary}


def _ctx(tmp_path: Path, spec: dict[str, str]) -> RunContext:
    return RunContext(
        run_id="r-gate",
        source="file://spec.md",
        live=False,
        root=tmp_path,
        artifacts_dir=tmp_path / "artifacts-gate",
        issue_key="SPN-1",
        spec=spec,
        approvals_dir=tmp_path / "approvals-gate",
    )


def _run_verdict(tmp_path: Path, store: FactStore, spec: dict[str, str]) -> str:
    """The verdict `_stage_validity` reaches after the investigate stage; a refusal parks and raises."""
    ctx = _ctx(tmp_path, spec)
    _stage_investigate(ctx, store=store, emit=lambda _s: None)
    with contextlib.suppress(AutorunError):
        _stage_validity(ctx, store=store, issue_type="Bug", emit=lambda _s: None)
    return ctx.verdict


# --- the helper is the design's own file set ----------------------------------------------------


def test_the_gate_file_set_is_exactly_what_the_design_proposes(tmp_path: Path) -> None:
    from orchestrator.sdlc.design import _fallback_design, gate_file_set

    store = _repo(tmp_path, per_file=1, pad_bytes=SMALL)
    _named_small(tmp_path)
    spec = _spec(NAMED)

    got = gate_file_set(spec, store=store, root=tmp_path)

    assert got == tuple(_fallback_design(spec, None, store, tmp_path)["files_to_touch"])
    assert got[0] == NAMED, "a path the ticket names outranks the files its words match"


def test_with_no_named_path_the_gate_file_set_is_the_landing_files_the_design_takes(tmp_path: Path) -> None:
    from orchestrator.sdlc.design import _fallback_design, gate_file_set

    store = _repo(tmp_path, per_file=1, pad_bytes=SMALL)

    got = gate_file_set(_spec(), store=store, root=tmp_path)

    assert got == tuple(_fallback_design(_spec(), None, store, tmp_path)["files_to_touch"])
    assert 1 <= len(got) <= 5, "the design proposes at most five landing files"


# --- the run and the plan weigh what the design will propose ------------------------------------


def test_the_run_does_not_refuse_a_small_named_file_for_the_big_files_its_words_match(tmp_path: Path) -> None:
    store = _repo(tmp_path, per_file=1, pad_bytes=BIG)
    _named_small(tmp_path)

    assert _run_verdict(tmp_path, store, _spec(NAMED)) == "PROCEED"


async def test_the_plan_reaches_the_same_verdict_for_a_small_named_file(tmp_path: Path) -> None:
    _repo(tmp_path, per_file=1, pad_bytes=BIG)
    _named_small(tmp_path)

    plan = await build_plan(_spec(NAMED), root=tmp_path, issue_type="Bug")

    assert "**Validity:** PROCEED" in plan


def test_the_run_weighs_a_huge_file_the_ticket_names_as_the_plan_already_does(tmp_path: Path) -> None:
    """Today the run measures only the ranked rows and so passes a ticket whose own file is 2.8x the
    budget; the plan has always added the named path (`build_plan`), so the two disagreed."""
    store = _repo(tmp_path, per_file=1, pad_bytes=SMALL)
    _named_huge(tmp_path)

    assert _run_verdict(tmp_path, store, _spec("named_huge.py")) == "TOO_BIG"


def test_a_ticket_that_names_nothing_and_matches_small_files_still_proceeds(tmp_path: Path) -> None:
    """Unchanged behaviour the new gate must keep."""
    store = _repo(tmp_path, per_file=1, pad_bytes=SMALL)

    assert _run_verdict(tmp_path, store, _spec()) == "PROCEED"


def test_a_bare_file_name_in_the_ticket_is_resolved_under_the_repo_root(tmp_path: Path) -> None:
    """`EBSOrderApiClient.cs` with no directory is how a ticket names a file; the design resolves it to
    its one location under the root, so the gate must be given the root or it measures a path that
    does not exist and passes a ticket whose own file is 2.8x the budget."""
    store = _repo(tmp_path, per_file=1, pad_bytes=SMALL)
    (tmp_path / "pkg").mkdir()
    _named_huge(tmp_path / "pkg", "deep_named.py")

    assert _run_verdict(tmp_path, store, _spec("deep_named.py")) == "TOO_BIG"


def test_when_the_design_proposes_nothing_the_gate_keeps_the_old_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ten files of 45 KB: the old estimate (the first five, 1.1x) proceeds; falling through to the
    whole landing (ten files, 2.25x) would refuse. An empty design must not change that."""
    import orchestrator.sdlc.design as design_module

    store = _repo(tmp_path, per_file=1, pad_bytes=45_000)
    monkeypatch.setattr(design_module, "gate_file_set", lambda *a, **k: ())

    assert _run_verdict(tmp_path, store, _spec()) == "PROCEED"


# --- the refusal line ---------------------------------------------------------------------------


def _one_file_over(tmp_path: Path, pad_bytes: int) -> tuple[FactStore, list[str]]:
    store = _repo(tmp_path, files=1, per_file=1, pad_bytes=pad_bytes)
    return store, ["f00.py"]


def test_the_refusal_line_is_two_times_the_budget() -> None:
    from orchestrator.sdlc.validity import _CONTEXT_REFUSE_RATIO

    assert _CONTEXT_REFUSE_RATIO == 2.0


def test_a_design_between_1_5x_and_2x_the_budget_is_warned_not_refused(tmp_path: Path) -> None:
    """1.8x: designs of 1.85x and 1.9x resolved when they ran with excerpting."""
    store, files = _one_file_over(tmp_path, int(1.8 * BUDGET))

    result = assess(
        _spec(),
        store=store,
        landing=files,
        context_files=files,
        issue_type="Bug",
        root=tmp_path,
        context_budget=BUDGET,
    )

    assert result.verdict is Verdict.PROCEED
    warning = next(f for f in result.findings if f.check == "context-budget")
    assert "so files will be excerpted" in warning.detail


def test_a_design_past_2x_the_budget_is_still_refused(tmp_path: Path) -> None:
    store, files = _one_file_over(tmp_path, int(2.2 * BUDGET))

    result = assess(
        _spec(),
        store=store,
        landing=files,
        context_files=files,
        issue_type="Bug",
        root=tmp_path,
        context_budget=BUDGET,
    )

    assert result.verdict is Verdict.TOO_BIG


def test_a_design_of_exactly_twice_the_budget_is_refused(tmp_path: Path) -> None:
    """The line is inclusive: `>=`, as before. 400,000 bytes on the nose is refused."""
    store = _repo(tmp_path, files=1, per_file=1, pad_bytes=SMALL)
    (tmp_path / "exact.py").write_bytes(b"#" * (2 * BUDGET))

    result = assess(
        _spec(),
        store=store,
        landing=["exact.py"],
        context_files=["exact.py"],
        issue_type="Bug",
        root=tmp_path,
        context_budget=BUDGET,
    )

    assert result.verdict is Verdict.TOO_BIG
