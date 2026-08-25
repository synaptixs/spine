"""Render `assets/knowledge-foundation.svg` — the Knowledge Foundation Architecture diagram.

The product-neutral, federated picture: many sources across many repositories collapsing through
one narrow vocabulary into a grounded, versioned graph, and fanning back out as projections. The
specification it draws is [`docs/specs/knowledge-foundation-diagram-prompt.md`]; its animated HTML
sibling is generated from that prompt, and this is the static, diffable form of the same figure.

**Product-neutral on purpose.** No tool or vendor names, and the vocabulary here (`file`,
`symbol`, `work item`, `contract`) is deliberately *not* Spine's own `NodeKind`/`EdgeKind`. This
describes the architecture, not one implementation of it — which is why, unlike
`render_architecture_svg.py`, nothing on this diagram is read from `pkg.facts`. That is a
departure from the usual "every number is computed" rule and it is the whole point of the figure.

**Layout is computed, seeded and deterministic** — same input, same bytes (CLAUDE.md, invariant
3). No random placement, no force layout: a picture that redraws differently for an identical
commit cannot be diffed.

**Text is measured, not estimated.** Every string is laid out against real Helvetica advance
widths (the PostScript standard metrics, in units/1000, embedded below), and the SVG names
Helvetica first so the renderer resolves the font the layout was computed for. Card heights
derive from wrapped line counts; nothing has a fixed height. `--check` then re-derives the
geometry and **fails on any overlap or overflow**, so "the text does not collide" is a build
result rather than something someone eyeballed once.

    uv run python scripts/render_knowledge_foundation_svg.py          # write the SVG + PNG
    uv run python scripts/render_knowledge_foundation_svg.py --check  # fail if stale or broken
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "assets" / "knowledge-foundation.svg"

# --------------------------------------------------------------------------------------
# Font metrics — Helvetica / Arial advance widths, units per 1000 em.
#
# These are the published PostScript standard widths. Arial is metrically compatible, so the
# fallback measures identically. Measuring rather than estimating is what lets the overflow
# check below be an assertion instead of a hope: the previous generator in this repo used
# "~7.6px per character" and ran a wrapped line straight through a label.
# --------------------------------------------------------------------------------------

_REG = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191,
    "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278,
    "0": 556, "1": 556, "2": 556, "3": 556, "4": 556, "5": 556, "6": 556, "7": 556,
    "8": 556, "9": 556, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556,
    "@": 1015, "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722, "O": 778,
    "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944,
    "X": 667, "Y": 667, "Z": 611, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556,
    "`": 333, "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556,
    "h": 556, "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556, "o": 556,
    "p": 556, "q": 556, "r": 333, "s": 500, "t": 278, "u": 556, "v": 500, "w": 722,
    "x": 500, "y": 500, "z": 500, "{": 334, "|": 260, "}": 334, "~": 584,
}  # fmt: skip

_BOLD = {
    " ": 278, "!": 333, '"': 474, "#": 556, "$": 556, "%": 889, "&": 722, "'": 238,
    "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278,
    "0": 556, "1": 556, "2": 556, "3": 556, "4": 556, "5": 556, "6": 556, "7": 556,
    "8": 556, "9": 556, ":": 333, ";": 333, "<": 584, "=": 584, ">": 584, "?": 611,
    "@": 975, "A": 722, "B": 722, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 556, "K": 722, "L": 611, "M": 833, "N": 722, "O": 778,
    "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944,
    "X": 667, "Y": 667, "Z": 611, "[": 333, "\\": 278, "]": 333, "^": 584, "_": 556,
    "`": 333, "a": 556, "b": 611, "c": 556, "d": 611, "e": 556, "f": 333, "g": 611,
    "h": 611, "i": 278, "j": 278, "k": 556, "l": 278, "m": 889, "n": 611, "o": 611,
    "p": 611, "q": 611, "r": 389, "s": 556, "t": 333, "u": 611, "v": 556, "w": 778,
    "x": 556, "y": 556, "z": 500, "{": 389, "|": 280, "}": 389, "~": 584,
}  # fmt: skip

# Glyphs outside the standard table. Deliberately over-stated: a width that is too large wraps
# a line early, which is invisible; one that is too small overflows the card, which is not.
_EXTRA = {"→": 800, "·": 300, "§": 556, "—": 1000, "–": 556, "≈": 584, "⁄": 300}

MONO_ADV = 620  # Menlo is 602/1000; rounded up for the same reason as _EXTRA.

SANS = "Helvetica, Arial, Liberation Sans, sans-serif"
MONO = "Menlo, DejaVu Sans Mono, Courier New, monospace"


def text_w(s: str, size: float, *, bold: bool = False, mono: bool = False) -> float:
    """Advance width of ``s`` in px. Exact for Helvetica/Arial; conservative elsewhere."""
    if mono:
        return len(s) * size * MONO_ADV / 1000.0
    table = _BOLD if bold else _REG
    return sum(_EXTRA.get(c, table.get(c, 600)) for c in s) * size / 1000.0


def wrap(text: str, max_px: float, size: float, *, bold: bool = False) -> list[str]:
    """Greedy wrap against measured widths. Never truncates — a clipped sentence reads as a
    finished one, and the reader has no way to tell."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if not cur or text_w(trial, size, bold=bold) <= max_px:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


