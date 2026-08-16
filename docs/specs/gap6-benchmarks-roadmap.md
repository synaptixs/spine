# G6 — Benchmarks: measure and publish retrieval/localization quality

> **"G6" is a label, not a position in a queue.** It's gap #6 in the Graphify comparison. The specs
> are not ordered and do not run in sequence.

**Status:** Not started. **Rewritten 2026-08-15 against 3.18.1.**
**Owner:** _unassigned_

> **What changed in this rewrite.** The original opened with *"we make strong claims and publish
> **zero** evidence."* That was true when written and is **false now** — 3.17.0 and 3.18.0 shipped
> a labelled corpus, `scoreboard.json`, four oracles, and a strict CI accuracy gate. The original
> Phase 1 ("build a harness, record a baseline, commit it") therefore described machinery that
> already exists, and following it would produce **a second baseline that can disagree with the
> first**. This version extends the shipped measurement surface instead of duplicating it.
>
> The claim→metric table and the refusal to chase LOCOMO are unchanged. They were right.

**Gap:** #6 in [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md).

**One-liner:** we measure whether *the graph* is right; we do not measure whether *the answers* are
right. Extend the existing scoreboard to comprehension metrics on pinned **public** repos, gate
them, publish.

---

## What 3.18.1 already measures — do not rebuild any of this

| Shipped | What it gives us |
|---|---|
| `corpus/` — 19 labelled fixtures, 8 front-ends | Precision **1.00** on every node and edge kind; recall 1.00 except `CALLS` |
| `pkg accuracy --oracle invention` | 0 invented targets / 15,212 call edges. **Python-only** |
| `pkg accuracy --oracle parity` | Declared-vs-extracted routes/tables. Shortfall 0 |
| `pkg accuracy --oracle runtime` | `CALLS` recall by executing a repo's own tests. **Python-only** |
| `src/orchestrator/pkg/scoreboard.json` | Committed baseline; keys today: `corpus`, `invention`, `parity` |
| `pkg accuracy --check` | The CI gate: strict on corpus, ratchet on parity, trend-only elsewhere |
| `understand --check` | Currency gate — the bank provably matches the code |

**The hole this spec fills.** Every row above answers *"is the graph right?"* on **fixtures we
wrote**. None answers *"does it give the right answer to a real engineering question on a real
repository?"* That second question is the one a buyer asks, and it is unmeasured.

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
(`GroundingVerifier.stale_findings`, `pkg/verifier.py:122`, is most of it). A competitive sweep in
August 2026 confirmed it: **every published benchmark in the code-intelligence category is an
efficiency metric** — token savings, tool-call reduction — and none reports correctness.

## What already exists (reuse, don't rebuild)

| Piece | Gives us | Verified |
|---|---|---|
| `pkg/scoreboard.json` + `pkg accuracy --check` | Baseline **and** gate. Add a `comprehension` key; do not start a second baseline | ✅ |
| `evals/` — `harness.py`, `graders.py`, `models.py`, `paths.py` | The measurement package to extend | ✅ |
| `pkg/verifier.py` — `stale_findings`, `doc_findings` | Provenance validity, nearly free | ✅ `:92`, `:122` |
| `sdlc/localize.py`, `sdlc/investigate.py`, `sdlc/coverage.py` | The functions under test | ✅ |
| `evals/agent_corpus.py` | The precedent for asymmetric scoring — copy its discipline | ✅ |

## Corpus: public only, pinned by commit

**Open question 1 in the previous draft is closed: public repos only.** Under the invariant
*"never publish a number we can't reproduce on demand"*, a private or ephemeral repo cannot back a
published figure. `synaptixs/NN` is therefore excluded from anything published, though it remains
useful privately.

Two of the four validation repos are already public OSS and should be pinned as-is:

| Repo | Language | Why it earns a slot |
|---|---|---|
| **open5gs** | C | Large, real, and C is where `invention` cannot see |
| **dlib** | C++ | Heavy templates — the hardest extraction shape we ship |
| **flask**, **httpx** | Python | Small, fast, already exercised against 3.17/3.18 in Aug 2026 |
| a Go and a TypeScript repo, TBD | Go, TS | TS has the weakest `CALLS` recall (0.50) — measure it or it hides |

**The corpus must not be Python-heavy.** The `runtime` and `invention` oracles are Python-only, so
a Python-dominated corpus would report health it has not measured. Non-Python repos are what make
that limit visible rather than invisible.

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — Comprehension metrics into the existing scoreboard** | Ground truth mined from **merged PRs and closed bug issues** (the PR's changed files = truth for impact; the fixing commit = truth for localization) on the pinned public corpus. Metrics: top-k localization, fault-site top-1, impact recall/precision, provenance validity. Deterministic, no LLM in scoring. Land them as a **`comprehension` key in `scoreboard.json`**, written by `pkg accuracy --scoreboard`. | ~5–7 d | `pkg accuracy` prints comprehension alongside corpus/invention/parity; one baseline, one file |
| **2 — Gate + trend** | Extend `pkg accuracy --check` to the new key. Ratchet, not strict — these move with repo churn, like parity. Track a trend series across releases. | ~2–3 d | A PR that degrades localization is visible before merge, using the gate that already exists |
| **3 — Publish** | Public methodology + results: corpus (with commit SHAs), ground-truth derivation, metrics, numbers, and a **reproducible command**. State what we did *not* measure — the Python-only oracles especially — rather than implying broader coverage. | ~3–4 d | A public benchmark page and a command an outsider can run |

**Which phase buys what.** Phases 1–2 make the claim *checkable*; Phase 3 makes it *a market
position*. Phase 3 is not optional polish — an unpublished benchmark changes nothing outside the
repo, and "measured correctness" is the only axis on which Spine currently leads.

## Invariants you must not break

- **Scoring is deterministic and no-LLM.** A benchmark that needs a model to grade is a benchmark
  nobody can reproduce. If a metric genuinely needs judgement, report it separately and label it.
- **Pin the corpus.** Commit SHAs, not branch names — otherwise the numbers move under you.
- **Bound honestly.** Report corpus size, and what was excluded and why. No cherry-picked repos.
- **Never publish a number we can't reproduce on demand.** The command must be in the doc.
- **One baseline.** Comprehension metrics live in `scoreboard.json` next to the rest. Two baselines
  is how a project ends up quoting whichever is kinder.

## Non-goals

- LOCOMO / LongMemEval / conversational-memory benchmarks (wrong product — see above).
- Beating Graphify on *their* axis.
- **Benchmarking codegen quality (SWE-bench-style)** — a different, much larger program. It now has
  its own record: [`codegen-benchmark-roadmap.md`](codegen-benchmark-roadmap.md). This spec is about
  **comprehension/retrieval** only.
- Marketing spin. If a number is bad, it gets published or the claim gets dropped.

## Open questions

1. ~~Public corpus or private?~~ **Closed: public only** for anything published (see Corpus above).
2. Ground truth from merged PRs is noisy (PRs touch unrelated files). Do we hand-curate a small gold
   set instead? (Lean: **both** — a curated gold set of ~50 for headline numbers, mined PRs for
   volume/trend.)
3. Do we publish before or after G3/G5 land? (Lean: **baseline privately now, publish once they
   land**, so the numbers reflect the improved product.)
4. **New:** does a comprehension regression gate belong on the `strict` tier or the `ratchet` tier?
   Leaning ratchet — the corpus is real repos, so churn moves it, and the invention metric is
   ungated for exactly this reason.
