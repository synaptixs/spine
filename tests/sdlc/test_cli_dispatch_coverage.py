"""The third coverage state (B130): run by a test through a CLI command — neither covered nor a gap.

A typer/click handler has no ``CALLS`` from its test; ``CliRunner().invoke(app, ["cmd"])`` runs
it. Before, ``regression_gaps`` listed it under "add tests here". It must not, and it must not be
called *covered* either: the test may stop at a usage error, and the graph cannot show the
handler ran (D16). Every surface says the same words (D17, D22).
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.store import FactStore
from orchestrator.plugin.server import regression_gaps
from orchestrator.sdlc.coverage import (
    DISPATCH_LABEL,
    CoverageIndex,
    build_regression_plan,
    render_regression_plan_md,
)

_CLI = (
    "import typer\n\n"
    "app = typer.Typer()\n\n\n"
    "def do_work(n: int) -> int:\n    return n + 1\n\n\n"
    "def shared(n: int) -> int:\n    return n\n\n\n"
    "@app.command()\n"
    "def crunch(n: int = 1) -> None:\n    print(do_work(shared(n)))\n\n\n"
    "@app.command()\n"
    "def other() -> None:\n    pass\n"
)
_INVOKES = (
    "from typer.testing import CliRunner\n\n"
    "from tool import app, shared\n\n\n"
    "def test_crunch() -> None:\n"
    '    assert CliRunner().invoke(app, ["crunch", "--n", "2"]).exit_code == 0\n\n\n'
    "def test_shared() -> None:\n    assert shared(1) == 1\n"
)
_HELP_ONLY = (
    "from typer.testing import CliRunner\n\n"
    "from tool import app\n\n\n"
    'def test_help() -> None:\n    CliRunner().invoke(app, ["crunch", "--help"])\n'
)


def _store(tmp_path: Path, test_source: str, extra: dict[str, str] | None = None) -> FactStore:
    files = {"tool.py": _CLI, "tests/test_tool.py": test_source, **(extra or {})}
    for name, source in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source, encoding="utf-8")
    return FactStore(RepoCodeExtractor().extract(tmp_path))


def _item(plan: object, name: str) -> object:
    return next(i for i in plan.impacted if i.name == name)  # type: ignore[attr-defined]


# ---- build_regression_plan (D16, D22) ----------------------------------------


def test_a_handler_run_only_through_invoke_is_dispatch_reached_not_covered(tmp_path: Path) -> None:
    store = _store(tmp_path, _INVOKES)
    plan = build_regression_plan(store, "py:tool.crunch")
    assert plan.target_covered is False  # the CLI hop never counts as covering (D16)
    assert plan.covering_tests == []
    assert plan.target_dispatch_reached is True
    assert [t.split(" @ ")[0] for t in plan.dispatch_tests] == ["test_crunch"]
    md = render_regression_plan_md(plan)
    assert f"◐ {DISPATCH_LABEL}" in md and "no test exercises it" not in md


def test_a_callee_of_a_dispatched_handler_is_listed_as_dispatch_reached_not_a_gap(tmp_path: Path) -> None:
    store = _store(tmp_path, _INVOKES)
    plan = build_regression_plan(store, "py:tool.do_work")
    crunch = _item(plan, "crunch")
    assert crunch.covered is False and crunch.dispatch_reached is True and crunch.gap is False  # type: ignore[attr-defined]
    assert crunch.command == "CLI crunch"  # type: ignore[attr-defined]
    md = render_regression_plan_md(plan)
    gaps = md.split("## Regression gaps — add tests here")[1].split("##")[0]
    assert "`crunch`" not in gaps
    assert "(via `CLI crunch`)" in md


def test_without_an_invoke_the_handler_is_still_a_gap(tmp_path: Path) -> None:
    store = _store(tmp_path, "def test_nothing() -> None:\n    assert True\n")
    plan = build_regression_plan(store, "py:tool.do_work")
    crunch = _item(plan, "crunch")
    assert crunch.gap is True and crunch.dispatch_reached is False  # type: ignore[attr-defined]
    assert build_regression_plan(store, "py:tool.crunch").target_dispatch_reached is False


def test_help_only_invocation_leaves_the_handler_a_gap(tmp_path: Path) -> None:
    store = _store(tmp_path, _HELP_ONLY)
    assert _item(build_regression_plan(store, "py:tool.do_work"), "crunch").gap is True  # type: ignore[attr-defined]


def test_direct_coverage_still_wins_over_dispatch(tmp_path: Path) -> None:
    """`shared` is called by `test_shared` directly: covered, whatever the CLI hop says."""
    store = _store(tmp_path, _INVOKES)
    plan = build_regression_plan(store, "py:tool.shared")
    assert plan.target_covered is True and plan.target_dispatch_reached is False
    assert [t.split(" @ ")[0] for t in plan.covering_tests] == ["test_shared"]


def test_an_invoking_helper_counts_only_when_a_test_reaches_it(tmp_path: Path) -> None:
    """D21: the edge starts at the helper; the label needs a test that calls the helper."""
    helper = (
        "from typer.testing import CliRunner\n\nfrom tool import app\n\n\n"
        'def smoke() -> int:\n    return CliRunner().invoke(app, ["crunch"]).exit_code\n'
    )
    uncalled = _store(
        tmp_path / "a", "def test_nothing() -> None:\n    assert True\n", {"scripts/smoke.py": helper}
    )
    assert _item(build_regression_plan(uncalled, "py:tool.do_work"), "crunch").gap is True  # type: ignore[attr-defined]
    called = _store(
        tmp_path / "b",
        "from scripts.smoke import smoke\n\n\ndef test_smoke() -> None:\n    assert smoke() == 0\n",
        {"scripts/smoke.py": helper},
    )
    assert _item(build_regression_plan(called, "py:tool.do_work"), "crunch").dispatch_reached is True  # type: ignore[attr-defined]


# ---- CoverageIndex (D17) -------------------------------------------------------


def test_coverage_index_reports_the_third_state(tmp_path: Path) -> None:
    index = CoverageIndex(_store(tmp_path, _INVOKES))
    assert not index.is_covered("py:tool.crunch") and index.is_dispatch_reached("py:tool.crunch")
    assert not index.is_covered("py:tool.do_work") and index.is_dispatch_reached("py:tool.do_work")
    assert index.is_covered("py:tool.shared") and not index.is_dispatch_reached("py:tool.shared")
    assert not index.is_dispatch_reached("py:tool.other")  # no test runs `other`


# ---- regression_gaps MCP output (D22) ------------------------------------------


def test_regression_gaps_moves_dispatched_items_out_of_uncovered(tmp_path: Path) -> None:
    _store(tmp_path, _INVOKES)
    out = regression_gaps(str(tmp_path), symbol="py:tool.do_work")
    assert out["found"]
    assert all(u["name"] != "crunch" for u in out["uncovered"])
    assert out["dispatch_reached"] == [{"name": "crunch", "where": "tool.py:15", "command": "CLI crunch"}]
    target = regression_gaps(str(tmp_path), symbol="py:tool.crunch")
    assert target["target_covered"] is False and target["target_dispatch_reached"] is True
    assert target["dispatch_tests_count"] == 1 and len(target["dispatch_tests"]) == 1


# ---- the other surfaces say the same words (D17) -------------------------------


def test_episteme_safety_row_carries_the_label_not_the_warning(tmp_path: Path) -> None:
    from orchestrator.knowledge.renderers import _coverage_note

    index = CoverageIndex(_store(tmp_path, _INVOKES))
    assert _coverage_note(index, "py:tool.crunch") == f" · {DISPATCH_LABEL}"
    assert _coverage_note(index, "py:tool.shared") == ""
    assert _coverage_note(index, "py:tool.other") == " · **no test path visible**"


def test_builddoc_evidence_counts_dispatched_apart_from_untested() -> None:
    from orchestrator.sdlc.builddoc import _evidence_block

    text = _evidence_block(
        {"call_graph_available": True, "covered": ["a"], "uncovered": ["b"], "dispatched": ["crunch"]}
    )
    assert "1 of 3 symbol(s)" in text and "untested: `b`" in text
    assert f"1 more are {DISPATCH_LABEL}: `crunch`" in text


def test_a_landing_run_through_a_command_is_not_called_unreached() -> None:
    from orchestrator.sdlc.landings import Landing, render_landing

    hit = Landing(
        name="crunch",
        where="tool.py:15",
        kind="Function",
        callers=0,
        module="tool",
        covered=False,
        dispatch_reached=True,
    )
    line = render_landing(hit)
    assert DISPATCH_LABEL in line and "no test reaches this" not in line


def test_ideation_code_items_do_not_call_a_dispatched_landing_unreached() -> None:
    from orchestrator.sdlc.ideation import _code_items
    from orchestrator.sdlc.landings import Landing

    base = {"where": "tool.py:15", "kind": "Function", "callers": 0, "module": "tool", "covered": False}
    dispatched = _code_items([Landing(name="crunch", dispatch_reached=True, **base)])  # type: ignore[arg-type]
    untested = _code_items([Landing(name="other", **base)])  # type: ignore[arg-type]
    assert "no test reaches it" not in dispatched[0] and "no test reaches it" in untested[0]
