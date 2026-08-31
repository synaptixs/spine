# G6 — Benchmarks: measure and publish retrieval/localization quality

> **"G6" is a label, not a position in a queue.** It's gap #6 in the Graphify comparison. The specs
> are not ordered and do not run in sequence.

**Status:** **Phase 1's harness shipped 2026-08-31**; the gold set it scores is not written, so
localization reports `not_measured`. Scope decided 2026-08-30 (D1–D4 below).
**Rewritten 2026-08-15 against 3.18.1**; decisions taken and the text reconciled to them
2026-08-30.
**Owner:** _unassigned_ — and that is the binding constraint, not the code. D1 makes the gold set
**hand-labelling time**, which is the one cost nobody has agreed to spend.

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

## Decisions taken — 2026-08-30

Four decisions had blocked this spec since the rewrite. All four are now taken, and together they
**narrow v1**: a hand-labelled gold set, two metrics, five repositories, a ratchet gate.

They are recorded here because the rest of this page is the *result* of them — the previous text
said the opposite in three places (Phase 1 mined PRs, the corpus table named four other repos,
open question 2 leaned "both") and has been rewritten to match rather than left to disagree with
its own decisions.

| | Decision | Taken | Why |
|---|---|---|---|
| **D1** | Ground truth | **Hand-labelled gold set only** — 30–50 issues. PR mining **dropped** | A merged PR touches files unrelated to its fix, so a poor localization score could not be attributed to Spine rather than to the label. A small hand-labelled set is attributable, and it is the discipline that has now worked twice in this repository (`corpus/`, the shadowed-call fixtures). Phase 1 drops from ~5–7 d to ~3 d — but see the Owner line: that is *labelling* time, not machine time |
| **D2** | Metrics in v1 | **Two — top-k localization and provenance validity** | Localization is the claim that matters and is wholly unmeasured today; a `0` from `ticket-to-landing-sites` cannot currently be told apart from *never measured*. Provenance validity is nearly free (`stale_findings`). The other three are not cut so much as **removed by D1**: impact recall needs exactly the PR changed-file list that D1 dropped, fault-site top-1 needs a traceback corpus that does not exist, and `regression_gaps` precision needs a coverage run per repository |
| **D3** | Corpus | **Five repositories**, one per front-end — see the corpus section below | A subset of the eleven already SHA-pinned for the invention oracle: pinned, exercised at 3.22.0, right language mix. Not all eleven — 30–50 labelled issues spread over eleven repositories is too thin per repository to say anything about any of them |
| **D4** | Gate tier | **Ratchet** | Not for the reason the spec first gave — a SHA-pinned corpus does *not* churn. The real reason: this is a **ratio**, not a defect count, so unlike `invention` there is no correct value to hold at zero. `strict` would freeze whatever v1 happened to score into the definition of correct |

**What D1 costs, stated plainly.** Volume. A gold set of 30–50 gives headline numbers with a clean
denominator and no trend signal worth reading at that size. If a trend series is later wanted, it
is a second decision with its own measurement — not a quiet re-introduction of mined PRs.

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

| Our claim | Measurable as | In v1? |
|---|---|---|
| "`investigate` finds where a ticket lands" | **Localization accuracy** — given a real issue, is the true fix site in the top-k returned symbols? | ✅ **ships** (D2) |
| "grounded, `file:line`" | **Provenance validity** — sampled facts still resolve to the claimed line | ✅ **ships** (D2) — nearly free |
| "`blast_radius` tells you what breaks" | **Impact recall** — of the files a real PR actually touched, how many were in the predicted radius? | ❌ needs the PR changed-file list **D1 dropped** |
| "`localize` resolves a trace to the fault site" | **Fault-site top-1 accuracy** on real tracebacks | ❌ no traceback corpus exists |
| "`regression_gaps` finds untested reach" | **Precision** — are flagged symbols genuinely uncovered? | ❌ needs a coverage run per repository |

**Three of five are deferred, and the page says so rather than implying five.** Anything published
from v1 quotes two metrics and names the other three as not measured.

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

