"""P2 of SSPN-297 (SSPN-299): the `## Options` block of a change's `design.md`.

The writer and the reader live together and must agree: every write is read back through
``load_options`` on a change re-loaded with ``load_change``. Hand-written blocks are read the
way people write them, and anything not understood is **reported, never dropped** (D5, D7)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.intake import options as op
from orchestrator.intake import requirements as rq

_A = op.Option(
    title="Reuse the answer editor",
    approach="Extend record_answers so a fixed question is appended first.",
    anchors=("orchestrator.intake.requirements.record_answers", "src/orchestrator/cli/build.py"),
    creates=("src/orchestrator/intake/options.py",),
    risks="The editor and the reader can disagree on placement.",
    source="user",
)
_B = op.Option(
    title="A new answer command",
    approach="Add `openspec set` that writes a why-field directly.",
    source="generated · claude-haiku-5-5 · $0.0042",
)


def _change(tmp_path: Path, design: str | None = None, *, newline: str = "") -> rq.LoadedChange:
    directory = tmp_path / "changes" / "c"
    directory.mkdir(parents=True)
    (directory / "proposal.md").write_text("# P\n\n## Why\nw\n")
    if design is not None:
        with (directory / "design.md").open("w", encoding="utf-8", newline=newline) as handle:
            handle.write(design)
    return rq.load_change("c", root=tmp_path)


def _reload(change: rq.LoadedChange) -> op.OptionsBlock:
    return op.load_options(rq.load_change(str(change.directory)))


# ---- the writer and the reader agree ------------------------------------------------------------


def test_what_is_written_is_what_is_read_back(tmp_path: Path) -> None:
    change = _change(tmp_path)
    op.write_options(change, [_A, _B])
    block = _reload(change)
    assert block.present and block.problems == ()
    assert block.options == (_A, _B)


def test_a_change_with_no_design_md_gets_one(tmp_path: Path) -> None:
    change = _change(tmp_path)
    op.write_options(change, [_A])
    text = change.design_path.read_text()
    assert text.startswith("# Design\n\n## Options\n\n### Option 1 — Reuse the answer editor\n")


def test_the_rendered_block_is_the_documented_layout() -> None:
    assert op.render_options([_A]) == (
        "## Options\n\n"
        "### Option 1 — Reuse the answer editor\n"
        "- **Approach:** Extend record_answers so a fixed question is appended first.\n"
        "- **Anchors:** `orchestrator.intake.requirements.record_answers`, `src/orchestrator/cli/build.py`\n"
        "- **Creates:** `src/orchestrator/intake/options.py`\n"
        "- **Risks:** The editor and the reader can disagree on placement.\n"
        "- **Source:** user\n"
    )


def test_rendering_is_deterministic() -> None:
    assert op.render_options([_A, _B]) == op.render_options([_A, _B])


def test_the_rest_of_design_md_keeps_every_byte(tmp_path: Path) -> None:
    design = "# Design\n\nIntro the author wrote.\n\n## Context\nSome **bold** context.\n\n## Decision\nTBD\n"
    change = _change(tmp_path, design)
    op.write_options(change, [_A])
    text = change.design_path.read_text()
    assert text.startswith("# Design\n\nIntro the author wrote.\n\n## Context\nSome **bold** context.\n\n")
    assert text.endswith("\n\n## Decision\nTBD\n")
    assert text.index("## Options") < text.index("## Decision")  # before the decision it informs


def test_an_existing_options_block_is_replaced_in_place(tmp_path: Path) -> None:
    design = "# Design\n\n## Options\n\n### Old\n- **Approach:** old\n\n## Risks\nr\n"
    change = _change(tmp_path, design)
    op.write_options(change, [_B])
    text = change.design_path.read_text()
    assert "old" not in text and text.count("## Options") == 1
    assert text.endswith("\n\n## Risks\nr\n")
    assert _reload(change).options == (_B,)


def test_with_no_decision_section_the_block_goes_at_the_end(tmp_path: Path) -> None:
    change = _change(tmp_path, "# Design\n\n## Context\nc\n")
    op.write_options(change, [_A])
    text = change.design_path.read_text()
    assert text.startswith("# Design\n\n## Context\nc\n\n## Options\n")


def test_crlf_is_kept(tmp_path: Path) -> None:
    change = _change(tmp_path, "# Design\r\n\r\n## Context\r\nc\r\n")
    op.write_options(change, [_A])
    raw = change.design_path.read_bytes()
    assert b"\r\n## Options\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert _reload(change).options == (_A,)


def test_no_trailing_newline_is_kept(tmp_path: Path) -> None:
    change = _change(tmp_path, "# Design\n\n## Context\nc")
    op.write_options(change, [_A])
    assert not change.design_path.read_text().endswith("\n")
    assert _reload(change).options == (_A,)


def test_the_files_mode_is_kept(tmp_path: Path) -> None:
    change = _change(tmp_path, "# Design\n")
    os.chmod(change.design_path, 0o700)  # owner-only, and not mkstemp's 0o600
    op.write_options(change, [_A])
    assert change.design_path.stat().st_mode & 0o777 == 0o700


def test_a_write_leaves_no_temp_file(tmp_path: Path) -> None:
    change = _change(tmp_path)
    op.write_options(change, [_A])
    assert sorted(p.name for p in change.directory.iterdir()) == ["design.md", "proposal.md"]


# ---- untrusted text cannot write structure --------------------------------------------------------


def test_a_line_break_in_a_value_cannot_open_a_section(tmp_path: Path) -> None:
    sneaky = op.Option(
        title="Sneaky\n## Decision",
        approach="Do it.\n## Decision\n- chosen: this one",
        risks="none\n### Option 9 — Injected",
        source="relayed",
    )
    change = _change(tmp_path)
    op.write_options(change, [sneaky])
    text = change.design_path.read_text()
    assert "\n## Decision" not in text and "\n### Option 9" not in text
    block = _reload(change)
    assert len(block.options) == 1 and block.problems == ()
    assert block.options[0].approach == "Do it. ## Decision - chosen: this one"


@pytest.mark.parametrize("item", ["a`b", "a, b", "a; b", "   "])
def test_an_anchor_that_would_not_read_back_is_refused_and_writes_nothing(tmp_path: Path, item: str) -> None:
    change = _change(tmp_path, "# Design\n")
    before = change.design_path.read_bytes()
    with pytest.raises(op.OptionsError):
        op.write_options(change, [op.Option(title="T", approach="A", anchors=(item,), source="user")])
    assert change.design_path.read_bytes() == before


@pytest.mark.parametrize(
    "option",
    [
        op.Option(title="  ", approach="A", source="user"),
        op.Option(title="T", approach="", source="user"),
        op.Option(title="T", approach="A", source="somebody"),
    ],
)
def test_an_option_the_reader_would_report_is_refused_by_the_writer(option: op.Option) -> None:
    with pytest.raises(op.OptionsError):
        op.render_options([option])


def test_two_options_with_one_title_are_refused() -> None:
    with pytest.raises(op.OptionsError, match="twice"):
        op.render_options([_A, op.Option(title=_A.title.upper(), approach="x", source="user")])


def test_nothing_to_write_is_refused() -> None:
    with pytest.raises(op.OptionsError):
        op.render_options([])


# ---- reading what people write ------------------------------------------------------------------


def test_a_hand_written_block_is_read_the_way_people_write_it() -> None:
    design = (
        "# Design\n\n## Options\n\nTwo ways to do this.\n\n"
        "### Option A: Keep the editor\n"
        "- **Approach**: Reuse it.\n"
        "  It already handles placement.\n"
        "- anchors: `orchestrator.intake.requirements.record_answers`; build.py\n"
        "- **Creates:**\n"
        "  - `a.py`\n"
        "  - b.py\n"
        "\n"
        "```python\n- **Approach:** not a field, a code sample\n```\n"
        "### Rewrite it\n"
        "* Approach: Start over.\n"
    )
    block = op.parse_options(design)
    assert block.problems == ()
    first, second = block.options
    assert first.title == "Keep the editor"
    assert first.approach == "Reuse it. It already handles placement."
    assert first.anchors == ("orchestrator.intake.requirements.record_answers", "build.py")
    assert first.creates == ("a.py", "b.py")
    assert first.source == ""  # unstated: Spine does not guess who wrote it
    assert (second.title, second.approach) == ("Rewrite it", "Start over.")


def test_an_option_with_no_approach_is_reported_not_dropped() -> None:
    block = op.parse_options("## Options\n\n### Only anchors\n- **Anchors:** `x`\n")
    assert [o.title for o in block.options] == ["Only anchors"]
    assert any("no Approach" in p for p in block.problems)


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("### T\n- **Approach:** a\n- **Colour:** red\n", "unknown field"),
        ("### T\n- **Approach:** a\n- **Approach:** b\n", "twice"),
        ("### T\n- **Approach:** a\nA stray sentence.\n", "not understood"),
        ("### T\n- **Approach:** a\n### t\n- **Approach:** b\n", "same title"),
        ("Just prose, no options.\n", "no options"),
        ("### T\n- **Approach:** a\n- **Source:** my cat\n", "source"),
        ("### Option 2 —\n- **Approach:** a\n", "no title"),
    ],
)
def test_what_the_reader_does_not_understand_is_reported(body: str, needle: str) -> None:
    block = op.parse_options("## Options\n\n" + body)
    assert any(needle in p for p in block.problems), block.problems
    assert block.present


def test_the_first_of_a_repeated_field_is_kept() -> None:
    block = op.parse_options("## Options\n\n### T\n- **Approach:** first\n- **Approach:** second\n")
    assert block.options[0].approach == "first"


def test_an_options_heading_inside_a_code_fence_is_not_a_section() -> None:
    block = op.parse_options("# D\n\n```md\n## Options\n### T\n- **Approach:** a\n```\n")
    assert not block.present and block.options == ()


def test_with_two_options_sections_the_last_is_read_and_that_is_said() -> None:
    block = op.parse_options(
        "## Options\n\n### Old\n- **Approach:** o\n\n## Options\n\n### New\n- **Approach:** n\n"
    )
    assert [o.title for o in block.options] == ["New"]
    assert any("2 `## Options` sections" in p for p in block.problems)


def test_a_bare_hash_line_is_not_a_heading() -> None:
    """B129's class: a heading is one line, for this reader and its writer alike."""
    block = op.parse_options("## Options\n\n### T\n- **Approach:** a\n##\nOptions\n")
    assert [o.title for o in block.options] == ["T"]


