"""The ``## Options`` block of an OpenSpec change's ``design.md`` (SSPN-297, P2 — D5, D7).

Ideation keeps a change's candidate solutions where OpenSpec already keeps alternatives: in
``design.md``. Each option is one ``###`` subsection of ``## Options``::

    ## Options

    ### Option 1 — Reuse the answer editor
    - **Approach:** Extend record_answers so a fixed question is appended first.
    - **Anchors:** `orchestrator.intake.requirements.record_answers`, `src/orchestrator/cli/build.py`
    - **Creates:** `src/orchestrator/intake/options.py`
    - **Risks:** The editor and the reader can disagree on placement.
    - **Source:** user

**Anchors** are the existing symbols or paths an option changes or reuses — what a later phase
measures against the graph. **Creates** is what it adds. **Source** says who the block came from
as Spine observed it — ``user``, ``relayed`` (over MCP) or ``generated · <model> · $<cost>`` —
and is empty for a block a person wrote by hand: Spine never guesses an author.

The writer and the reader are in this one module and must agree, so every write is read back the
same way. The reader is forgiving about how people write a block (``**Approach**:``, ``Approach:``,
``* `` bullets, sub-bullet lists, a ``### Title`` with no number) and **reports** what it does not
understand rather than dropping it: a malformed option is still an option, with a problem beside
it. The writer is strict, and refuses anything it could not read back as written — a line break in
a value is collapsed, so text from a model cannot write a heading of its own.

No model is called here; nothing here reads the graph. Deterministic: the same text, the same block.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.intake.atomic import write_atomic

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.intake.requirements import LoadedChange

__all__ = [
    "Option",
    "OptionsBlock",
    "OptionsError",
    "load_options",
    "parse_options",
    "render_options",
    "write_options",
]

#: Who an option came from, as Spine observed it. ``generated`` carries ``· <model> · $<cost>``.
SOURCES = ("user", "relayed", "generated")

_FIELDS = ("approach", "anchors", "creates", "risks", "source")
_LIST_FIELDS = {"anchors", "creates"}
# One line each, for this reader and its writer alike — `\s` would let a bare `##` swallow the
# next line as its heading text (B129).
_H2 = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
_H3 = re.compile(r"^###[ \t]+(.+?)[ \t]*$")
_FIELD = re.compile(
    r"^[-*][ \t]+(?:\*\*)?(?P<label>[A-Za-z][A-Za-z ]*?)(?:\*\*:|:\*\*|:)[ \t]*(?P<value>.*)$"
)
_NUMBERED = re.compile(r"^option[ \t]+\w+[ \t]*[—–:.\-][ \t]*", re.IGNORECASE)
_SUB_BULLET = re.compile(r"^[-*][ \t]+")


class OptionsError(ValueError):
    """An option the writer will not write, or a ``design.md`` that cannot be read. Never a defect."""


@dataclass(frozen=True)
class Option:
    """One candidate solution, as written in ``## Options``."""

    title: str
    approach: str
    anchors: tuple[str, ...] = ()
    creates: tuple[str, ...] = ()
    risks: str = ""
    source: str = ""


@dataclass(frozen=True)
class OptionsBlock:
    """What ``## Options`` holds. ``present`` is whether the section exists at all; ``problems``
    is everything the reader could not take as written — reported, never silently dropped."""

    present: bool
    options: tuple[Option, ...] = ()
    problems: tuple[str, ...] = ()


# --- reading --------------------------------------------------------------------------------------


def _fence_mask(lines: list[str]) -> list[bool]:
    inside, mask = False, []
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            mask.append(True)
            inside = not inside
        else:
            mask.append(inside)
    return mask


def _sections(lines: list[str], mask: list[bool]) -> list[tuple[str, int, int]]:
    """``(name, start, end)`` per unfenced ``## `` heading: ``lines[start]`` is the heading and the
    section runs to the next one."""
    starts = [
        (n, m.group(1).strip().lower())
        for n, line in enumerate(lines)
        if not mask[n] and (m := _H2.match(line))
    ]
    return [
        (name, at, starts[k + 1][0] if k + 1 < len(starts) else len(lines))
        for k, (at, name) in enumerate(starts)
    ]


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _fold(text: str) -> str:
    return _one_line(text).casefold()


def _items(value: str) -> list[str]:
    return [item for item in (part.strip().strip("`").strip() for part in re.split(r"[,;]", value)) if item]


def _source_ok(source: str) -> bool:
    return source.split("·")[0].strip().lower() in SOURCES


def _title(heading: str) -> str:
    return _one_line(_NUMBERED.sub("", heading.strip(), count=1))