# --------------------------------------------------------------------------------------
# Palette — light, print-safe, and colour is never the only carrier of meaning.
# --------------------------------------------------------------------------------------

PAPER = "#FFFFFF"
CARD = "#FFFFFF"
CARD_EDGE = "#D8DEE9"
TITLE = "#16202E"
BODY = "#5A6678"
FAINT = "#8A94A6"
FLOW = "#D2564B"  # the dashed convergence/fan-out
INFER = "#C77D1E"  # the opt-in inference path, amber and dashed
LINK = "#B9C2CF"  # source → reader, a plain short connector

COLUMNS: list[tuple[str, str, str, str]] = [
    # (key, header, rule colour, column tint)
    ("sources", "SOURCES · MANY PROJECTS", "#6B7F3A", "#F6FAF0"),
    ("extraction", "1 · EXTRACTION", "#5B5AA6", "#F2F2FB"),
    ("vocabulary", "2 · VOCABULARY", "#C0473E", "#FFFFFF"),
    ("enrichment", "3 · ENRICHMENT PASSES", "#3F8A5B", "#F1FAF3"),
    ("store", "4 · GROUNDED STORE", "#2F6DA8", "#F1F6FC"),
    ("query", "5 · QUERY LAYER", "#A8447C", "#FDF2F8"),
    ("projections", "PROJECTIONS", "#2F6DA8", "#F2F7FC"),
]

W = 2000
PAD = 52
GUTTER = 52
COL_W = {
    "sources": 250,
    "extraction": 208,
    "vocabulary": 176,
    "enrichment": 256,
    "store": 232,
    "query": 214,
    "projections": 248,
}

HEAD_Y = 62  # baseline of the column headers
RULE_Y = 78  # the coloured rule under each header
BODY_TOP = 104  # where each column's content starts


def col_x(key: str) -> float:
    x: float = PAD
    for k, *_ in COLUMNS:
        if k == key:
            return x
        x += COL_W[k] + GUTTER
    raise KeyError(key)


# --------------------------------------------------------------------------------------
# Geometry ledger — every box drawn is recorded, so `verify()` can prove they do not collide.
# --------------------------------------------------------------------------------------


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    what: str
    owner: str = ""

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def contains(self, other: Box) -> bool:
        return (
            self.x <= other.x + 0.5
            and self.y <= other.y + 0.5
            and self.x2 >= other.x2 - 0.5
            and self.y2 >= other.y2 - 0.5
        )

    def overlaps(self, other: Box, *, slack: float = 0.0) -> bool:
        """Partial intersection only. A chip drawn *inside* its card is nesting, not a
        collision, and flagging it would train the reader to ignore this check."""
        if self.contains(other) or other.contains(self):
            return False
        return (
            self.x < other.x2 - slack
            and other.x < self.x2 - slack
            and self.y < other.y2 - slack
            and other.y < self.y2 - slack
        )


