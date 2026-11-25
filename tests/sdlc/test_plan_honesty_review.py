"""G2 PR-B review fixes: the stated-cause reader, §3's consequence line, the UI fact and two regexes.

Each test pins one defect the maintainer review of the PR reproduced. Synthetic fixtures throughout.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.builddoc import _root_cause_block, collect_evidence
from orchestrator.sdlc.design import ui_kind_ticket
from orchestrator.sdlc.investigate import _without_title_headings
from orchestrator.sdlc.rca import build_rca, origin_words, render_rca_md, stated_causes


def _store(*files: str) -> FactStore:
    b = FactBatch()
    for i, f in enumerate(files):
        b.add_node(Node(f"m:{i}", NodeKind.MODULE, f, "python", Provenance(f, 1, 90)))
    return FactStore(b)


# ---- 1: a heading inside a code block is code, not the reporter's section ----------------------------------


def test_a_heading_inside_a_fenced_block_is_not_a_stated_section() -> None:
    text = (
        "## Summary\nCharging fails.\n\n```python\n# Fix\ncharge(order)  # see billing/charge.py\n```\n"
        "Then it raises.\n"
    )

    assert stated_causes(text) == []


def test_a_heading_inside_a_jira_code_macro_is_not_a_stated_section() -> None:
    text = "h2. Summary\nCharging fails.\n{code:python}\n# Fix\ncharge(order)\n{code}\nThen it raises.\n"

    assert stated_causes(text) == []


def test_a_fenced_block_inside_a_stated_section_is_kept_whole() -> None:
    text = "## Root cause\nThe guard is inverted:\n```python\n# wrong\nif not ok:\n```\n## Notes\nnone\n"

    [(heading, body)] = stated_causes(text)

    assert heading == "Root cause"
    assert body.endswith("if not ok:\n```")


async def test_a_fenced_comment_never_becomes_the_stated_fault_file() -> None:
    text = "## Summary\nCharging fails.\n```python\n# Fix\ncharge(order)  # see billing/charge.py\n```\n"

    report = await build_rca(text, store=_store("billing/charge.py", "billing/order.py"), source_text=text)

    assert report.fault_origin != "stated"
    assert not any(h.confidence == "stated" for h in report.hypotheses)


# ---- 2: an ambiguous basename, a cause section before a fix section, a proposed test file ------------------


async def test_an_ambiguous_basename_is_not_claimed_as_the_stated_fault_file() -> None:
    text = "## Root cause\nThe check in utils.py is inverted.\n"

    report = await build_rca(text, store=_store("src/api/utils.py", "src/web/utils.py"), source_text=text)

    assert report.fault_origin != "stated"
    assert report.fault_origin != "stated-fix"


async def test_a_cause_section_names_the_fault_file_before_a_fix_section() -> None:
    text = (
        "## Fix\nGuard the call in src/api/handler.py.\n\n"
        "## Root cause\nsrc/cart/total.py rounds too early.\n"
    )

    report = await build_rca(text, store=_store("src/api/handler.py", "src/cart/total.py"), source_text=text)

    assert report.fault_module == "src/cart/total.py"
    assert report.fault_origin == "stated"


async def test_a_test_file_a_fix_section_proposes_is_not_the_fault_file() -> None:
    text = "## Proposed fix\nAdd a case to tests/test_total.py for a zero quantity.\n"

    report = await build_rca(text, store=_store("tests/test_total.py", "src/cart/total.py"), source_text=text)

    # Not claimed as the stated fault. (The older "a file the ticket names" route may still show it as
    # named by the ticket — that route predates this PR and says only that the ticket names it.)
    assert report.fault_origin not in ("stated", "stated-fix")


async def test_a_file_only_a_fix_section_names_says_fix_section() -> None:
    text = "## Fix\nClamp the quantity in src/cart/total.py.\n"

    report = await build_rca(text, store=_store("src/cart/total.py", "src/cart/item.py"), source_text=text)

    assert report.fault_module == "src/cart/total.py"
    assert report.fault_origin == "stated-fix"
    assert origin_words("stated-fix") == "named in the ticket's own fix section"
    block = _root_cause_block(report)
    assert "fix section" in block
    assert "root-cause section" not in block


# ---- 3: "the ticket states a cause" only when it does ------------------------------------------------------


def test_an_exception_with_no_module_and_no_stated_cause_does_not_claim_a_stated_cause() -> None:
    report = SimpleNamespace(exception="ValueError: bad", fault_site="", fault_module="", hypotheses=[])

    block = _root_cause_block(report)

    assert "states a cause" not in block
    assert "`ValueError: bad`" in block
    assert "``" not in block.replace("`ValueError: bad`", "")  # no empty module in backticks


def test_a_stated_cause_with_no_graph_file_still_says_so() -> None:
    hyp = SimpleNamespace(confidence="stated", claim="The ticket's own section:", quote="x", evidence=())
    report = SimpleNamespace(exception="", fault_site="", fault_module="", hypotheses=[hyp])

    assert "the ticket states a cause, and no file it names is in the graph" in _root_cause_block(report)


# ---- 4: the section cap says what it dropped ---------------------------------------------------------------


async def test_sections_past_the_cap_are_counted_not_dropped_silently() -> None:
    text = "".join(
        f"## {h}\nbody {i}\n\n" for i, h in enumerate(("Cause", "Analysis", "Fix", "Proposed fix"))
    )

    report = await build_rca(text, store=_store("src/a.py"), source_text=text)
    stated = [h for h in report.hypotheses if h.confidence == "stated"]

    assert len(stated) == 3
    assert "3 of 4" in stated[-1].claim
    assert "3 of 4" in render_rca_md(report)


async def test_three_sections_or_fewer_carry_no_cap_note() -> None:
    text = "## Cause\nbody\n\n## Fix\nbody\n"

    report = await build_rca(text, store=_store("src/a.py"), source_text=text)

    assert not any(" of " in h.claim and "sections" in h.claim for h in report.hypotheses)


# ---- 6: a test file holding JSX is not a listed UI module --------------------------------------------------


def test_a_test_file_with_jsx_is_not_a_listed_or_counted_ui_module(tmp_path: Path) -> None:
    b = FactBatch()
    b.add_node(
        Node("m:0", NodeKind.MODULE, "login", "typescript", Provenance("src/login.tsx", 1, 9), ui=True)
    )
    b.add_node(
        Node(
            "m:1",
            NodeKind.MODULE,
            "login.test",
            "typescript",
            Provenance("src/login.test.tsx", 1, 9),
            ui=True,
            test=True,
        )
    )

    ev = collect_evidence(FactStore(b), files=["src/login.test.tsx"], root=tmp_path)

    assert ev["ui_listed"] == []
    assert ev["ui_in_graph"] == 1


# ---- 7: backend wording is not UI work ---------------------------------------------------------------------


def test_backend_wording_is_not_a_ui_ticket() -> None:
    for text in (
        "Fix the config loader crash on start",
        "Escape SQL placeholders in the report query",
        "The intake service screens applicants by age",
    ):
        assert not ui_kind_ticket(text), text


def test_ui_wording_still_reads_as_ui() -> None:
    for text in (
        "Show a spinner while signing in",
        "The sign-in screen freezes",
        "Nothing happens on the screen after submit",
        "Add a loading indicator to checkout",
    ):
        assert ui_kind_ticket(text), text


# ---- 8: no catastrophic backtracking in the heading regexes ------------------------------------------------


def test_a_long_run_of_spaces_in_a_heading_line_parses_fast() -> None:
    line = "# a" + " " * 20_000 + "b"
    started = time.perf_counter()
    stated_causes(line + "\n## Root cause\nx\n")
    _without_title_headings(line + "\nbody", "a b")
    assert time.perf_counter() - started < 0.5


def test_closing_hashes_are_still_stripped_from_a_heading() -> None:
    assert stated_causes("## Root cause ##\nThe guard is inverted.\n") == [
        ("Root cause", "The guard is inverted.")
    ]
    assert _without_title_headings("## T-1: Cart ##\nbody", "T-1: Cart").strip() == "body"