def parse_options(design_md: str) -> OptionsBlock:
    """The ``## Options`` block of ``design_md``: the last such section, as a reader of sections by
    name keeps. Text before the first option is intro prose; a fenced code sample is skipped."""
    lines = design_md.replace("\r\n", "\n").split("\n")
    mask = _fence_mask(lines)
    found = [s for s in _sections(lines, mask) if s[0] == "options"]
    if not found:
        return OptionsBlock(present=False)
    problems: list[str] = []
    if len(found) > 1:
        problems.append(f"{len(found)} `## Options` sections; the last is read")
    _, start, end = found[-1]
    raw: list[tuple[str, dict[str, list[str]]]] = []
    current: dict[str, list[str]] | None = None
    last_field = ""
    for n in range(start + 1, end):
        line, fenced = lines[n], mask[n]
        if fenced or not line.strip():
            continue
        if m := _H3.match(line):
            current, last_field = {}, ""
            raw.append((m.group(1), current))
            continue
        if current is None:
            continue  # intro prose before the first option
        label = f"Option {len(raw)}"
        if line[:1] in " \t":
            if not last_field:
                problems.append(f"{label}: line not understood: {_one_line(line)!r}")
            elif last_field in _LIST_FIELDS:
                current[last_field].extend(_items(_SUB_BULLET.sub("", line.strip())))
            else:
                current[last_field].append(line.strip())
            continue
        if not (fm := _FIELD.match(line)):
            problems.append(f"{label}: line not understood: {_one_line(line)!r}")
            last_field = ""
            continue
        name = fm.group("label").strip().lower()
        if name not in _FIELDS:
            problems.append(f"{label}: unknown field {fm.group('label').strip()!r}")
            last_field = ""
            continue
        if name in current:
            problems.append(f"{label}: {name!r} given twice; the first is used")
            last_field = ""
            continue
        value = fm.group("value").strip()
        current[name] = _items(value) if name in _LIST_FIELDS else ([value] if value else [])
        last_field = name
    if not raw:
        problems.append("`## Options` has no options")
    options: list[Option] = []
    seen: dict[str, int] = {}
    for k, (heading, fields) in enumerate(raw, 1):
        title = _title(heading)
        option = Option(
            title=title,
            approach=_one_line(" ".join(fields.get("approach", []))),
            anchors=tuple(fields.get("anchors", [])),
            creates=tuple(fields.get("creates", [])),
            risks=_one_line(" ".join(fields.get("risks", []))),
            source=_one_line(" ".join(fields.get("source", []))),
        )
        if not title:
            problems.append(f"Option {k} has no title")
        if not option.approach:
            problems.append(f"Option {k} ({title or 'untitled'}) has no Approach")
        if option.source and not _source_ok(option.source):
            problems.append(
                f"Option {k}: unknown source {option.source!r} — expected user, relayed or generated"
            )
        if title and (first := seen.get(_fold(title))):
            problems.append(f"Options {first} and {k} have the same title {title!r}")
        seen.setdefault(_fold(title), k)
        options.append(option)
    return OptionsBlock(present=True, options=tuple(options), problems=tuple(problems))


