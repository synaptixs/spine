"""Markdown → Atlassian Document Format, so issue bodies survive the trip to Jira.

Jira Cloud's v3 REST API accepts **only** ADF for descriptions and comments —
plain text and wiki markup are rejected. The converter this replaces emitted one
paragraph per non-blank line, which meant every structured issue body arrived as
an undifferentiated wall: ``- item`` rendered as a literal hyphen, ``## Heading``
as literal hashes, a markdown table as three rows of pipes, and blank lines — the
only visual grouping left — were dropped outright. Writing *more* detail into an
issue made that worse, not better, which is the bug this module exists to fix.

Rendered here into the ADF node types Jira draws natively: headings, bullet and
ordered lists (nested), fenced code with a language, tables, blockquotes,
horizontal rules, and inline code / bold / links.

**Emphasis is deliberately limited to** ``**bold**``. Underscore emphasis is not
supported because this domain is full of snake_case: ``spec_to_issue_request``
would render as "spec to issue request" with the middle italicised. Single-asterisk
italics are skipped for the same reason — globs like ``*.py`` and ``**/*.md`` are
everywhere in engineering issues and would pair up across a sentence. That is the
same trade the PKG's call resolver makes: render what is unambiguous, leave the
rest as literal text, never guess.

Deterministic and dependency-free — same markdown in, same JSON out.
"""

from __future__ import annotations

import re
from typing import Any

# A single newline inside a paragraph becomes a ``hardBreak`` rather than a space.
# Markdown would fold it, but plain-text callers (``comment_issue``, an injected
# spec) rely on their line breaks surviving, and folding them silently reflows
# someone's carefully laid-out body.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)\s*([A-Za-z0-9+#._-]*)\s*$")
_RULE_RE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_TABLE_DELIM_RE = re.compile(r"^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$")
_SAFE_LANG_RE = re.compile(r"^[a-z0-9+#-]+$")

_INLINE_RE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|\[(?P<link_text>[^\]\n]*)\]\((?P<link_href>[^)\s]+)\)"
    r"|(?P<strong>\*\*(?P<strong_text>[^*\n]+)\*\*)"
    r"|(?P<url>https?://[^\s<>)\]]+)"
)


# ---- inline ---------------------------------------------------------------


def _inline(text: str) -> list[dict[str, Any]]:
    """Text → ADF inline nodes, marking code / bold / links."""
    nodes: list[dict[str, Any]] = []

    def push(raw: str, marks: list[dict[str, Any]] | None = None) -> None:
        if not raw:
            return
        node: dict[str, Any] = {"type": "text", "text": raw}
        if marks:
            node["marks"] = marks
        nodes.append(node)

    pos = 0
    for match in _INLINE_RE.finditer(text):
        push(text[pos : match.start()])
        href = match.group("link_href")
        if match.group("code"):
            push(match.group("code")[1:-1], [{"type": "code"}])
        elif href is not None:
            push(match.group("link_text") or href, [{"type": "link", "attrs": {"href": href}}])
        elif match.group("strong"):
            push(match.group("strong_text"), [{"type": "strong"}])
        else:
            url = match.group("url")
            push(url, [{"type": "link", "attrs": {"href": url}}])
        pos = match.end()
    push(text[pos:])
    return nodes


