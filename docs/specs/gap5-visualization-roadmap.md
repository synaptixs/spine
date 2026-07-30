# G5 — Visualization: graph exports and richer deterministic visuals

> **"G5" is a label, not a position in a queue.** It's gap #5 in the Graphify comparison. The specs
> are not ordered and do not run in sequence.

**Status:** **Phase 1 SHIPPED** (2026-07-30, branch `feat/g5-visualization`). Phases 2 and 3 not
started — and Phase 3 should not start before the re-evaluation in open question 3.
**Owner:** _unassigned (phases 2–3)_

## Before you start

**Prerequisites: none. You can start today.**

| What this track needs | State |
|---|---|
| A fact graph to export/visualise | ✅ Exists — you **read** `pkg/facts.py`, you never write it |
| Anything from G2, G3, G4, G6 or the watch-items | Nothing. No shared files. |

**Resolved (2026-07-30): G3 shipped in 3.10.0 and added no new node kind.** Media artifacts reuse
`DOC` (`pkg/facts.py`: *"Media (G3) reuses `DOC` — it does not get its own node kind"*), so an
exporter that covers `DOC` covers OCR'd images and transcribed audio for free. There is no
media follow-up to schedule.
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
| **`orchestrator pkg export` (`cli.py`)** | **The command already exists** — SQLite-only, via `--db`. Phase 1 *extends* it with `--format`; it does not build it. See "The `--db` problem" below. |
| `pkg/export.py` | `export_sqlite` — the existing kind-per-table projection; the model for new exporters |
| `pkg/rdf.py` | `facts_to_graph` — an RDF projection already exists |
| **`knowledge/renderers.py` + `episteme/`** | **The page-per-module/area Markdown writer already exists and shipped** (all 4 phases of `pkg-navigable-reports.md`): symbol anchors, source deep-links, backlinks, orphan reaping. This is Phase 1's "Markdown wiki" — see Open Question 1, which is now answered. |
| `knowledge/report_html.py`, `report_svg.py` | Self-contained deterministic report + inline SVG |
| `web/static/md.js` `mermaidSvg()` | Deterministic mermaid → inline SVG (supported subset documented in CLAUDE.md) |
| `scripts/check-mermaid.js` | Verifies diagrams actually render — run it on anything you add |
| `pkg/overview.py` | Bounded view for UIs, with `truncated{}` honesty |

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — Exports** ✅ | Added `--format graphml\|dot\|json\|obsidian` to the existing `pkg export`. Byte-equality asserted by test. | ~2–3 d | **Done** — see "Phase 1 — shipped" below |
| **2 — Richer deterministic visuals** | Extend the report/`state` visuals: seeded clustering (community grouping computed in Python — deterministic algorithm, stable tie-breaks), more diagram types, bigger legible graphs. Stays inline-SVG + self-contained. | ~5–7 d | `state --out report.html` shows a clustered architecture view; identical commit → identical bytes |
| **3 — Interaction over a precomputed layout** | In the web UI: filter by kind/area, search, collapse a cluster, click-through to source. Positions come precomputed from Python; JS only shows/hides/pans. No new deps. | ~5–8 d | Explorable graph in-UI; positions provably identical across reloads |