**D3 (2026-08-30) replaced the corpus this section used to name.** It listed open5gs, dlib,
flask/httpx and two TBD repositories — none of them pinned, two of them unexercised. Four of the
five below instead come from the eleven repositories already SHA-pinned and run against 3.22.0 for
the invention oracle ([`invention-oracle-cross-language.md`](invention-oracle-cross-language.md)),
so the corpus arrives pinned and already known to extract.

| Repo | Language | Pin | Why this one earns a slot |
|---|---|---|---|
| **vuejs/core** | TypeScript | `e2bede96134f` | TS has the weakest `CALLS` recall we ship — **0.50**. Measure it or it hides |
| **gin-gonic/gin** | Go | `dcaa4296d111` | Mid-size, dense HTTP routing — exercises `Endpoint`/`EXPOSES` alongside localization |
| **fmtlib/fmt** | C++ | `e27cc20bd93a` | The hardest extraction shape we ship, and it carried 43 of the 47 fabricated edges the invention widening found |
| **libuv/libuv** | C | `f87c8e4f70f2` | C is where `invention` originally could not see; it is also the front-end with `CALLS` recall 1.00, so a poor localization score here is *not* a recall artefact |
| **pallets/flask** | Python | **to be pinned in Phase 1** | The largest front-end and the language most users point Spine at. The one slot with no recorded SHA — pinning it is Phase 1's first act, not an assumption |

**Excluded, and why — required by "bound honestly".**

- **C# has no slot.** Dapper (`6d48ef664acc`) and Newtonsoft.Json (`09bb545d7296`) stay pinned and
  can join when the gold set grows; five slots cannot cover six front-ends, and C# produced the
  fewest extraction surprises of the six in the invention sweep. **v1 therefore publishes no C#
  localization number, and must say so rather than let five repositories imply the matrix.**
- **The other six pinned repositories** — leveldb, zod, nest, cobra, grpc-go, and the C files of
  leveldb/fmt — are dropped for spread, not for quality: one repository per front-end keeps
  30–50 labelled issues thick enough per repository to mean something.
- **`synaptixs/spine` itself is excluded from anything published.** Self-measurement cannot back a
  public claim, and the Python slot exists precisely so the number does not come from us.

**The corpus must not be Python-heavy** — one of five. The `runtime` and `invention` oracles are
Python-only, so a Python-dominated corpus would report health it has not measured. Non-Python
repositories are what make that limit visible rather than invisible.

