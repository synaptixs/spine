"""G2 PR-B, phase B3: §12 scores what the plan knows, and the wording stops overclaiming.

B176 (D5), B167, B184 (D9), B170. Synthetic fixtures; the CB-528 shape is a UI task whose design names
only a service file, on the fallback template, with no test file listed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.builddoc import _blast_prose, _confidence_block, render_build_md
from tests.sdlc.test_builddoc import _Assessment, _design, _Investigation, _spec


def _section(md: str, title: str) -> str:
    return md.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def _signals(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "verdict": "PROCEED",
        "brief_agrees": True,
        "same_reading": False,
        "root_cause": False,
        "fault_site": False,
        "unverified": False,
        "over_budget": False,
        "untested": 0,
        "stated_criteria": True,
        "named_elsewhere": False,
        "layout_would_stop": False,
        "no_files": False,
    }
    base.update(over)
    return base


# ---- B176 (D5): caps with printed reasons ------------------------------------------------------------------


def test_b176_every_check_positive_is_still_high_without_a_cap() -> None:
    block = _confidence_block(signals=_signals(), journey=[])

    assert "**Is the analysis right? — high**" in block
    assert "At most medium" not in block


@pytest.mark.parametrize(
    ("signal", "reason"),
    [
        ("template", "§6 is the fallback template"),
        ("no_kind_file", "no file of the ticket's kind is listed"),
        ("tests_unlisted", "the plan asks for tests and lists no test file"),
    ],
)
def test_b176_each_cap_holds_high_to_medium_and_prints_its_reason(signal: str, reason: str) -> None:
    block = _confidence_block(signals=_signals(**{signal: True}), journey=[])

    assert "**Is the analysis right? — medium**" in block
    assert "**At most medium:**" in block
    assert reason in block


def test_b176_a_low_band_stays_low_and_still_says_why_it_could_not_be_high() -> None:
    block = _confidence_block(
        signals=_signals(
            verdict="TOO_BIG", brief_agrees=False, unverified=True, over_budget=True, template=True
        ),
        journey=[],
    )

    assert "— low**" in block
    assert "§6 is the fallback template" in block


def test_b176_the_cb528_shape_scores_at_most_medium_with_three_reasons(tmp_path: Path) -> None:
    (tmp_path / "src/services").mkdir(parents=True)
    (tmp_path / "src/services/session.ts").write_text("export function signIn() {}\n", encoding="utf-8")
    spec = _spec(
        title="Show a spinner while signing in",
        summary="Show a loading spinner on the sign-in and sign-up screens while the request runs.",
        acceptance_criteria=["A spinner shows while the request runs.", "Add a test for the spinner."],
    )
    from orchestrator.sdlc.design import template_approach

    design = _design(
        approach=template_approach(spec["title"]),
        files_to_touch=["src/services/session.ts"],
        test_strategy="Add tests covering each acceptance criterion.",
    )
    md = render_build_md(
        spec,
        investigation=_Investigation(),
        design=design,
        validity=_Assessment(),
        root=tmp_path,
        commit="abc1234",
        context_budget=200_000,
        evidence={"listed_tests": [], "ui_listed": [], "ui_in_graph": 2},
        source_text="Show a loading spinner on the sign-in and sign-up screens while the request runs.",
    )
    confidence = _section(md, "12. Confidence")

    assert "— high**" not in confidence
    for reason in (
        "§6 is the fallback template",
        "no file of the ticket's kind is listed",
        "the plan asks for tests and lists no test file",
    ):
        assert reason in confidence


def test_d6_a_ticket_that_does_not_read_as_ui_gets_no_kind_cap(tmp_path: Path) -> None:
    md = render_build_md(
        _spec(title="Speed up the price lookup", summary="The price lookup re-reads the table."),
        investigation=_Investigation(),
        design=_design(),
        validity=_Assessment(),
        root=tmp_path,
        commit="abc1234",
        context_budget=200_000,
        evidence={"ui_listed": [], "ui_in_graph": 3},
    )

    assert "ticket's kind" not in md


def test_b176_a_listed_test_file_lifts_the_tests_cap(tmp_path: Path) -> None:
    md = render_build_md(
        _spec(),
        investigation=_Investigation(),
        design=_design(test_strategy="Add tests covering each acceptance criterion."),
        validity=_Assessment(),
        root=tmp_path,
        commit="abc1234",
        context_budget=200_000,
        evidence={"listed_tests": ["tests/test_a.py"]},
    )

    assert "lists no test file" not in md


def test_b176_a_model_written_design_is_not_the_template(tmp_path: Path) -> None:
    from orchestrator.sdlc.design import template_approach

    md = render_build_md(
        _spec(),
        investigation=_Investigation(),
        design=_design(approach=template_approach("A ticket"), llm=True),
        validity=_Assessment(),
        root=tmp_path,
        commit="abc1234",
        context_budget=200_000,
    )

    assert "fallback template" not in md


def test_b176_the_fallback_design_uses_the_template_function() -> None:
    from orchestrator.sdlc.design import _fallback_design, template_approach

    design = _fallback_design({"title": "Speed up the cart", "summary": "x"}, None)
    assert design["approach"] == template_approach("Speed up the cart")


# ---- B167: each validity verdict is read as itself ---------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "reading"),
    [
        ("TOO_BIG", "TOO_BIG — larger than codegen's context budget"),
        ("UNLOCALIZED", "UNLOCALIZED — a bug that resolves to no symbol in the graph"),
        ("ALREADY_DONE", "ALREADY_DONE — the graph says it already exists"),
        ("DUPLICATE", "DUPLICATE — another run already covered it"),
        ("CRITERIA_WRONG", "CRITERIA_WRONG — the ticket disagrees with the code"),
        (None, "unknown — no verdict was recorded"),
    ],
)
def test_b167_each_verdict_reads_as_itself(verdict: Any, reading: str) -> None:
    block = _confidence_block(signals=_signals(verdict=verdict), journey=[])

    assert f"| Validity gate | {reading} | − |" in block


def test_b167_too_big_never_says_the_ticket_disagrees() -> None:
    block = _confidence_block(signals=_signals(verdict="TOO_BIG"), journey=[])

    assert "disagrees" not in block


# ---- B184 (D9): floor wording, elided keyword rows, §9 not computed ---------------------------------------


def _bd(**over: Any) -> dict[str, Any]:
    bd: dict[str, Any] = {
        "grounded": True,
        "call_graph_available": True,
        "modules": [{"ref": "src/screens/detail.tsx", "importers": 0, "importer_names": [], "hotspots": []}],
        "unverified_references": [],
        "languages": ["typescript"],
    }
    bd.update(over)
    return bd


def test_b184_no_traced_importer_is_a_floor_not_a_proof() -> None:
    prose = _blast_prose(_bd(), "typescript")

    assert "cannot propagate outward" not in prose
    assert "no traced importer" in prose
    assert "navigation, string registration and dynamic imports are not traced" in prose


class _Row:
    def __init__(
        self, name: str, where: str, callers: int = 0, found_by: str = "", cross_repo: int = 0
    ) -> None:
        self.name, self.where, self.callers, self.found_by, self.cross_repo = (
            name,
            where,
            callers,
            found_by,
            cross_repo,
        )
        self.kind, self.module, self.weak = "Function", "m", False


def test_b184_keyword_only_rows_with_no_caller_and_no_link_are_dropped_and_counted(tmp_path: Path) -> None:
    rows = [
        _Row("searchQuery", "src/search/query.ts:4"),  # keyword-only, 0 callers, not a design file
        _Row("onSearch", "src/home/bar.tsx:9"),  # likewise
        _Row("helper", "src/lib/util.py:10", callers=3),  # has callers, outside the design: kept
        _Row("Detail", "src/a.py:2"),  # in a design file: kept
        _Row(
            "wired", "src/other.ts:7", found_by="graph via route → CALLS"
        ),  # reached through the graph: kept
    ]
    md = render_build_md(
        _spec(),
        investigation=_Investigation(rows),  # type: ignore[arg-type]
        design=_design(files_to_touch=["src/a.py"]),
        validity=_Assessment(),
        root=tmp_path,
        commit="abc1234",
        context_budget=200_000,
    )
    pkg = _section(md, "4. PKG — what the graph knows")

    assert "`searchQuery`" not in pkg and "`onSearch`" not in pkg
    assert "`helper`" in pkg and "`Detail`" in pkg and "`wired`" in pkg
    assert "2 keyword-only row(s) with no caller and no link to the files being changed were dropped" in pkg


def test_b184_when_every_row_is_dropped_the_section_says_so(tmp_path: Path) -> None:
    md = render_build_md(
        _spec(),
        investigation=_Investigation([_Row("searchQuery", "src/search/query.ts:4")]),  # type: ignore[list-item]
        design=_design(files_to_touch=["src/a.py"]),
        validity=_Assessment(),
        root=tmp_path,
        commit="abc1234",
        context_budget=200_000,
    )
    pkg = _section(md, "4. PKG — what the graph knows")

    assert "Nothing in the graph matched" not in pkg
    assert "1 keyword-only row(s)" in pkg
    assert "| symbol |" not in pkg


def test_b184_section_9_says_not_computed(tmp_path: Path) -> None:
    from tests.sdlc.test_builddoc import _render

    nine = _section(_render(tmp_path), "9. Facts the generator needs")

    assert "Not computed:" in nine
    assert "no phase owns this yet" not in nine


# ---- B170: the brief does not repeat the title under Problem -----------------------------------------------


def test_b170_the_sources_own_title_heading_is_not_repeated() -> None:
    from orchestrator.sdlc.investigate import Investigation, render_investigation_md

    title = "TCK-9: Cart total is wrong"
    inv = Investigation(
        title=title, problem=f"## {title}\n# {title}\n\nIssue type: Bug\n\nThe total is off by one."
    )
    md = render_investigation_md(inv)
    problem = md.split("## Problem", 1)[1].split("\n## ", 1)[0]

    assert md.count(title) == 1
    assert problem.lstrip().startswith("Issue type: Bug")


def test_b170_a_different_heading_is_kept() -> None:
    from orchestrator.sdlc.investigate import Investigation, render_investigation_md

    inv = Investigation(title="TCK-9: Cart", problem="## Background\nThe total is off by one.")
    md = render_investigation_md(inv)

    assert "## Background" in md
