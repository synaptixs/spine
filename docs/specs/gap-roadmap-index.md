# Competitive-gap roadmap — directory

**You do not need this page to start a track.** Each spec is self-contained: it states its own
prerequisites, invariants, phases and exit criteria. This page exists only to say what the specs
*are* and which files each one owns. If you're picking up a track, open its spec and start there.

**Date:** 2026-07-22 · spine v3.8.2. **Statuses refreshed 2026-08-30 at 3.25.1** — the G4 and G6
rows had not moved since the page was written, and both had.
**Source:** the gaps in [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md).

---

## The specs

> **The G-numbers are labels, not an order.** They're the gap IDs from the comparison document.
> G3 is not "after" G2, and a lower number is not higher priority. There is no queue.

| Spec | What it covers | Status |
|---|---|---|
| **G2** — [document modality](gap2-document-modality-roadmap.md) | HTML + Office (`.docx`/`.xlsx`) ingestion | ✅ **Shipped in 3.8.2** |
| **G3** — [media ingestion](gap3-media-ingestion-roadmap.md) | Images, audio, video (OCR / transcription) | ✅ **Shipped in 3.10.0** — all three phases |
| **G4** — [adoption & distribution](gap4-adoption-distribution-roadmap.md) | Install friction, channels, proof assets | 🟡 **Phase 1 done 2026-08-19** — friction audit: ≈28s cold start, no key required, two findings fixed. **2** channels · **3** proof assets · **4** measurement outstanding |
| **G5** — [visualization](gap5-visualization-roadmap.md) | Graph exports + richer deterministic visuals | ✅ **Shipped in 3.11.0** — Phases 1–2; **Phase 3 deliberately dropped** (Gephi does it better on our export) |
| **G6** — [benchmarks](gap6-benchmarks-roadmap.md) | Retrieval/localization measurement + publication | Not started — **no prerequisites; best done first**. Rewritten 2026-08-15 against 3.18.1; **scope decided 2026-08-30** (D1–D4: gold set only, two metrics, five repos, ratchet gate). Nothing blocks it but staffing the ~3 days of labelling |
| **CB** — [codegen benchmark](codegen-benchmark-roadmap.md) | SWE-bench comparability, then the `resolved`-vs-`mergeable` delta | Not started — **no prerequisites**. Explicit non-goal of G6; it had no home before |
| **KL** — [codex plugin keyless](codex-plugin-keyless-roadmap.md) | Remove the API-key requirement via MCP sampling / Ollama | Not started — **Phase 0 is a blocking spike** (does Codex support sampling?) |
| **WI** — watch-items | PR-workflow defense; doc-drift durability | ⚠️ **Spec missing.** `watch-items-roadmap.md` does not exist in `docs/specs/` or `archive/` — this row linked a file that was never written or was deleted. Write it or drop the row |

**Gap 1 (language breadth — 36 grammars vs our 8) is deliberately not in this program.** We are not
chasing language count without a concrete need. If that changes it gets its own spec; the queue
would be Rust → Kotlin → Ruby.

## Everything left can run in parallel

Every remaining track has **zero prerequisites** and can start today. The one shared prerequisite
this program used to have — a reader seam in `pkg/doc_source.py` so formats register instead of
branching — **shipped with G2 in 3.8.2**, so it's no longer pending on anyone.

Suggested starting order if you're picking, rather than staffing everything:

1. **G6 Phase 1** (baseline) — G2 shipped without one, so its effect is unmeasured, and every later
   track inherits that problem. **Its four blocking decisions were taken 2026-08-30**, so the spec
   is now actionable as written.
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
| **G6** | `evals/`, a new `comprehension` key in `pkg/scoreboard.json` | `pkg/accuracy` CLI — **extend the existing gate, never a second baseline** |
| **CB** | `scripts/` (new SWE-bench harness), `evals/` | `scripts/codegen_benchmark.py` and the SDLC preflight — **read-only**; the strict gate must stay production code, not benchmark code |
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