@dataclass
class Canvas:
    parts: list[str] = field(default_factory=list)
    cards: list[Box] = field(default_factory=list)
    texts: list[Box] = field(default_factory=list)

    # -- primitives ---------------------------------------------------------------------

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str,
        stroke: str,
        r: float = 8,
        sw: float = 1,
        dash: str = "",
        record: str = "",
        owner: str = "",
    ) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" rx="{_n(r)}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>'
        )
        if record:
            self.cards.append(Box(x, y, w, h, record, owner))

    def text(
        self,
        x: float,
        y: float,
        s: str,
        *,
        size: float,
        fill: str,
        bold: bool = False,
        mono: bool = False,
        anchor: str = "start",
        spacing: float = 0.0,
        owner: str = "",
        italic: bool = False,
    ) -> None:
        """One line of text. Its measured box is recorded for the collision check."""
        width = text_w(s, size, bold=bold, mono=mono) + spacing * max(0, len(s) - 1)
        left = x if anchor == "start" else (x - width if anchor == "end" else x - width / 2)
        extra = f' letter-spacing="{spacing}"' if spacing else ""
        extra += ' font-style="italic"' if italic else ""
        self.parts.append(
            f'<text x="{_n(x)}" y="{_n(y)}" text-anchor="{anchor}" '
            f'font-family="{MONO if mono else SANS}" font-size="{_n(size)}" '
            f'font-weight="{700 if bold else 400}" fill="{fill}"{extra}>{esc(s)}</text>'
        )
        # Ascent/descent box: Helvetica's cap height is ~0.717em and descender ~0.207em.
        self.texts.append(Box(left, y - size * 0.78, width, size * 1.0, f"text:{s[:38]}", owner))

    def path(self, d: str, *, stroke: str, sw: float = 1.2, dash: str = "", marker: str = "") -> None:
        da = f' stroke-dasharray="{dash}"' if dash else ""
        mk = f' marker-end="url(#{marker})"' if marker else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}"{da}{mk}/>')


def _n(v: float) -> str:
    """Trim floats so the output is byte-stable across runs and platforms."""
    return f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------------------
# Content. Transcribed from the specification, not invented here.
# --------------------------------------------------------------------------------------

SOURCES = [
    ("Source code", "many languages, many repositories"),
    ("Product & user docs", "guides, references, change logs"),
    ("Institutional knowledge", "design records, decision records, runbooks, wikis"),
    ("Defect databases", "reports, regressions, root causes"),
    ("Issue trackers & epics", "work items, epics, parent and blocker links"),
    ("Schemas & contracts", "migrations, API contracts, event payloads"),
]

READERS = [
    ("code reader", "parse → symbols"),
    ("doc reader", "split → sections"),
    ("record reader", "read → decisions"),
    ("defect reader", "read → reports"),
    ("tracker reader", "read → work items"),
    ("schema reader", "read → contracts"),
]

ENTITY_KINDS = ["file", "symbol", "module", "document", "section", "work item", "defect", "contract"]
RELATION_KINDS = ["calls", "imports", "defines", "describes", "touches", "depends on", "derives from"]

PASSES = [
    (
        "Reference resolution",
        "Bind call sites to definitions across files, packages and repository boundaries.",
    ),
    (
        "Documentation binding",
        "Attach each document section to the code it describes — and record where it no longer does.",
    ),
    (
        "Schema folding",
        "Fold migrations into the schema they produce; tie contracts to the code that satisfies them.",
    ),
    (
        "Work linking",
        "Connect work items and defects to the components they touched, via the commits that closed them.",
    ),
]

QUESTIONS = [
    "who calls this?",
    "what breaks if I change it?",
    "which documents describe this?",
    "which issues touched it?",
]

PROJECTIONS = [
    ("Committed knowledge", "Human-readable summaries written back into the repo and reviewed like code."),
    ("Dashboards", "Coverage, undocumented surfaces, defect hot spots, drift over time."),
    ("Interchange exports", "The same facts in a portable graph format for external tooling and analysis."),
    (
        "Grounded agent inputs",
        "Retrieval that returns facts with citations, so an answer can be verified, not trusted.",
    ),
    (
        "Cross-project patterns",
        "Recurring designs, shared idioms, and drift between what documents claim and what the code does.",
    ),
]


# --------------------------------------------------------------------------------------
# Card builders. Every height is derived from wrapped content; none is a constant.
# --------------------------------------------------------------------------------------


def titled_card(
    c: Canvas,
    x: float,
    y: float,
    w: float,
    title: str,
    detail: str,
    *,
    owner: str,
    title_size: float = 13,
    detail_size: float = 9.5,
    pad: float = 13,
    stroke: str = CARD_EDGE,
    fill: str = CARD,
    detail_mono: bool = False,
) -> float:
    """A card whose height is whatever its wrapped detail needs. Returns that height."""
    inner = w - 2 * pad
    lines = wrap(detail, inner, detail_size) if detail else []
    lead = detail_size * 1.42
    height = pad + title_size * 0.78 + (8 + len(lines) * lead if lines else 0) + pad - 2
    c.rect(x, y, w, height, fill=fill, stroke=stroke, record=f"card:{title}", owner=owner)
    c.text(x + pad, y + pad + title_size * 0.78, title, size=title_size, fill=TITLE, bold=True, owner=owner)
    for i, line in enumerate(lines):
        c.text(
            x + pad,
            y + pad + title_size * 0.78 + 8 + (i + 1) * lead - lead * 0.28,
            line,
            size=detail_size,
            fill=BODY,
            mono=detail_mono,
            owner=owner,
        )
    return height


