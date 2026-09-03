"""Render `assets/spine-architecture.svg` — the platform diagram, with its numbers read from source.

**Why this is a generator and not a drawing.** The PNG it replaces was stamped `3.8.4`, claimed
`41 commands` against an actual 53, and carried `7 node kinds · 9 edge kinds` — figures
`ARCHITECTURE.md` had corrected two releases earlier while the image kept the old ones. A picture
nobody can regenerate goes stale silently, and there is no check that would ever notice.

So every number on this diagram is computed here, at render time, from the thing it describes:
the version from `pyproject.toml`, the command count from `cli/`, the node and edge kinds from
`pkg.facts`, the front-end count from the extractor's dispatch table. Re-run it and the picture is
true again; if it cannot be re-run, that is a build failure rather than a slow drift.

    uv run python scripts/render_architecture_svg.py            # write the SVG (+ PNG if possible)
    uv run python scripts/render_architecture_svg.py --check    # fail if it is out of date

The SVG is the source. A PNG is rasterised beside it when `rsvg-convert` is on PATH, because
`README.md` embeds the raster: GitHub renders an `<img>` from `raw.githubusercontent.com`
reliably for PNG and inconsistently for SVG, and a hero image that sometimes fails to load is
worse than one that is merely large.

The layout is computed, seeded and deterministic — same input, same bytes — for the same reason
every other visual surface in this repo is (CLAUDE.md, invariant 3). No random placement, no
force layout: a picture that redraws differently for an identical commit cannot be diffed.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "assets" / "spine-architecture.svg"

# Palette — matches `pkg-hero.svg` and the banner, so the assets read as one set.
INK = "#0B1020"
PANEL = "#111A31"
EDGE = "#2A3757"
TEAL = "#2DD4BF"
TEAL_DIM = "#5DCAA5"
AMBER = "#E5A44C"
TEXT = "#F5F7FA"
MUTED = "#C9D2E0"
FAINT = "#8A94A6"
MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"
SANS = "Inter, -apple-system, Segoe UI, Arial, sans-serif"

W = 1600
PAD = 64
COL_GAP = 20


# --------------------------------------------------------------------------------------
# Facts. Every one is read, never typed.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    version: str
    commands: int
    node_kinds: int
    edge_kinds: int
    languages: int
    verifiers: int

    @classmethod
    def read(cls) -> Facts:
        from orchestrator.pkg.facts import EdgeKind, NodeKind

        version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
        commands = sum(
            len(re.findall(r"\.command\(", p.read_text()))
            for p in (REPO / "src/orchestrator/cli").glob("*.py")
        )
        dispatch = (REPO / "src/orchestrator/pkg/extractor.py").read_text()
        # The dispatch table names one module per front-end; `default` is the Python fall-through,
        # so it counts as a language rather than being excluded.
        languages = len({m for m in re.findall(r"(\w+)_extractor", dispatch)})
        verifiers = len(
            [p for p in (REPO / "src/orchestrator/runtime/verifiers").glob("*.py") if p.stem != "__init__"]
        )
        return cls(version, commands, len(list(NodeKind)), len(list(EdgeKind)), languages, verifiers)


# --------------------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------------------


def atext(
    x: int,
    y: int,
    text: str,
    *,
    size: int,
    fill: str,
    anchor: str = "start",
    weight: int = 400,
    extra: str = "",
) -> str:
    """A `<text>` with an explicit anchor — used where copy is right-aligned or centred."""
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{SANS}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}"{extra}>{esc(text)}</text>'
    )


def rect(x: int, y: int, w: int, h: int, *, fill: str, stroke: str, r: int = 10, sw: float = 1.5) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
    )


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def label(
    x: int, y: int, text: str, *, size: int, fill: str, weight: int = 400, font: str = SANS, extra: str = ""
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}"{extra}>{esc(text)}</text>'
    )


def section(y: int, letter: str, title: str, blurb: str, badge: str = "") -> tuple[str, int]:
    """A section heading, its one-line blurb, and an optional right-aligned badge.

    The badge is pinned to the right margin rather than trailing the title. Measuring a
    letter-spaced uppercase run to place it after the text put it *through* the title on the
    first render — and a diagram is exactly the artefact where a layout bug is invisible until
    someone opens it.
    """
    out = [
        label(
            PAD, y, f"{letter} · {title}", size=17, fill=TEAL_DIM, weight=600, extra=' letter-spacing="2.5"'
        )
    ]
    if badge:
        width = len(badge) * 8 + 24
        bx = W - PAD - width
        out.append(rect(bx, y - 17, width, 24, fill="none", stroke=TEAL, r=12, sw=1))
        out.append(label(bx + 12, y, badge, size=13, fill=TEAL, font=MONO))
    out.append(label(PAD, y + 32, blurb, size=19, fill=MUTED))
    return "\n".join(out), y + 60


def cards(y: int, items: list[tuple[str, str, str]]) -> tuple[str, int]:
    """A row of equal cards: (title, detail, package).

    Height is derived from the tallest detail in the row, never fixed. A fixed height ran the
    second wrapped line straight through the package label on the first render.
    """
    n = len(items)
    width = (W - 2 * PAD - COL_GAP * (n - 1)) // n
    wrapped = [_wrap(detail, width - 40) for _, detail, _ in items]
    lines = max(len(w) for w in wrapped)
    height = 62 + lines * 21 + 26
    out = []
    for i, ((title, _, pkg), detail_lines) in enumerate(zip(items, wrapped, strict=True)):
        x = PAD + i * (width + COL_GAP)
        out.append(rect(x, y, width, height, fill=PANEL, stroke=EDGE))
        out.append(label(x + 20, y + 34, title, size=20, fill=TEXT, weight=600))
        for j, line in enumerate(detail_lines):
            out.append(label(x + 20, y + 60 + j * 21, line, size=15, fill=MUTED))
        if pkg:
            out.append(label(x + 20, y + height - 16, pkg, size=13, fill=FAINT, font=MONO))
    return "\n".join(out), y + height + 30


def _wrap(text: str, px: int) -> list[str]:
    """Greedy wrap at ~7.6px per character — the measured average for Inter at 15px."""
    budget = max(1, int(px / 7.6))
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if len(trial) <= budget:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    # No truncation. The band's right-hand copy lost "Everything above and below reads from it."
    # to a `[:2]` on the first render, which reads as a finished sentence and is not one.
    return lines


def band(y: int, title: str, sub: str, body: str, *, fill: str, stroke: str) -> tuple[str, int]:
    body_lines = _wrap(body, 760)
    height = max(92, 30 + len(body_lines) * 22 + 30)
    out = [rect(PAD, y, W - 2 * PAD, height, fill=fill, stroke=stroke)]
    out.append(label(PAD + 24, y + 38, title, size=24, fill=TEXT, weight=700))
    out.append(label(PAD + 24, y + 64, sub, size=14, fill=TEAL_DIM, font=MONO))
    right = W - PAD - 24
    for j, line in enumerate(body_lines):
        out.append(atext(right, y + 40 + j * 22, line, size=15, fill=MUTED, anchor="end"))
    return "\n".join(out), y + height + 30


def gate(y: int, title: str, body: str) -> tuple[str, int]:
    x, width, height = 400, W - 800, 78
    out = [rect(x, y, width, height, fill="#201A12", stroke=AMBER)]
    out.append(atext(W // 2, y + 32, f"🔒 {title}", size=19, fill=AMBER, anchor="middle", weight=700))
    out.append(atext(W // 2, y + 58, body, size=15, fill=MUTED, anchor="middle"))
    return "\n".join(out), y + height + 30


# --------------------------------------------------------------------------------------
# The diagram
# --------------------------------------------------------------------------------------


def render(f: Facts) -> str:
    parts: list[str] = []
    y = 78

    parts.append(
        label(PAD, y, "PLATFORM ARCHITECTURE", size=16, fill=FAINT, weight=500, extra=' letter-spacing="4"')
    )
    y += 54
    parts.append(label(PAD, y, "Spine — the governed delivery loop", size=46, fill=TEXT, weight=700))
    y += 44
    parts.append(
        label(
            PAD,
            y,
            "A request flows top to bottom — through comprehension, deterministic research and planning,",
            size=20,
            fill=MUTED,
        )
    )
    y += 28
    parts.append(
        label(
            PAD,
            y,
            "into an execution loop that pauses at two human gates — and out as a reviewed pull request.",
            size=20,
            fill=MUTED,
        )
    )
    y += 34
    parts.append(label(PAD, y, f"synaptixs-spine · {f.version}", size=16, fill=FAINT, font=MONO))
    y += 52

    block, y = section(
        y,
        "A",
        "SURFACES",
        "You ask for something — from a terminal, your AI assistant, or the web inbox. "
        "Same engine behind all three.",
    )
    parts.append(block)
    block, y = cards(
        y,
        [
            ("Command line", f"{f.commands} commands · the main surface", "cli/"),
            ("Assistant plugin", "Claude Code & Codex call it as tools", "plugin/"),
            ("Web app + API", "the operator inbox — approvals live here", "registry/"),
        ],
    )
    parts.append(block)

    block, y = section(
        y,
        "B",
        "INTAKE & COMPREHENSION",
        "Before writing anything, Spine reads. Your requirement becomes structured intents; "
        "your code and your docs become a graph.",
        badge="deterministic · no LLM",
    )
    parts.append(block)
    block, y = cards(
        y,
        [
            ("Read the requirement", "Confluence · Jira · OpenSpec · files → intents", "intake/"),
            ("Size up the repo", "languages, framework, what kind of job", "catalog/"),
            ("Write it down", "a committed, code-true knowledge base", "knowledge/"),
        ],
    )
    parts.append(block)

    block, y = band(
        y,
        "Product Knowledge Graph",
        "the source of truth · pkg/",
        f"Every fact points at a file and a line. {f.node_kinds} node kinds · {f.edge_kinds} edge kinds, "
        f"across {f.languages} language front-ends, built from code and docs. No LLM — the same "
        "commit always "
        "gives the same graph. Everything above and below reads from it.",
        fill="#16224180",
        stroke=TEAL,
    )
    parts.append(block)

    block, y = section(
        y,
        "C",
        "RESEARCH",
        "Every run starts here. Three deterministic passes compose one Evidence artifact, and "
        "everything after is judged against it.",
        badge="deterministic · no LLM",
    )
    parts.append(block)
    block, y = cards(
        y,
        [
            ("Where it lands", "symbols with file:line, kind, callers, module", "sdlc/investigate"),
            ("Why it broke", "fault site, hypotheses, regression surface", "sdlc/rca"),
            ("What it touches", "blast radius keyed off the landing sites", "sdlc/impact"),
            ("Is the ticket true?", "criteria bound to a file:line, or refused", "sdlc/validity"),
        ],
    )
    parts.append(block)

    block, y = section(
        y,
        "D",
        "PLANNING",
        "The work becomes a typed, validated plan — which tools, which budget, where the "
        "approvals sit. Not free-form prompting.",
    )
    parts.append(block)
    block, y = cards(
        y,
        [
            ("Choose the approach", "objective → agents + tools", "planner/"),
            ("The plan, typed", "validated before anything runs", "ir/ · GraphIR"),
            ("Reusable parts", "versioned templates + tool contracts", "registry/"),
        ],
    )
    parts.append(block)

    block, y = gate(
        y,
        "GATE 1 · approve intents",
        "Stop. A human approves before a single line is generated — nothing has been written yet.",
    )
    parts.append(block)

    block, y = section(
        y,
        "E",
        "GOVERNED EXECUTION LOOP",
        f"Now it runs. Each step is one contract-checked call, verified by {f.verifiers} "
        "verifiers against schema, evidence and policy.",
    )
    parts.append(block)
    block, y = cards(
        y,
        [
            ("Run one step", "perceive → plan → act → observe", "runtime/"),
            ("Check every step", "schema · evidence · policy", "runtime/verifiers"),
            ("Hand out credentials", "only at the moment of use", "gateway/"),
            ("Survive the pause", "resumable across gates and restarts", "temporal/"),
        ],
    )
    parts.append(block)

    block, y = section(
        y,
        "F",
        "SDLC DELIVERY",
        "Code is generated grounded in what already exists and in the Evidence above, tested, "
        "and retried until the tests pass. Then it's reviewed.",
    )
    parts.append(block)
    block, y = cards(
        y,
        [
            ("Build the change", "evidence → code → tests → PR", "sdlc/"),
            ("Write, test, retry", "until the tests are green", "agentic/"),
            ("Review it", "secrets · security · style", "codereview/"),
            ("Same engine, other jobs", "PR reviewer, codebase auditor", "personas/"),
        ],
    )
    parts.append(block)

    block, y = gate(
        y,
        "GATE 2 · approve merge",
        "Stop again. Until this moment nothing has been pushed to your repo or your tracker.",
    )
    parts.append(block)

    block, y = band(
        y,
        "Reviewed, CI-green pull request",
        "the pipeline ends here · deployment stays yours",
        "Every step above was traced and recorded — an append-only trail you can replay, and a Case "
        "you can read back node by node with `sdlc explain`.",
        fill=PANEL,
        stroke=TEAL_DIM,
    )
    parts.append(block)

    block, y = section(y, "G", "SHARED SERVICES", "Used across every layer.")
    parts.append(block)
    block, y = cards(
        y,
        [
            ("Model access", "one client, any provider", "core/"),
            ("Use outside tools", "databases, Atlassian — governed", "mcp/"),
            ("Keep artifacts", "runs, bundles, reports", "storage/"),
            ("Tell someone", "Slack on gates", "notify/"),
            ("Measure it", "the eval harness", "evals/"),
            ("Domain meaning", "optional — off unless configured", "spine/"),
        ],
    )
    parts.append(block)

    y += 10
    legend = [
        (TEAL_DIM, "Flow layer"),
        (AMBER, "Human gate — nothing crosses without you"),
        (TEAL, "Knowledge-graph substrate"),
    ]
    x = PAD
    for colour, text in legend:
        parts.append(rect(x, y - 12, 28, 14, fill="none", stroke=colour, r=4, sw=2))
        parts.append(label(x + 38, y, text, size=15, fill=FAINT))
        x += 48 + len(text) * 8
    y += 44

    height = y
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {W} {height}" role="img">'
        f"<title>Spine platform architecture</title>"
        f"<desc>Surfaces, deterministic comprehension and research over the Product Knowledge Graph, "
        f"typed planning, a governed execution loop with two human gates, and SDLC delivery to a "
        f"reviewed pull request. Generated from source at version {f.version}.</desc>"
        f'<rect x="0" y="0" width="{W}" height="{height}" fill="{INK}"/>'
        f'<rect x="0" y="0" width="{W}" height="6" fill="{TEAL}"/>'
        # The observability rail, drawn once down the right edge.
        f'<line x1="{W - 34}" y1="300" x2="{W - 34}" y2="{height - 120}" stroke="{EDGE}" stroke-width="1.5"/>'
        f'<text x="{W - 20}" y="{(height) // 2}" font-family="{SANS}" font-size="13" fill="{FAINT}" '
        f'letter-spacing="3" transform="rotate(90 {W - 20} {height // 2})" '
        f'text-anchor="middle">OBSERVABILITY · AUDIT</text>'
    )
    return head + "\n" + "\n".join(parts) + "\n</svg>\n"


def main(argv: list[str]) -> int:
    svg = render(Facts.read())
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_text() != svg:
            print(
                f"{OUT.relative_to(REPO)} is out of date — run scripts/render_architecture_svg.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUT.relative_to(REPO)} is current")
        return 0
    OUT.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)} ({len(svg):,} bytes)")
    _rasterise()
    return 0


def _rasterise() -> None:
    """Write the PNG the README embeds. Best-effort: a missing rasteriser is not a build failure.

    The SVG is what is reviewed and diffed; the PNG is a rendering of it. Regenerating one
    without the other is how they drift, so this runs on the same command rather than being a
    step someone has to remember.
    """
    import shutil
    import subprocess

    tool = shutil.which("rsvg-convert")
    png = OUT.with_suffix(".png")
    if tool is None:
        print(f"  (rsvg-convert not found — {png.name} not refreshed; `brew install librsvg`)")
        return
    subprocess.run([tool, "-w", "1600", str(OUT), "-o", str(png)], check=True)  # noqa: S603
    print(f"wrote {png.relative_to(REPO)} ({png.stat().st_size:,} bytes)")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
