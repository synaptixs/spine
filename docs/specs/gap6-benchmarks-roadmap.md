# G6 — Benchmarks: measure and publish retrieval/localization quality

> **"G6" is a label, not a position in a queue.** It's gap #6 in the Graphify comparison. The specs
> are not ordered and do not run in sequence.

**Status:** Not started.
**Owner:** _unassigned_

## Before you start

**Prerequisites: none. You can start today — and this is the track to start first.**

| What this track needs | State |
|---|---|
| A measurement package to extend | ✅ Exists — `evals/`; this track owns it outright |
| A repo corpus to measure against | ✅ Exists — the validation repos already used for live proofs |
| Anything from G2, G3, G4, G5 or the watch-items | Nothing. No shared files at all. |

**Why first:** G2 already shipped (3.8.2) with **no baseline**, so we cannot say whether it improved
comprehension. Every track after it inherits that problem, and it compounds — the longer this waits,
the less "before" the baseline actually represents. It blocks nothing, so it runs alongside anything.

**Gap:** #6 in [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md).

**One-liner:** we make strong claims — "grounded", "file:line-accurate", "finds where a ticket
lands" — and publish **zero** evidence. Build the harness that measures those claims, get a
baseline before the other tracks start, then publish.

---

## Why, and why *not* their benchmark

Graphify publishes LOCOMO / LongMemEval numbers against mem0, supermemory, and dense RAG. It is
tempting to chase those. **Don't — they measure a different product.** Those are *conversational
memory* benchmarks; we are not a memory system. Running them would either produce bad numbers on
an irrelevant axis, or good numbers that prove nothing a buyer cares about.

**Measure what we actually claim:**

| Our claim | Measurable as |
|---|---|
| "`investigate` finds where a ticket lands" | **Localization accuracy** — given a real issue, is the true fix site in the top-k returned symbols? |
| "`localize` resolves a trace to the fault site" | **Fault-site top-1 accuracy** on real tracebacks |
| "`blast_radius` tells you what breaks" | **Impact recall** — of the files a real PR actually touched, how many were in the predicted radius? |
| "`regression_gaps` finds untested reach" | **Precision** — are flagged symbols genuinely uncovered? |
| "grounded, `file:line`" | **Provenance validity** — sampled facts still resolve to the claimed line |

The last one is the differentiator nobody else reports, and we can already compute it
(`GroundingVerifier.stale_findings` is most of it).

## Why Phase 1 should go first

Every other track in this program will claim it improved comprehension. Without a baseline
captured **before** they start, none of those claims are checkable, and regressions land silently.
Phase 1 is small and unblocks honest reporting for the whole program.

## What already exists (reuse, don't rebuild)

| Piece | Gives us |
|---|---|
| `evals/` | An existing measurement package — extend it, don't start a new one |
| `pkg/verifier.py` | `stale_findings` / `doc_findings` — provenance validity is nearly free |
| `sdlc/localize.py`, `investigate.py`, `coverage.py` | The functions under test |
| Validation repos (private, ephemeral) | `synaptixs/NN`, `open5gs`, `dlib`, the Go mirror — real corpora already used for live proofs |

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — Harness + baseline** | A fixed, versioned corpus (pin commits) + ground truth mined from **merged PRs and closed bug issues** (the PR's changed files = truth for impact; the fixing commit = truth for localization). Metrics: top-k localization, impact recall/precision, provenance validity. Deterministic, no LLM in scoring. Record baseline numbers. | ~5–7 d | `orchestrator evals run` (or equivalent) prints a metrics table; **baseline committed** so later work diffs against it |
| **2 — Regression gate + trend** | Wire the harness into CI on a small corpus subset; fail (or warn) on a material metric drop. Track a trend series across releases. | ~3–4 d | A PR that degrades localization accuracy is visible before merge |
| **3 — Publish** | A public methodology + results doc: corpus, ground-truth derivation, metrics, numbers, and a **reproducible** command. Honest framing: state what we did *not* measure (we are not a memory system) rather than implying broader coverage. | ~3–4 d | Public benchmark page + reproducible harness; a proof asset G4 can point at |

## Invariants you must not break

- **Scoring is deterministic and no-LLM.** A benchmark that needs a model to grade is a benchmark
  nobody can reproduce. If a metric genuinely needs judgement, report it separately and label it.
- **Pin the corpus.** Commit SHAs, not branch names — otherwise the numbers move under you.
- **Bound honestly.** Report corpus size, and what was excluded and why. No cherry-picked repos.
- **Never publish a number we can't reproduce on demand.** The command must be in the doc.

## Non-goals

- LOCOMO / LongMemEval / conversational-memory benchmarks (wrong product — see above).
- Beating Graphify on *their* axis.
- Benchmarking codegen quality (SWE-bench-style) — a different, much larger program; this spec is
  about **comprehension/retrieval** only.
- Marketing spin. If a number is bad, it gets published or the claim gets dropped.

## Open questions

1. Public corpus or private? Public repos make results reproducible by outsiders; our existing
   validation repos are private/ephemeral. (Lean: **public repos only** for anything published.)
2. Ground truth from merged PRs is noisy (PRs touch unrelated files). Do we hand-curate a small
   gold set instead? (Lean: **both** — a curated gold set of ~50 for headline numbers, mined PRs
   for volume/trend.)
3. Do we publish before or after G3/G5 land? (Lean: **baseline privately now, publish once they
   land**, so the numbers reflect the improved product.)