def note(
    c: Canvas, x: float, y: float, w: float, text: str, *, owner: str, size: float = 9, bold: bool = False
) -> float:
    """A free-standing footnote under a column. Returns its height."""
    lines = wrap(text, w, size, bold=bold)
    lead = size * 1.5
    for i, line in enumerate(lines):
        c.text(x, y + (i + 1) * lead - lead * 0.3, line, size=size, fill=FAINT, bold=bold, owner=owner)
    return len(lines) * lead


# --------------------------------------------------------------------------------------
# Columns
# --------------------------------------------------------------------------------------


def build(c: Canvas) -> tuple[float, dict[str, list[tuple[float, float]]], dict[str, float]]:
    """Draw every column.

    Returns the content height, the anchor points the flow lines use, and **each column's own
    bottom** — the tints are drawn to those rather than to one shared height. Store and query
    hold a single card each; tinting them to the tallest column left two empty coloured slabs
    down the middle of the figure, and the provenance band then sat on top of both of them.
    """
    anchors: dict[str, list[tuple[float, float]]] = {}
    bottoms: dict[str, float] = {}
    # Declared once, as floats: every card height is derived from wrapped text and is therefore
    # fractional, and these cursors accumulate them column by column.
    x: float
    w: float
    y: float
    h: float
    pts: list[tuple[float, float]]
    ins: list[tuple[float, float]]
    outs: list[tuple[float, float]]
    cy: float
    pad: float
    height: float

    # --- 1. sources --------------------------------------------------------------------
    key = "sources"
    x, w = col_x(key), COL_W[key]
    y = BODY_TOP
    pts = []
    for title, detail in SOURCES:
        h = titled_card(c, x, y, w, title, detail, owner=key)
        pts.append((x + w, y + h / 2))
        y += h + 12
    anchors["sources_out"] = pts
    y += 10
    y += note(c, x, y, w, "Stacked cards: every row arrives from many repos.", owner=key)
    y += 8
    y += note(
        c, x, y, w, "One source type spans many projects; one project gives many source types.", owner=key
    )
    bottoms[key] = y

    # --- 2. extraction -----------------------------------------------------------------
    key = "extraction"
    x, w = col_x(key), COL_W[key]
    y = BODY_TOP
    ins, outs = [], []
    for title, detail in READERS:
        h = titled_card(c, x, y, w, title, detail, owner=key, title_size=12, detail_size=9.5)
        ins.append((x, y + h / 2))
        outs.append((x + w, y + h / 2))
        y += h + 12
    anchors["readers_in"], anchors["readers_out"] = ins, outs
    y += 10
    y += note(
        c,
        x,
        y,
        w,
        "One front-end per source type, all emitting the same shape — so adding a source is "
        "additive, and nothing downstream changes.",
        owner=key,
    )
    bottoms[key] = y

    # --- 3. vocabulary (the narrow waist) ----------------------------------------------
    key = "vocabulary"
    x, w = col_x(key), COL_W[key]
    y = BODY_TOP
    pad = 13
    inner = w - 2 * pad
    cur = y + 26
    rows: list[tuple[str, float, float, bool, bool]] = []  # (text, dy, size, bold, mono)
    rows.append(("NARROW WAIST", cur, 9.5, True, False))
    cur += 26
    rows.append(("ENTITY KINDS", cur, 8.5, True, False))
    cur += 16
    for k in ENTITY_KINDS:
        rows.append((k, cur, 10, False, True))
        cur += 13
    cur += 12
    rows.append(("RELATION KINDS", cur, 8.5, True, False))
    cur += 16
    for k in RELATION_KINDS:
        rows.append((k, cur, 10, False, True))
        cur += 13
    cur += 14
    closed = wrap(
        "A closed set. Every source collapses into these kinds; a new source adds no kinds.", inner, 8.5
    )
    for line in closed:
        rows.append((line, cur, 8.5, False, False))
        cur += 12
    cur += 12
    rows.append(("Every entity carries", cur, 8.5, False, False))
    cur += 12
    rows.append(("__ITALIC__its origin, always.", cur, 8.5, False, False))
    cur += 14
    height = cur - y
    c.rect(x, y, w, height, fill="#FFFFFF", stroke=FLOW, dash="5 4", record="card:narrow waist", owner=key)
    for text, dy, size, bold, mono in rows:
        italic = text.startswith("__ITALIC__")
        s = text.removeprefix("__ITALIC__")
        colour = FLOW if (bold and size >= 9) or italic else (TITLE if bold else BODY)
        if mono:
            colour = TITLE
        c.text(
            x + pad,
            dy,
            s,
            size=size,
            fill=colour,
            bold=bold,
            mono=mono,
            italic=italic,
            spacing=1.2 if bold else 0.0,
            owner=key,
        )
    # A separator between the two kind lists, drawn at the gap we already reserved.
    sep = next(dy for text, dy, *_ in rows if text == "RELATION KINDS") - 22
    c.path(f"M{_n(x + pad)} {_n(sep)} H{_n(x + w - pad)}", stroke="#EED9D7", sw=1)
    anchors["waist_in"] = [(x, y + height * 0.42)]
    anchors["waist_out"] = [(x + w, y + height * 0.42)]
    ly = y + height + 26
    c.text(x, ly, "READING THE LINES", size=8, fill=FAINT, bold=True, spacing=1.4, owner=key)
    ly += 16
    for colour, dash, text in (
        (LINK, "", "one source, one reader"),
        (FLOW, "4 4", "extracted facts"),
        (INFER, "2 5", "inferred, opt-in"),
    ):
        c.path(f"M{_n(x)} {_n(ly - 3)} h22", stroke=colour, dash=dash, sw=1.4)
        c.text(x + 30, ly, text, size=8.5, fill=FAINT, owner=key)
        ly += 15
    bottoms[key] = ly

    # --- 4. enrichment passes ----------------------------------------------------------
    key = "enrichment"
    x, w = col_x(key), COL_W[key]
    y = BODY_TOP
    ins, outs = [], []
    for title, detail in PASSES:
        h = titled_card(c, x, y, w, title, detail, owner=key)
        ins.append((x, y + h / 2))
        outs.append((x + w, y + h / 2))
        y += h + 13
    # The opt-in inference card — a different class, drawn differently and said so.
    h = titled_card(
        c,
        x,
        y,
        w,
        "Inference — opt-in",
        "Model-proposed relations, each labelled and scored: conf 0.72. Stored apart from the facts.",
        owner=key,
        fill="#FDF6E7",
        stroke=INFER,
    )
    anchors["infer_out"] = [(x + w, y + h / 2)]
    anchors["enrich_in"], anchors["enrich_out"] = ins, outs
    y += h + 14
    y += note(
        c,
        x,
        y,
        w,
        "Passes relate what extraction could only see in isolation. Each reads the graph and "
        "writes back only new relations — never new kinds.",
        owner=key,
    )
    bottoms[key] = y

    # --- 5. grounded store -------------------------------------------------------------
    key = "store"
    x, w = col_x(key), COL_W[key]
    pad = 14
    inner = w - 2 * pad
    body_a = wrap("Content-addressed: identical input yields identical facts.", inner, 9.5)
    body_b = wrap(
        "Versioned: the graph is a build artifact tied to a commit, not a one-time crawl.", inner, 9.5
    )
    body_c = wrap(
        "Built only from a clean tree. A dirty source marks the graph untrusted rather than "
        "silently answering from it.",
        inner,
        9.5,
    )
    body_d = wrap(
        "Facts and hypotheses are stored as separate classes; a query returns facts unless it opts in.",
        inner,
        9,
    )
    lead = 13.5
    height = (
        pad
        + 14
        + 12
        + len(body_a) * lead
        + 8
        + len(body_b) * lead
        + 14
        + 24
        + 12
        + len(body_c) * lead
        + 16
        + len(body_d) * 12.5
        + 14
        + 16
        + pad
    )
    y = BODY_TOP + 84  # centred against the taller columns either side
    c.rect(x, y, w, height, fill=CARD, stroke=CARD_EDGE, record="card:Grounded store", owner=key)
    cy = y + pad + 14
    c.text(x + pad, cy, "Grounded store", size=13, fill=TITLE, bold=True, owner=key)
    cy += 12
    for line in body_a:
        cy += lead
        c.text(x + pad, cy, line, size=9.5, fill=BODY, owner=key)
    cy += 8
    for line in body_b:
        cy += lead
        c.text(x + pad, cy, line, size=9.5, fill=BODY, owner=key)
    cy += 14
    chip = "graph @ 4f1c9ab · clean"
    chip_w = text_w(chip, 9.5, mono=True) + 24
    c.rect(x + pad, cy, chip_w, 24, fill="#F2F6FC", stroke="#CFDCEC", r=12, record="chip:commit", owner=key)
    c.text(x + pad + 12, cy + 15.5, chip, size=9.5, fill="#2F6DA8", mono=True, owner=key)
    cy += 24 + 12
    for line in body_c:
        cy += lead
        c.text(x + pad, cy, line, size=9.5, fill=BODY, owner=key)
    cy += 16
    c.path(f"M{_n(x + pad)} {_n(cy)} H{_n(x + w - pad)}", stroke="#E7ECF3", sw=1)
    # The opt-in path enters here rather than at the facts inlet, so the picture says what the
    # sentence below it says: hypotheses are a separate class, not a variety of fact.
    anchors["store_in_infer"] = [(x, cy + 6)]
    for line in body_d:
        cy += 12.5
        c.text(x + pad, cy, line, size=9, fill=FAINT, owner=key)
    cy += 14
    c.text(
        x + w / 2, cy + 4, "one build · many projects", size=9.5, fill="#2F6DA8", anchor="middle", owner=key
    )
    anchors["store_in"] = [(x, y + height * 0.45)]
    anchors["store_out"] = [(x + w, y + height * 0.45)]
    bottoms[key] = y + height
    store_bottom = y + height

    # --- 6. query layer ----------------------------------------------------------------
    key = "query"
    x, w = col_x(key), COL_W[key]
    pad = 14
    inner = w - 2 * pad
    intro = wrap("Answers questions instead of making callers walk edges.", inner, 9.5)
    q_lines = [wrap(q, inner, 11, bold=True) for q in QUESTIONS]
    tail = wrap(
        "Bounded views say so: results state their cap and depth, and a summary is labelled as one.",
        inner,
        9,
    )
    height = (
        pad
        + 14
        + 12
        + len(intro) * 13.5
        + 18
        + sum(len(q) * 15 + 12 for q in q_lines)
        + 10
        + 16
        + len(tail) * 12.5
        + pad
    )
    y = BODY_TOP + 116
    c.rect(x, y, w, height, fill=CARD, stroke=CARD_EDGE, record="card:Query layer", owner=key)
    cy = y + pad + 14
    c.text(x + pad, cy, "Query layer", size=13, fill=TITLE, bold=True, owner=key)
    cy += 12
    for line in intro:
        cy += 13.5
        c.text(x + pad, cy, line, size=9.5, fill=BODY, owner=key)
    cy += 18
    for q in q_lines:
        for line in q:
            cy += 15
            c.text(x + pad, cy, line, size=11, fill=TITLE, owner=key)
        cy += 12
    cy += 10
    c.path(f"M{_n(x + pad)} {_n(cy)} H{_n(x + w - pad)}", stroke="#F0DCE8", sw=1)
    for line in tail:
        cy += 12.5
        c.text(x + pad, cy, line, size=9, fill=FAINT, owner=key)
    anchors["query_in"] = [(x, y + height * 0.45)]
    anchors["query_out"] = [(x + w, y + height * 0.45)]
    bottoms[key] = y + height

    # --- 7. projections ----------------------------------------------------------------
    key = "projections"
    x, w = col_x(key), COL_W[key]
    y = BODY_TOP
    ins = []
    for title, detail in PROJECTIONS:
        h = titled_card(c, x, y, w, title, detail, owner=key)
        ins.append((x, y + h / 2))
        y += h + 13
    anchors["proj_in"] = ins
    y += 8
    y += note(c, x, y, w, "Only possible when several projects share one graph.", owner=key, bold=True)
    y += 8
    y += note(c, x, y, w, "A single repository cannot show what is common across an org.", owner=key)
    bottoms[key] = y

    # --- the provenance band, under the store and query columns ------------------------
    band_x = col_x("store")
    band_w = col_x("query") + COL_W["query"] - band_x
    band_y = max(store_bottom, bottoms["query"]) + 46
    band_h = _provenance_band(c, band_x, band_y, band_w)
    bottoms["band"] = band_y + band_h

    return max(bottoms.values()), anchors, bottoms