def _paragraph(lines: list[str]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for offset, line in enumerate(lines):
        if offset:
            content.append({"type": "hardBreak"})
        content.extend(_inline(line))
    return {"type": "paragraph", "content": content}


# ---- blocks ---------------------------------------------------------------


def _is_table_start(lines: list[str], i: int) -> bool:
    return "|" in lines[i] and i + 1 < len(lines) and bool(_TABLE_DELIM_RE.match(lines[i + 1]))


def _starts_block(lines: list[str], i: int) -> bool:
    """Does this line open a non-paragraph block? Bounds paragraph collection."""
    line = lines[i]
    return bool(
        _FENCE_RE.match(line)
        or _RULE_RE.match(line)
        or _HEADING_RE.match(line)
        or _QUOTE_RE.match(line)
        or _BULLET_RE.match(line)
        or _ORDERED_RE.match(line)
        or _is_table_start(lines, i)
    )


def _split_row(line: str) -> list[str]:
    """Cells of a pipe-table row. Escaped pipes are not supported (rare in specs)."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_fence(lines: list[str], i: int) -> tuple[dict[str, Any], int]:
    opener = _FENCE_RE.match(lines[i])
    lang = (opener.group(1) if opener else "").lower()
    i += 1
    body: list[str] = []
    while i < len(lines) and not _FENCE_RE.match(lines[i]):
        body.append(lines[i])
        i += 1
    i = min(i + 1, len(lines))  # consume the closing fence if there was one
    node: dict[str, Any] = {"type": "codeBlock"}
    if lang and _SAFE_LANG_RE.match(lang):
        # An unknown language is rejected by Jira, so only pass through a plain token.
        node["attrs"] = {"language": lang}
    text = "\n".join(body)
    if text:
        node["content"] = [{"type": "text", "text": text}]
    return node, i


def _parse_table(lines: list[str], i: int) -> tuple[dict[str, Any], int]:
    header = _split_row(lines[i])
    width = len(header)
    i += 2  # header row + delimiter row
    body: list[list[str]] = []
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        body.append(_split_row(lines[i]))
        i += 1

    def row(cells: list[str], kind: str) -> dict[str, Any]:
        padded = (cells + [""] * width)[:width]  # ragged rows would break the render
        return {
            "type": "tableRow",
            "content": [{"type": kind, "attrs": {}, "content": [_paragraph([cell])]} for cell in padded],
        }

    node = {
        "type": "table",
        "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
        "content": [row(header, "tableHeader"), *(row(cells, "tableCell") for cells in body)],
    }
    return node, i


def _collect_list_items(lines: list[str], i: int) -> tuple[list[tuple[int, str, bool]], int]:
    """Consecutive list lines as ``(indent, text, ordered)``, in source order."""
    items: list[tuple[int, str, bool]] = []
    while i < len(lines):
        if _RULE_RE.match(lines[i]):
            break
        bullet = _BULLET_RE.match(lines[i])
        ordered = _ORDERED_RE.match(lines[i])
        if bullet:
            items.append((len(bullet.group(1)), bullet.group(2), False))
        elif ordered:
            items.append((len(ordered.group(1)), ordered.group(2), True))
        else:
            break
        i += 1
    return items, i


def _build_list(items: list[tuple[int, str, bool]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    """One list node from ``items[index:]``, recursing on deeper indents."""
    ordered = items[index][2]
    node: dict[str, Any] = {"type": "orderedList" if ordered else "bulletList", "content": []}
    while index < len(items):
        item_indent, text, item_ordered = items[index]
        if item_indent < indent or item_ordered is not ordered:
            break
        content: list[dict[str, Any]] = [_paragraph([text])]
        index += 1
        if index < len(items) and items[index][0] > item_indent:
            child, index = _build_list(items, index, items[index][0])
            content.append(child)
        node["content"].append({"type": "listItem", "content": content})
    return node, index


def _parse_blocks(lines: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue

        if _FENCE_RE.match(lines[i]):
            node, i = _parse_fence(lines, i)
            out.append(node)
            continue

        if _RULE_RE.match(lines[i]):
            out.append({"type": "rule"})
            i += 1
            continue

        heading = _HEADING_RE.match(lines[i])
        if heading:
            out.append(
                {
                    "type": "heading",
                    "attrs": {"level": len(heading.group(1))},
                    "content": _inline(heading.group(2).strip()),
                }
            )
            i += 1
            continue

        if _QUOTE_RE.match(lines[i]):
            quoted: list[str] = []
            while i < len(lines) and (match := _QUOTE_RE.match(lines[i])) is not None:
                quoted.append(match.group(1))
                i += 1
            inner = _parse_blocks(quoted) or [{"type": "paragraph", "content": []}]
            out.append({"type": "blockquote", "content": inner})
            continue

        if _is_table_start(lines, i):
            node, i = _parse_table(lines, i)
            out.append(node)
            continue

        if _BULLET_RE.match(lines[i]) or _ORDERED_RE.match(lines[i]):
            items, i = _collect_list_items(lines, i)
            cursor = 0
            while cursor < len(items):
                node, cursor = _build_list(items, cursor, items[cursor][0])
                out.append(node)
            continue

        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not _starts_block(lines, i):
            para.append(lines[i].strip())
            i += 1
        if para:
            out.append(_paragraph(para))
        else:  # defensive: never spin on a line no branch consumed
            i += 1
    return out


def markdown_to_adf(text: str) -> dict[str, Any]:
    """Render markdown as an ADF document. Empty input yields a valid empty doc."""
    normalised = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    content = _parse_blocks(normalised.split("\n"))
    if not content:
        content = [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": content}


__all__ = ["markdown_to_adf"]
