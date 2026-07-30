# G5 — Visualization: graph exports and richer deterministic visuals

> **"G5" is a label, not a position in a queue.** It's gap #5 in the Graphify comparison. The specs
> are not ordered and do not run in sequence.

**Status:** Not started.
**Owner:** _unassigned_

## Before you start

**Prerequisites: none. You can start today.**

| What this track needs | State |
|---|---|
| A fact graph to export/visualise | ✅ Exists — you **read** `pkg/facts.py`, you never write it |
| Anything from G2, G3, G4, G6 or the watch-items | Nothing. No shared files. |

If G3 later adds a new node kind for media, this track renders it *then* — that's a follow-up, not a
prerequisite. Do not wait for it: everything already in the graph is worth exporting today.
**Gap:** #5 in [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md) rev. 3.

**One-liner:** close most of the visualization gap by **letting people use their own tools**
(GraphML / DOT / Obsidian / SVG exports) before building any interactive canvas of our own — and
make our built-in visuals richer **without** a bundler or a force-directed layout.

> ⚠️ This spec sits directly on top of two invariants that exist for good reasons. Read
> "The two lines you cannot cross" before designing anything.

---

## Why

Graphify's interactive force-directed `graph.html` — plus Obsidian-vault, Markdown-wiki, SVG and
GraphML exports — is a headline demo experience. Ours is deliberately utilitarian. "Deliberate"
is a real defence for the layout choice, but it is **not** a defence for having no export path at
all: today a user who wants to explore the graph in Gephi, yEd, or Obsidian simply can't.

## The two lines you cannot cross

**1. No force-directed layout.** (Invariant #3.) Any visual surface precomputes positions
deterministically, seeded, in Python. A picture that redraws differently for an identical commit
can't be diffed or reviewed — and diffability is a product property we sell. Graphify's graph
*looks* better partly *because* it doesn't care about this. Do not "fix" our layout by adopting
theirs.

**2. No build step, no heavy client dep.** (Invariant #4.) Vanilla JS, zero npm, no
`node_modules`, no d3/cytoscape-class dependency. The UI ships inside the pip wheel and must work
air-gapped. See the preamble of `registry/api/web/shell.py` and the reasoning in `md.js` (a
~90-line hand-rolled mermaid renderer was chosen over a 2.6 MB library for exactly this reason).

**Interaction is fine. Non-deterministic *layout* is not.** Filtering, searching, collapsing,
zooming, and hovering over a **precomputed** layout are all in bounds.

## What already exists (reuse, don't rebuild)

| Piece | Gives us |
|---|---|
| `pkg/export.py` | `export_sqlite` — the existing kind-per-table projection; the model for new exporters |
| `pkg/rdf.py` | `facts_to_graph` — an RDF projection already exists |
| `knowledge/report_html.py`, `report_svg.py` | Self-contained deterministic report + inline SVG |
| `web/static/md.js` `mermaidSvg()` | Deterministic mermaid → inline SVG (supported subset documented in CLAUDE.md) |
| `scripts/check-mermaid.js` | Verifies diagrams actually render — run it on anything you add |
| `pkg/overview.py` | Bounded view for UIs, with `truncated{}` honesty |

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — Exports (highest value, lowest risk)** | `orchestrator pkg export --format graphml\|dot\|json` + an **Obsidian-vault / Markdown-wiki** writer (a page per module/symbol, `[[wikilinks]]` along edges). Deterministic ordering so exports diff cleanly. | ~4–6 d | A user opens our graph in **Gephi/yEd/Obsidian**; re-running on the same commit produces a byte-identical file |
| **2 — Richer deterministic visuals** | Extend the report/`state` visuals: seeded clustering (community grouping computed in Python — deterministic algorithm, stable tie-breaks), more diagram types, bigger legible graphs. Stays inline-SVG + self-contained. | ~5–7 d | `state --out report.html` shows a clustered architecture view; identical commit → identical bytes |
| **3 — Interaction over a precomputed layout** | In the web UI: filter by kind/area, search, collapse a cluster, click-through to source. Positions come precomputed from Python; JS only shows/hides/pans. No new deps. | ~5–8 d | Explorable graph in-UI; positions provably identical across reloads |

**Phase 1 is the bang-for-buck.** It neutralises most of the comparison ("can I explore the
graph?" → "yes, in your tool of choice") for a fraction of Phase 3's cost and zero invariant risk.
If the program runs short, ship Phase 1 and stop.

## Invariants you must not break

- **Deterministic, seeded layout** — no force/random. If you add clustering, the algorithm must have
  stable tie-breaking (sort by id) so identical input → identical output.
- **No bundler, no npm, no d3/cytoscape.** If a phase seems to require one, it's the wrong phase.
- **Self-contained artifacts** (invariant #5) — an exported/saved report inlines its CSS/SVG and
  fetches nothing. Note `page_shell()` links `/static`, so a *served* page saved to disk loses its
  styling; exports must not depend on it.
- **Bound honestly** — cap nodes rendered and say "top N of M" (`build_overview`'s `truncated{}`).
- Run `node scripts/check-mermaid.js *.md` on any diagram you add or touch.

## Non-goals

- Matching Graphify's force-directed aesthetic. We are trading visual polish for diffability on
  purpose; say so in the docs rather than quietly converging.
- A hosted/SaaS graph explorer.
- Leiden/Louvain **as-is** if the implementation is non-deterministic — only a seeded, stable
  variant qualifies.

## Open questions

1. Obsidian vault vs. plain Markdown wiki — one or both? (Lean: **one writer, two flavours**; the
   page-per-symbol structure is the same. Note `understand` already writes an interlinked
   `episteme/`; check whether this is that writer with a different link syntax rather than a new one.)
2. Should exports live under `pkg export --format` or separate commands? (Lean: **`--format`** —
   `pkg export` already owns projections.)
3. Is Phase 3 worth it at all once Phase 1 exists? **Re-evaluate after Phase 1 ships** — external
   tools may cover the need entirely, and Phase 3 is where the invariant pressure is highest.