def test_no_design_md_means_no_options_and_no_problems(tmp_path: Path) -> None:
    block = op.load_options(_change(tmp_path))
    assert (block.present, block.options, block.problems) == (False, (), ())


def test_a_design_md_without_an_options_section_is_not_present(tmp_path: Path) -> None:
    block = op.load_options(_change(tmp_path, "# Design\n\n## Context\nc\n"))
    assert not block.present and block.problems == ()


@pytest.mark.parametrize("source", ["user", "relayed", "generated · ollama/qwen2.5-coder · $0", "GENERATED"])
def test_each_documented_source_is_accepted(source: str) -> None:
    block = op.parse_options(f"## Options\n\n### T\n- **Approach:** a\n- **Source:** {source}\n")
    assert block.problems == () and block.options[0].source == source


def test_a_new_design_md_gets_the_mode_any_new_file_gets(tmp_path: Path) -> None:
    """Not mkstemp's 0600: a design.md only its writer can read is no use in a shared checkout."""
    change = _change(tmp_path)
    op.write_options(change, [_A])
    reference = change.directory / "reference"
    reference.touch()
    assert change.design_path.stat().st_mode & 0o777 == reference.stat().st_mode & 0o777


def test_with_two_options_sections_the_writer_replaces_the_one_the_reader_reads(tmp_path: Path) -> None:
    design = (
        "# D\n\n## Options\n\n### First\n- **Approach:** f\n\n## Options\n\n### Second\n- **Approach:** s\n"
    )
    change = _change(tmp_path, design)
    op.write_options(change, [_B])
    block = _reload(change)
    assert block.options == (_B,)
    assert "### First" in change.design_path.read_text()  # the shadowed one is left as written


# ---- a design.md the reader cannot use (SSPN-312, R5) ------------------------------------------


def test_a_design_md_that_is_not_utf8_and_has_no_options_has_no_options(tmp_path: Path) -> None:
    change = _change(tmp_path)
    change.design_path.write_bytes("# Design\n\nCaf\u00e9.\n".encode("latin-1"))
    assert op.load_options(change) == op.OptionsBlock(present=False)


def test_a_design_md_with_options_that_is_not_utf8_is_an_options_error(tmp_path: Path) -> None:
    change = _change(tmp_path)
    change.design_path.write_bytes("# Design\n\n## Options\n\nCaf\u00e9.\n".encode("latin-1"))
    with pytest.raises(op.OptionsError, match="not UTF-8"):
        op.load_options(change)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_design_md_is_an_options_error_not_a_crash(tmp_path: Path) -> None:
    change = _change(tmp_path)
    change.design_path.write_text("# Design\n")
    change.design_path.chmod(0)
    try:
        with pytest.raises(op.OptionsError, match="cannot be read"):
            op.load_options(change)
    finally:
        change.design_path.chmod(0o644)