def _provenance_band(c: Canvas, x: float, y: float, w: float) -> float:
    """One fact, with its origin — the idea the whole diagram exists to make visible."""
    owner = "band"
    c.text(x, y, "ONE FACT, WITH ITS ORIGIN", size=9, fill=FAINT, bold=True, spacing=1.6, owner=owner)
    cy = y + 30
    fact = "calls( auth.verify → tokens.decode )"
    c.text(x, cy, fact, size=15, fill=TITLE, mono=True, owner=owner)
    cy += 20
    chips = ["auth/verify.rs:214", "decision-014 §3", "issue 1182 · closed"]
    cx = x
    for chip in chips:
        cw = text_w(chip, 9.5, mono=True) + 22
        if cx + cw > x + w:  # never let a chip run past the band it belongs to
            cx = x
            cy += 30
        c.rect(cx, cy, cw, 24, fill="#F5F7FA", stroke="#DFE5EE", r=12, record=f"chip:{chip}", owner=owner)
        c.text(cx + 11, cy + 15.5, chip, size=9.5, fill=BODY, mono=True, owner=owner)
        cx += cw + 10
    cy += 24 + 16
    lines = wrap(
        "Nothing is asserted that cannot be pointed at. A fact traces to a file and line, "
        "a document and section, or a work item — so an answer can always be checked.",
        w,
        9,
    )
    for line in lines:
        c.text(x, cy, line, size=9, fill=FAINT, owner=owner)
        cy += 12.5
    return cy - y


