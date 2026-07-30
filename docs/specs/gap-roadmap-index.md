# Competitive-gap roadmap — directory

**You do not need this page to start a track.** Each spec is self-contained: it states its own
prerequisites, invariants, phases and exit criteria. This page exists only to say what the specs
*are* and which files each one owns. If you're picking up a track, open its spec and start there.

**Date:** 2026-07-22 · spine v3.8.2
**Source:** the gaps in [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md).

---

## The specs

> **The G-numbers are labels, not an order.** They're the gap IDs from the comparison document.
> G3 is not "after" G2, and a lower number is not higher priority. There is no queue.

| Spec | What it covers | Status |
|---|---|---|
| **G2** — [document modality](gap2-document-modality-roadmap.md) | HTML + Office (`.docx`/`.xlsx`) ingestion | ✅ **Shipped in 3.8.2** |
| **G3** — [media ingestion](gap3-media-ingestion-roadmap.md) | Images, audio, video (OCR / transcription) | Not started — **no prerequisites** |
| **G4** — [adoption & distribution](gap4-adoption-distribution-roadmap.md) | Install friction, channels, proof assets | Not started — **no prerequisites** (Phase 3 excepted) |
| **G5** — [visualization](gap5-visualization-roadmap.md) | Graph exports + richer deterministic visuals | Not started — **no prerequisites** |
| **G6** — [benchmarks](gap6-benchmarks-roadmap.md) | Retrieval/localization measurement + publication | Not started — **no prerequisites; best done first** |
| **WI** — [watch-items](watch-items-roadmap.md) | PR-workflow defense; doc-drift durability | Not started — **no prerequisites** |

**Gap 1 (language breadth — 36 grammars vs our 8) is deliberately not in this program.** We are not
chasing language count without a concrete need. If that changes it gets its own spec; the queue
would be Rust → Kotlin → Ruby.

## Everything left can run in parallel

Every remaining track has **zero prerequisites** and can start today. The one shared prerequisite
this program used to have — a reader seam in `pkg/doc_source.py` so formats register instead of
branching — **shipped with G2 in 3.8.2**, so it's no longer pending on anyone.

Suggested starting order if you're picking, rather than staffing everything:

1. **G6 Phase 1** (baseline) — G2 shipped without one, so its effect is unmeasured, and every later
   track inherits that problem.
2. **WI-2** (doc drift) — the comparison flags drift as *a lead, not a moat*: cheap for a competitor
   to copy once they have doc→code edges, which Graphify already does.
3. Anything else, in whatever order suits the people you have.

## Who owns which files

The only genuinely cross-cutting fact, and the reason parallel work is safe: **every point where two
tracks meet is append-only.**

| Track | Files it owns outright | Shared, append-only |
|---|---|---|
| ~~G2~~ ✅ | ~~doc readers~~ | *(shipped — added 4 readers + 1 extra without modifying any existing reader)* |
| **G3** | new `media/` package, `media extract` CLI | reader registry, `pyproject` extras, `pkg/facts.py` *(only if it adds a `Media` kind)* |
| **G4** | docs, `plugins/`, packaging | `plugin/_TOOLS` |
| **G5** | `pkg/export.py`, `knowledge/report_*.py`, `web/static/` | `pkg/facts.py` — **read-only** |
| **G6** | `evals/` | none |
| **WI-1** | `sdlc/` | `plugin/_TOOLS` |
| **WI-2** | `pkg/doc_link.py`, `pkg/verifier.py`, `knowledge/current_state.py` | none |

Appending a line to a registry, an extras block, or a tuple is a trivial merge conflict — not a
design conflict.

## House rules that apply to every track

Each spec repeats the ones that bite it, so you don't have to come back here. In full, from
[CLAUDE.md](../../CLAUDE.md): the PKG is the source of truth · `understand`/`state` stay
deterministic and no-LLM · layout is computed and seeded, never force-directed · the web UI has no
build step · shared artifacts are self-contained · bound honestly ("top N of M") · the base install
stays stdlib-only, every parser behind a lazy-imported extra.

Work off `develop`, phase-at-a-time PRs, and run the gate before pushing: `mypy src tests` (**not**
just `src`) + `ruff format --check .` + the suite.