**Phase 1 is the bang-for-buck.** It neutralises most of the comparison ("can I explore the
graph?" → "yes, in your tool of choice") for a fraction of Phase 3's cost and zero invariant risk.
If the program runs short, ship Phase 1 and stop.

It also costs about half what this spec originally assumed, because two of its three parts turned
out to exist: `pkg export` is already a command, and the page-per-module Markdown writer already
ships as `episteme/`. What is genuinely new is the GraphML/DOT/JSON writers, a link-syntax flavour,
and the determinism test. Re-estimated ~4–6 d → **~2–3 d**.

### The `--db` problem (decide before writing code)

`pkg export` today takes `--db pkg-facts.db` and always writes SQLite. That option is **published
CLI surface** — it shipped in a released version, so someone may be scripting it. Adding
`--format graphml` beside a `--db` flag that only means anything for SQLite gives you either an
invalid flag combination or two overlapping options that both look authoritative.

Pick one deliberately:

- **Generalise to `--out`,** keep `--db` working as a deprecated alias that implies
  `--format sqlite`, and warn on use. Costs one release cycle of alias; loses nothing.
- **Keep both, validate,** and reject `--db` with a non-sqlite `--format`. Cheaper now, but the
  help text has to explain a rule instead of a surface.

Do not silently ignore `--db` when `--format` is passed — that is the one option that breaks a
working script without saying so.

## Invariants you must not break

- **Deterministic, seeded layout** — no force/random. If you add clustering, the algorithm must have
  stable tie-breaking (sort by id) so identical input → identical output.
- **No bundler, no npm, no d3/cytoscape.** If a phase seems to require one, it's the wrong phase.
- **Self-contained artifacts** (invariant #5) — an exported/saved report inlines its CSS/SVG and
  fetches nothing. Note `page_shell()` links `/static`, so a *served* page saved to disk loses its
  styling; exports must not depend on it.
- **Bound honestly — and note that visuals and exports want opposite things here.**
  - **Visuals are bounded.** A diagram with 9,000 nodes communicates nothing; cap it and say
    "top N of M" (`build_overview`'s `truncated{}`).
  - **Exports are complete.** The whole point of handing the graph to Gephi or yEd is that *their*
    tooling does the filtering. A silently truncated GraphML is worse than no GraphML — the user
    draws conclusions from a subset without knowing it. If an export ever must be bounded (a hard
    memory ceiling, say), it fails loudly or writes a manifest stating exactly what was dropped.
    It never just stops early.
- **Determinism is a test, not an aspiration.** Every exit criterion below says "byte-identical".
  That only stays true if something checks it: export twice from the same commit and assert byte
  equality, in CI. Unstable `set`/`dict` iteration is the usual culprit — sort by node id at every
  boundary. Two committed episteme regressions came from exactly this (module map with no name
  tie-break; `stats.most_common` with no id tie-break).
- Run `node scripts/check-mermaid.js *.md` on any diagram you add or touch.

## Phase 1 — shipped

`orchestrator pkg export --format graphml|dot|json|obsidian --out <path>`. Live on this repo:
**10,321 nodes / 28,504 edges / 0 dangling** across the three graph formats, and an Obsidian vault
of **83 pages / 1,351 wikilinks / 0 broken**. 25 tests; suite 1,990 green.

New: `pkg/graph_export.py` (writers + `WRITERS`/`GRAPH_FORMATS`), `knowledge/wikilinks.py`
(`to_wikilinks`, `write_vault`), `tests/pkg/test_graph_export.py`,
`tests/knowledge/test_wikilinks.py`.

**Four things the build taught that the plan did not know.**

**1. The export was missing the entire doc modality, silently.** `pkg export` ran raw
`RepoCodeExtractor`, but `Doc` nodes come from the `link_docs` post-pass — so the export carried
**9,401 nodes / 26,926 edges** where the real graph has **10,321 / 28,504**. 920 `Doc` nodes and
1,576 `MENTIONS` edges were absent, and because media (G3) reuses `Doc`, transcripts and OCR'd
images would have been missing too. Graph formats now run `link_docs`; **sqlite deliberately does
not** — its kind-per-table schema has no doc table, so the nodes would be dropped anyway, and its
shape is a contract with the ontomesh consumer. *If you add a projection, ask what post-passes the
graph needs before you serialise it; raw extraction is not the whole graph.*

**2. `NodeKind` has no `UNKNOWN`, and adding one would have been the wrong fix.** GraphML rejects
an edge whose endpoint is undeclared, so dangling endpoints need *something* — but inventing a
vocabulary member for an exporter's convenience is exactly what invariant #1 prevents. Placeholders
carry **no kind**, which is also the honest record: we have an id and nothing else.

**3. The wikilink work is a transform over rendered markdown, not a renderer.** Link generation in
`renderers.py` is ~18 inline f-strings with no single helper, so threading a mode through it would
have been invasive and easy to get subtly wrong. A post-pass keeps `renderers.py` untouched — no
second code path, nothing to drift. It writes a **copy**; `episteme/` stays canonical.

**4. Most of the wikilink care is in what is *not* rewritten.** A wikilink pointing at nothing is a
dead end; a surviving markdown link still works, because Obsidian renders both. Left alone: source
links, external links, bare anchors, targets escaping the vault, and **any label containing a pipe**
(`[[page|alias]]` has no escape for it). Wikilinks carry the vault-relative path, not the basename,
or `modules/x.md` and `areas/x.md` would collide silently.

**Two things only running it against a real consumer revealed** (Graphviz 15.1.0, this repo):

* **A complete export is too large to lay out, and that is fine.** `dot -Tcanon` on the 10,342-node
  DOT took **3m44s** just to parse and canonicalize — before any layout, and the result would be
  unreadable regardless. This does not argue for truncating the export; it argues that *slice, then
  lay out* is the real workflow. Gephi copes because you filter inside it. Phase 2 should assume
  users arrive with a slice, not a whole graph.
* **Consumers must resolve `IMPORTS` endpoints themselves, and the failure is silent.** 2,746 of
  5,895 import edges target a `Type` or `Function`, not a `Module`. Measured: the naive filter
  yields 3,033 module dependencies, the `CONTAINS` walk yields **4,287** — a naive read loses
  **1,254 edges, 29%**, and loses them in the direction that looks *plausible*: a tidier, more
  loosely-coupled architecture than the real one. That is the worst failure mode for a
  comprehension tool, and it is invisible without the number. `renderers.py` already does this
  walk; a Gephi user gets no such help. Documented in `CLI_REFERENCE.md` with a verified recipe.
  **A future phase could offer `--resolve-imports` to do the walk at export time** — deliberately
  not done here, because it would make the export a *view* rather than the facts, and "the export
  is what the graph says" is worth more than the convenience. If it is added, it must be an opt-in
  flag that says so in the output, never the default.

**Still to do before release:** a CHANGELOG entry, at version-bump time. (`CLI_REFERENCE.md` is
updated — it had documented only the SQLite form.)

## Non-goals

- Matching Graphify's force-directed aesthetic. We are trading visual polish for diffability on
  purpose; say so in the docs rather than quietly converging.
- A hosted/SaaS graph explorer.
- Leiden/Louvain **as-is** if the implementation is non-deterministic — only a seeded, stable
  variant qualifies.

## Open questions

1. ~~Obsidian vault vs. plain Markdown wiki — one or both?~~ **ANSWERED (2026-07-30): it is the
   existing writer with different link syntax.** The check this question asked for came back yes —
   `pkg-navigable-reports.md` shipped all four phases, and `episteme/` is already a page per
   module and area with symbol anchors, source deep-links, backlinks, and orphan reaping. The only
   thing Obsidian needs that it lacks is `[[wikilink]]` syntax instead of relative paths.
   **So: one writer, a link-syntax flavour on `knowledge/renderers.py` — not a second writer.**
   Building a parallel one would give two renderers over the same facts, and they would drift; the
   `IMPORTS`-edge bug that phase 2 of that spec shipped is the precedent for how quietly that goes
   wrong.
2. ~~Should exports live under `pkg export --format` or separate commands?~~ **ANSWERED:
   `--format`**, and more definitively than the original lean — `pkg export` is already a command
   (`cli.py`), so this is an extension, not a placement choice. The live question is what happens
   to its existing `--db` option; see "The `--db` problem" above.
3. Is Phase 3 worth it at all once Phase 1 exists? **Now due — Phase 1 has shipped.** The case for
   Phase 3 is weaker than when this was written: a user who wants an explorable graph can open the
   GraphML in Gephi, which does layout, filtering, search and clustering better than an in-house
   canvas with no build step ever will. What Phase 1 does *not* answer is the zero-install case —
   someone reading a report who will not install Gephi. Decide against that question specifically,
   not against "should we have a graph UI", and note that Phase 3 is where the invariant pressure
   is highest. *Still open — but it should be answered before anyone starts it, not during.*

4. **New:** should exports be committable? A committed `graph.json` would make architectural drift
   reviewable in a PR diff, which is the same argument that justified `episteme/`. Against: it is
   9.5 MB on this repo and would dominate every diff it appears in. A bounded, committed *summary*
   projection may be the useful middle — but that is a different artifact from an export, and
   "exports are complete" should not be weakened to get it.