# --------------------------------------------------------------------------------------
# Flow lines. Drawn last so they sit above the tints and below nothing that matters.
# --------------------------------------------------------------------------------------


def flows(c: Canvas, a: dict[str, list[tuple[float, float]]]) -> None:
    def curve(
        p1: tuple[float, float], p2: tuple[float, float], *, stroke: str, dash: str, marker: str
    ) -> None:
        x1, y1 = p1
        x2, y2 = p2
        dx = max(16.0, (x2 - x1) * 0.5)
        c.path(
            f"M{_n(x1)} {_n(y1)} C{_n(x1 + dx)} {_n(y1)} {_n(x2 - dx)} {_n(y2)} {_n(x2)} {_n(y2)}",
            stroke=stroke,
            dash=dash,
            marker=marker,
        )

    # source → its reader: one to one, a plain connector rather than the dashed flow, because
    # nothing converges here yet.
    for p1, p2 in zip(a["sources_out"], a["readers_in"], strict=True):
        curve(p1, p2, stroke=LINK, dash="", marker="arrow-link")

    # readers → the waist: the convergence the whole design turns on.
    for p1 in a["readers_out"]:
        curve(p1, a["waist_in"][0], stroke=FLOW, dash="4 4", marker="arrow-flow")

    # the waist → each enrichment pass: the same bus fanning back out.
    for p2 in a["enrich_in"]:
        curve(a["waist_out"][0], p2, stroke=FLOW, dash="4 4", marker="arrow-flow")

    # enrichment → store, and the opt-in inference path beside it, deliberately distinct.
    for p1 in a["enrich_out"]:
        curve(p1, a["store_in"][0], stroke=FLOW, dash="4 4", marker="arrow-flow")
    curve(a["infer_out"][0], a["store_in_infer"][0], stroke=INFER, dash="2 5", marker="arrow-infer")

    # store → query → projections.
    curve(a["store_out"][0], a["query_in"][0], stroke=FLOW, dash="4 4", marker="arrow-flow")
    for p2 in a["proj_in"]:
        curve(a["query_out"][0], p2, stroke=FLOW, dash="4 4", marker="arrow-flow")


