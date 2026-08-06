"""Markdown → ADF: the structure Jira actually renders.

The converter this replaces emitted one paragraph per line, so an issue body's
lists, headings and tables arrived as literal punctuation. Each test here pins a
construct to the ADF node type Jira draws for it — a regression to
paragraph-per-line would fail every one of them.
"""

from __future__ import annotations

from typing import Any

from orchestrator.intake.adf import markdown_to_adf


def _types(doc: dict[str, Any]) -> list[str]:
    return [node["type"] for node in doc["content"]]


def _text(node: dict[str, Any]) -> str:
    if node.get("type") == "text":
        return str(node.get("text", ""))
    return "".join(_text(child) for child in node.get("content", []))


# ---- document shape -------------------------------------------------------


def test_empty_input_is_a_valid_empty_doc() -> None:
    doc = markdown_to_adf("")
    assert doc == {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": []}]}


def test_blank_separated_lines_stay_separate_paragraphs() -> None:
    doc = markdown_to_adf("line one\n\nline two")
    assert _types(doc) == ["paragraph", "paragraph"]
    assert _text(doc["content"][0]) == "line one"


def test_single_newline_becomes_a_hard_break_not_a_merge() -> None:
    """Plain-text callers laid their lines out on purpose; markdown would fold them."""
    doc = markdown_to_adf("first\nsecond")
    para = doc["content"][0]
    assert _types(doc) == ["paragraph"]
    assert [c["type"] for c in para["content"]] == ["text", "hardBreak", "text"]


# ---- blocks ---------------------------------------------------------------


def test_heading_levels() -> None:
    doc = markdown_to_adf("# one\n\n### three")
    assert _types(doc) == ["heading", "heading"]
    assert doc["content"][0]["attrs"]["level"] == 1
    assert doc["content"][1]["attrs"]["level"] == 3


def test_bullet_list_items_are_list_nodes_not_hyphen_text() -> None:
    doc = markdown_to_adf("- alpha\n- beta")
    assert _types(doc) == ["bulletList"]
    items = doc["content"][0]["content"]
    assert [item["type"] for item in items] == ["listItem", "listItem"]
    assert _text(items[0]) == "alpha"


def test_ordered_list_is_distinct_from_bullet() -> None:
    doc = markdown_to_adf("1. first\n2. second")
    assert _types(doc) == ["orderedList"]
    assert len(doc["content"][0]["content"]) == 2


def test_nested_list_nests() -> None:
    doc = markdown_to_adf("- outer\n  - inner\n- sibling")
    outer = doc["content"][0]
    assert [item["type"] for item in outer["content"]] == ["listItem", "listItem"]
    nested = outer["content"][0]["content"][1]
    assert nested["type"] == "bulletList"
    assert _text(nested) == "inner"


def test_fenced_code_keeps_its_language_and_body() -> None:
    doc = markdown_to_adf("```python\nx = 1\ny = 2\n```")
    block = doc["content"][0]
    assert block["type"] == "codeBlock"
    assert block["attrs"] == {"language": "python"}
    assert _text(block) == "x = 1\ny = 2"


def test_unsafe_fence_language_is_dropped_not_forwarded() -> None:
    """Jira rejects an unknown language; a plain fence is better than a 400."""
    doc = markdown_to_adf("```not a language\nbody\n```")
    assert "attrs" not in doc["content"][0]


def test_table_becomes_a_table_with_header_and_cells() -> None:
    doc = markdown_to_adf("| Framework | Detected from |\n|---|---|\n| FastAPI | decorator |")
    table = doc["content"][0]
    assert table["type"] == "table"
    header, body = table["content"]
    assert [cell["type"] for cell in header["content"]] == ["tableHeader", "tableHeader"]
    assert [cell["type"] for cell in body["content"]] == ["tableCell", "tableCell"]
    assert _text(body["content"][0]) == "FastAPI"


def test_ragged_table_row_is_padded_to_header_width() -> None:
    doc = markdown_to_adf("| a | b |\n|---|---|\n| only |")
    assert len(doc["content"][0]["content"][1]["content"]) == 2


def test_blockquote_and_rule() -> None:
    doc = markdown_to_adf("> quoted line\n\n---")
    assert _types(doc) == ["blockquote", "rule"]
    assert _text(doc["content"][0]) == "quoted line"


# ---- inline marks ---------------------------------------------------------


def _marks(node: dict[str, Any]) -> list[str]:
    return [mark["type"] for mark in node.get("marks", [])]


def test_inline_code_gets_a_code_mark_without_the_backticks() -> None:
    para = markdown_to_adf("call `impact_of` first")["content"][0]
    coded = [n for n in para["content"] if "code" in _marks(n)]
    assert [n["text"] for n in coded] == ["impact_of"]


def test_bold_gets_a_strong_mark() -> None:
    para = markdown_to_adf("this is **important** here")["content"][0]
    strong = [n for n in para["content"] if "strong" in _marks(n)]
    assert [n["text"] for n in strong] == ["important"]


def test_markdown_link_carries_its_href() -> None:
    para = markdown_to_adf("see [the spec](https://example.test/spec)")["content"][0]
    linked = [n for n in para["content"] if "link" in _marks(n)]
    assert linked[0]["text"] == "the spec"
    assert linked[0]["marks"][0]["attrs"]["href"] == "https://example.test/spec"


def test_bare_url_is_linked() -> None:
    para = markdown_to_adf("docs at https://example.test/x for more")["content"][0]
    linked = [n for n in para["content"] if "link" in _marks(n)]
    assert linked[0]["text"] == "https://example.test/x"


def test_snake_case_identifiers_are_never_italicised() -> None:
    """Underscore emphasis is unsupported on purpose — this domain is snake_case."""
    para = markdown_to_adf("call spec_to_issue_request in _link_dependencies")["content"][0]
    assert _text(para) == "call spec_to_issue_request in _link_dependencies"
    assert all(not n.get("marks") for n in para["content"])


def test_globs_are_never_italicised() -> None:
    para = markdown_to_adf("run over *.py and *.md files")["content"][0]
    assert _text(para) == "run over *.py and *.md files"


def test_bold_inside_inline_code_stays_literal() -> None:
    para = markdown_to_adf("`a**b**c`")["content"][0]
    assert _text(para) == "a**b**c"
    assert _marks(para["content"][0]) == ["code"]


# ---- determinism ----------------------------------------------------------


def test_same_input_same_output() -> None:
    body = "## Acceptance criteria\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert markdown_to_adf(body) == markdown_to_adf(body)