def load_options(change: LoadedChange) -> OptionsBlock:
    """The options a change's ``design.md`` holds; none (and no problem) when it has no design."""
    path = change.design_path
    if not path.is_file():
        return OptionsBlock(present=False)
    try:
        return parse_options(path.read_bytes().decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise OptionsError(f"{path} is not UTF-8 text: {exc}") from exc


# --- writing --------------------------------------------------------------------------------------


def _clean_items(kind: str, title: str, items: Sequence[str]) -> list[str]:
    out = []
    for item in items:
        text = _one_line(item)
        if not text or any(ch in text for ch in "`,;"):
            raise OptionsError(
                f"option {title!r}: {kind} entry {item!r} cannot be written so it reads back the same "
                "(empty, or holds a backtick, comma or semicolon)"
            )
        out.append(text)
    return out


def render_options(options: Sequence[Option]) -> str:
    """The ``## Options`` section for ``options``, in the documented layout. Refuses whatever the
    reader would report or could not read back as written."""
    if not options:
        raise OptionsError("no options to write")
    seen: set[str] = set()
    chunks: list[str] = []
    for k, option in enumerate(options, 1):
        title, approach = _one_line(option.title), _one_line(option.approach)
        risks, source = _one_line(option.risks), _one_line(option.source)
        if not title:
            raise OptionsError(f"option {k} has no title")
        if _fold(title) in seen:
            raise OptionsError(f"two options are titled {title!r} — a title is written twice")
        seen.add(_fold(title))
        if not approach:
            raise OptionsError(f"option {title!r} has no approach")
        if not _source_ok(source):
            raise OptionsError(
                f"option {title!r}: source {option.source!r} — "
                "say who it came from: user, relayed or generated"
            )
        lines = [f"### Option {k} — {title}", f"- **Approach:** {approach}"]
        if anchors := _clean_items("anchors", title, option.anchors):
            lines.append("- **Anchors:** " + ", ".join(f"`{a}`" for a in anchors))
        if creates := _clean_items("creates", title, option.creates):
            lines.append("- **Creates:** " + ", ".join(f"`{c}`" for c in creates))
        if risks:
            lines.append(f"- **Risks:** {risks}")
        lines.append(f"- **Source:** {source}")
        chunks.append("\n".join(lines) + "\n")
    return "## Options\n\n" + "\n".join(chunks)


def set_options(design_md: str, options: Sequence[Option]) -> str:
    """``design_md`` with its ``## Options`` section set to ``options``. An existing section is
    replaced where it is; a new one goes before ``## Decision`` (the choice it informs), else at
    the end. Every other line keeps its bytes; CRLF and a missing final newline are kept."""
    block = render_options(options).rstrip("\n").split("\n")
    crlf = "\r\n" in design_md
    text = design_md.replace("\r\n", "\n")
    if not text.strip():
        out = "# Design\n\n" + "\n".join(block) + "\n"
        return out.replace("\n", "\r\n") if crlf else out
    trailing = text.endswith("\n")
    lines = (text[:-1] if trailing else text).split("\n")
    sections = _sections(lines, _fence_mask(lines))
    existing = [s for s in sections if s[0] == "options"]
    decision = next((s for s in sections if s[0] == "decision"), None)
    if existing:
        _, start, end = existing[-1]
        lines[start:end] = block + ([""] if end < len(lines) else [])
    elif decision:
        at = decision[1]
        lead = [""] if at and lines[at - 1].strip() else []
        lines[at:at] = lead + block + [""]
    else:
        lines += ([""] if lines[-1].strip() else []) + block
    out = "\n".join(lines) + ("\n" if trailing else "")
    return out.replace("\n", "\r\n") if crlf else out


def write_options(change: LoadedChange, options: Sequence[Option]) -> None:
    """Set the change's ``## Options`` to ``options``, creating ``design.md`` if it has none.
    Validated in full before the file is touched, then replaced in one step."""
    path = change.design_path
    try:
        current = path.read_bytes().decode("utf-8") if path.is_file() else ""
    except UnicodeDecodeError as exc:
        raise OptionsError(f"{path} is not UTF-8 text: {exc}") from exc
    write_atomic(path, set_options(current, options))


# --- the choice (P4, D9) ---------------------------------------------------------------------------

_BY_NUMBER = re.compile(r"^(?:option[ \t]*)?(\d+)$", re.IGNORECASE)


def label(options: Sequence[Option], option: Option) -> str:
    """How a chosen option is recorded: ``Option <n> — <title>``, its place in the block and its name."""
    return f"Option {list(options).index(option) + 1} — {option.title}"


def resolve_choice(options: Sequence[Option], answer: str) -> Option | None:
    """The option an answer names: its number (``2``, ``Option 2``), its title, or its full label —
    case and spacing forgiven. ``None`` when it names none of them."""
    text = _one_line(answer)
    if m := _BY_NUMBER.match(text):
        n = int(m.group(1))
        return options[n - 1] if 1 <= n <= len(options) else None
    # A recorded label is matched by its title, so a choice survives the options being renumbered.
    wanted = {_fold(text), _fold(_title(text))}
    for option in options:
        if _fold(option.title) in wanted:
            return option
    return None


def set_decision(design_md: str, chosen: str, recorded: str) -> str:
    """``design_md`` with ``## Decision`` saying which option was chosen. The record that counts is
    the answer under the choice question in ``proposal.md``; this section shows it beside the
    options. An existing ``## Decision`` is replaced where it is, else it goes after ``## Options``."""
    block = ["## Decision", "", f"**Chosen:** {_one_line(chosen)}", "", _one_line(recorded)]
    crlf = "\r\n" in design_md
    text = design_md.replace("\r\n", "\n")
    trailing = text.endswith("\n") or not text
    lines = (text[:-1] if text.endswith("\n") else text).split("\n")
    sections = _sections(lines, _fence_mask(lines))
    existing = [s for s in sections if s[0] == "decision"]
    after = [s for s in sections if s[0] == "options"]
    if existing:
        _, start, end = existing[-1]
        lines[start:end] = block + ([""] if end < len(lines) else [])
    elif after:
        end = after[-1][2]
        lines[end:end] = ([""] if lines[end - 1].strip() else []) + block + ([""] if end < len(lines) else [])
    else:
        lines += ([""] if lines and lines[-1].strip() else []) + block
    out = "\n".join(lines) + ("\n" if trailing else "")
    return out.replace("\n", "\r\n") if crlf else out