# --------------------------------------------------------------------------------------
# The check that makes "nothing overlaps" a result rather than an opinion
# --------------------------------------------------------------------------------------


def verify(c: Canvas, width: float, height: float) -> list[str]:
    """Every text run inside a card it belongs to; no card overlapping another in its column."""
    problems: list[str] = []

    for t in c.texts:
        if t.x < -0.5 or t.x2 > width + 0.5 or t.y < -0.5 or t.y2 > height + 0.5:
            problems.append(f"text outside canvas: {t.what!r} at ({t.x:.0f},{t.y:.0f})")

    # A text run must not stick out of the card that contains its origin.
    for t in c.texts:
        holders = [
            b for b in c.cards if b.x <= t.x + 1 and t.x < b.x2 and b.y <= t.y + 2 and t.y2 <= b.y2 + 12
        ]
        if not holders:
            continue
        card = min(holders, key=lambda b: b.w)
        if t.x2 > card.x2 - 3:
            problems.append(
                f"text overflows {card.what!r}: {t.what!r} ends at {t.x2:.1f} > {card.x2 - 3:.1f}"
            )

    # Cards in the same column must not touch.
    by_owner: dict[str, list[Box]] = {}
    for b in c.cards:
        by_owner.setdefault(b.owner, []).append(b)
    for owner, boxes in by_owner.items():
        for i, b1 in enumerate(boxes):
            for b2 in boxes[i + 1 :]:
                if b1.overlaps(b2, slack=0.5):
                    problems.append(f"cards overlap in {owner!r}: {b1.what!r} and {b2.what!r}")

    # Text from one column must not stray into another's.
    spans = {k: (col_x(k), col_x(k) + COL_W[k]) for k in COL_W}
    for t in c.texts:
        if t.owner not in spans:
            continue
        lo, hi = spans[t.owner]
        if t.x < lo - 1 or t.x2 > hi + 1:
            problems.append(f"text leaves column {t.owner!r}: {t.what!r} spans {t.x:.0f}–{t.x2:.0f}")

    return problems