> **How a label is made, and why it is made that way.** Ground truth comes from the commit that
> fixed the issue: what that commit changed *is* the answer, which is git rather than anyone's
> opinion. `orchestrator pkg fix-sites <repo> <sha>` prints exactly that — paths and change
> counts, fetched at depth 2 so the commit reads as a *change* rather than as a repository
> appearing from nothing. **It deliberately does not choose:** a commit carries tests, changelogs
> and tidying, and deciding which change *is* the fix is the judgement the gold set exists to
> capture. A candidate proposed by reading the ticket the way `investigate` reads it would not be
> independent of the thing being scored — it would measure two readers of the same clues agreeing
> and report it as accuracy.
>
> The validator refuses what a reader could not check: an abbreviated fix commit, an issue that
> is not a URL, a repository absent from the corpus manifest, a label with no fix site, the same
> issue twice, an exclusion with no reason. `--paths` additionally verifies every labelled path
> exists in the **pinned** tree, which catches the likeliest silent mistake — naming a file the
> fix *created*, which no run against the pre-fix state could ever have found.
>
> **Two things measuring changed, recorded because the spec asserted otherwise.**
>
> - **`stale_findings` cannot be provenance validity.** The spec called it *"most of it"*. It
>   compares a graph against the source it was extracted from, so on a freshly-extracted tree it
>   is **zero by construction** — a constant, not a measurement. What *is* falsifiable per fact,
>   and is what shipped: **does the recorded line actually name the symbol?** Every fact carries
>   `file:line` and the product's central claim is that a reader can open it.
> - **It is scored per kind, and that is the metric's definition.** `Function`, `Type` and
>   `Field` read **1.000** here. `Module` reads 0.151, `Endpoint` 0.014, `Entity` 0.000 — none of
>   them a defect: they are named by construction (a dotted path, `GET /v1/x`, a table name), not
>   by a token at the site. Scoring them would measure a naming convention and call it
>   provenance; omitting them silently would let *not scored* read as *passed*, so they are
>   counted as `excluded`.
>
> **The pins live in prose today.** Those SHAs exist in exactly one place: a markdown table in
> `invention-oracle-cross-language.md`. Nothing in `src/` or `evals/` reads them, so "pinned" is
> currently a claim a human keeps. **Phase 1 must land a real manifest** — a checked-in file the
> harness reads — or the pinning invariant is honoured by memory alone.
>
> **Done 2026-08-31**, and the manifest refuses an abbreviation at load. All eleven prose pins
> were re-verified against the GitHub API while writing it: **all eleven resolve.** The four
> reused here are recorded full-length, because a 12-character abbreviation reads as a SHA,
> resolves for a human, and cannot be handed to `git fetch`.

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1a — the harness** ✅ **2026-08-31** | Manifest (`evals/comprehension_corpus.yaml`, five pins, validated full-length at load), fetch-and-persist (`evals/corpus_fetch.py`), **provenance validity** (`evals/comprehension.py`), a `comprehension` key on the one scoreboard, a ratchet gate, and `pkg accuracy --oracle comprehension [--corpus]` | ~1 d | ✅ **1.0000 on 10,789 anchored facts**, gated. Localization reports `not_measured`, never 0 |
| **1b — the scaffolding** ✅ **2026-08-31** | `evals/comprehension_labels.yaml` + a validator that refuses anything unreproducible, `pkg fix-sites` to read what a fixing commit changed, `pkg labels --check`, and the top-k scorer | ~0.5 d | ✅ Labelling is now a form to fill in, and every field is checked |
| **1c — the gold set** | 30–50 hand-labelled issues, by a human. **Owner: labelling in progress 2026-08-31** | ~3 d labelling | outstanding |
| **1 — A gold set, two metrics, into the existing scoreboard** | Pin the corpus in a **manifest the harness reads** (five repositories, D3), then hand-label **30–50 real issues** — one fix site per issue, taken from the fixing commit, recorded with the issue URL and the commit SHA so anyone can re-derive it. Metrics: **top-k localization** and **provenance validity** (D2). Deterministic, no LLM anywhere in scoring or in labelling. Land them as a **`comprehension` key in `scoreboard.json`**, written by `pkg accuracy --scoreboard`. | ~2 d harness + **~3 d labelling** | `pkg accuracy` prints comprehension alongside corpus/invention/parity; one baseline, one file; every label re-derivable from its recorded SHA |
| **2 — Gate + trend** | Extend `pkg accuracy --check` to the new key on the **ratchet** tier (D4). Track a trend series across releases — with the honest caveat that at n=30–50 a small move is noise, so the gate is a floor, not a graph. | ~2–3 d | A PR that degrades localization is visible before merge, using the gate that already exists |
| **3 — Publish** | Public methodology + results: the corpus **with its commit SHAs**, how each label was derived, the two metrics, the numbers, and a **reproducible command**. States what was *not* measured — impact recall, fault-site top-1, `regression_gaps` precision, C#, and the Python-only oracles — rather than implying broader coverage. | ~3–4 d | A public benchmark page and a command an outsider can run |

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

**All four are closed.** Nothing below blocks the work; the only unresolved item is who spends
Phase 1's labelling days, which is a staffing call and sits on the Owner line, not here.

1. ~~Public corpus or private?~~ **Closed: public only** for anything published (see Corpus above).
2. ~~Ground truth from merged PRs is noisy — hand-curate a small gold set instead?~~ **Closed by D1
   (2026-08-30): gold set only.** The lean was "both"; the decision dropped the mining half, and
   with it impact recall.
3. ~~Publish before or after G3/G5 land?~~ **Closed: publish now.** Both landed — G3 in 3.10.0 and
   G5 in 3.11.0 — so the condition the lean waited on is already met.
4. ~~`strict` tier or `ratchet` tier?~~ **Closed by D4 (2026-08-30): ratchet** — because the metric
   is a ratio with no correct value, not because a pinned corpus churns.