# --------------------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------------------


def render() -> str:
    c = Canvas()
    content_h, anchors, bottoms = build(c)
    height = content_h + 46

    # Column tints and headers, painted first so every card sits on top of them.
    chrome: list[str] = []
    for key, header, rule, tint in COLUMNS:
        x, w = col_x(key), COL_W[key]
        if tint != "#FFFFFF":
            bottom = bottoms.get(key, content_h) + 20
            chrome.append(
                f'<rect x="{_n(x - 14)}" y="{_n(RULE_Y - 34)}" width="{_n(w + 28)}" '
                f'height="{_n(bottom - RULE_Y + 34)}" rx="10" fill="{tint}"/>'
            )
        chrome.append(f'<rect x="{_n(x)}" y="{_n(RULE_Y)}" width="{_n(w)}" height="3" fill="{rule}"/>')
        chrome.append(
            f'<text x="{_n(x)}" y="{_n(HEAD_Y)}" font-family="{SANS}" font-size="10.5" '
            f'font-weight="700" fill="{rule}" letter-spacing="2.2">{esc(header)}</text>'
        )

    problems = verify(c, W, height)
    if problems:
        raise SystemExit("layout check failed:\n  " + "\n  ".join(problems))

    flow_parts = Canvas()
    flows(flow_parts, anchors)

    defs = "".join(
        f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5.5" '
        f'markerHeight="5.5" orient="auto-start-reverse">'
        f'<path d="M0 1 L9 5 L0 9 z" fill="{colour}"/></marker>'
        for mid, colour in (("arrow-flow", FLOW), ("arrow-link", LINK), ("arrow-infer", INFER))
    )

    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {W} {_n(height)}" role="img">'
        f"<title>Knowledge Foundation Architecture</title>"
        f"<desc>Many sources across many repositories — code, documentation, institutional records, "
        f"defects, work items and schemas — are read by one front-end each, collapsed into a single "
        f"closed vocabulary of entity and relation kinds, related by enrichment passes, and settled "
        f"into a content-addressed store tied to a commit. A query layer answers questions over it, "
        f"and the same facts project out as committed knowledge, dashboards, interchange exports, "
        f"grounded agent inputs and cross-project patterns. Every fact carries its origin; "
        f"model-inferred relations are a separate, opt-in class.</desc>"
        f"<defs>{defs}</defs>"
        f'<rect x="0" y="0" width="{W}" height="{_n(height)}" fill="{PAPER}"/>'
    )
    return (
        head
        + "\n"
        + "\n".join(chrome)
        + "\n"
        + "\n".join(flow_parts.parts)
        + "\n"
        + "\n".join(c.parts)
        + "\n</svg>\n"
    )


def main(argv: list[str]) -> int:
    svg = render()
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != svg:
            print(
                f"{OUT.relative_to(REPO)} is out of date — run scripts/render_knowledge_foundation_svg.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUT.relative_to(REPO)} is current, and its layout checks pass")
        return 0
    OUT.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)} ({len(svg):,} bytes)")
    _rasterise()
    return 0


def _rasterise() -> None:
    """Write the PNG beside the SVG, on the same command, so the two cannot drift."""
    tool = shutil.which("rsvg-convert")
    png = OUT.with_suffix(".png")
    if tool is None:
        print(f"  (rsvg-convert not found — {png.name} not refreshed; `brew install librsvg`)")
        return
    subprocess.run([tool, "-w", "2800", str(OUT), "-o", str(png)], check=True)  # noqa: S603
    print(f"wrote {png.relative_to(REPO)} ({png.stat().st_size:,} bytes)")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
