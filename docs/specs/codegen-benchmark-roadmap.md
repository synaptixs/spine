# Codegen benchmark — comparability, then the axis we win on

**Status:** Not started. **Written 2026-08-15 against 3.18.1.**
**Owner:** _unassigned_

**Why this record exists.** [`gap6-benchmarks-roadmap.md`](gap6-benchmarks-roadmap.md) lists
SWE-bench-style codegen benchmarking under **Non-goals** — correctly, since G6 is about
comprehension. But that left the codegen-comparability problem with *no spec, no owner, and no
roadmap entry anywhere*. It was described as "a different, much larger program" and then nobody
wrote it. This is that record.

**One-liner:** Spine's delivery half is measured but **not comparable**. 9/10 on our own tickets
cannot be set beside 72% on SWE-bench, and buyers will try. Phase 1 buys comparability. Phases 2–3
are the part that makes us a leader rather than an entrant.

---

## The strategic problem, stated plainly

Running SWE-bench and scoring below the leaders makes Spine a **follower on someone else's axis**.
That is the default outcome and it is worth naming before any work starts, because it is the
reason this program is not simply "run SWE-bench".

**Leadership requires an axis we win by construction.** We have one, and it is already built:

> **SWE-bench measures "the tests passed." It does not measure "this would merge."**

Spine's existing acceptance definition is strictly harder than SWE-bench's. From
`scripts/codegen_benchmark.py`:

> Acceptance per ticket = generated tests pass **AND** preflight (ruff + format + `mypy --strict`,
> repo config) passes **AND** the change fits — create tickets land inside the package and import
> the real model with no tracked file clobbered; edit tickets modify *the named target file* rather
> than creating a parallel module.

A patch that resolves a SWE-bench instance while introducing type errors, lint failures, or a
duplicate module **counts as solved there and is rejected here**. Nobody publishes that delta. We
can, because we already compute both halves.

## What 3.18.1 already ships — reuse, don't rebuild

| Piece | Gives us | Verified |
|---|---|---|
| `scripts/codegen_benchmark.py` | 10-ticket acceptance benchmark, production arm; **9/10** (edit 5/5, create 4/5), $0.68 total | ✅ |
| — its acceptance definition | tests **AND** preflight **AND** fit. This is the leadership axis, already implemented | ✅ |
| — throwaway git worktrees under `/tmp` | Read-only w.r.t. the repo; the isolation a public harness needs | ✅ |
| — `RecordingLLMClient` | Per-ticket cost accounting, so `$/resolved` is nearly free | ✅ |
| `scripts/codegen_ab.py` | The 3-ticket A/B precedent (3/3 grounded) — the shape Phase 3 scales up | ✅ |
| `evals/` (`harness.py`, `graders.py`, `models.py`) | The measurement package to extend | ✅ |
| `evals/agent_corpus.py` | Asymmetric scoring discipline — copy it | ✅ |
| CI-parity preflight in the SDLC path | The strict gate, in production, not bolted on for the benchmark | ✅ |

**The point:** Phase 2, the differentiating measurement, is mostly *plumbing existing code to a
public corpus*. That is why this program is worth starting despite the size of Phase 1.

## Phases

Each phase is labelled by what it buys. **Phase 1 buys parity and nothing more** — it is necessary
because without a comparable number the later phases are illegible, but it does not differentiate.

| Phase | Buys | Work | Effort | Exit |
|---|---|---|---|---|
| **1 — Comparable number** | **Parity** | Harness for **SWE-bench Verified** (human-validated subset). Containerised per-instance runs, official scoring, no bespoke grading. Report `resolved%` exactly as everyone else defines it, plus the harness version and model used. | ~3–4 w | A `resolved%` an outsider can put in a table beside OpenHands, from a command in the doc |
| **2 — The `mergeable` overlay** | **Leadership** | Re-score the *same* runs under Spine's strict acceptance: tests **AND** ruff/format/`mypy --strict` **AND** fit. Publish **three** numbers: `resolved%`, `mergeable%`, and **the delta**. The delta is the finding — how much of the industry's "solved" would not survive a real CI. | ~1–2 w | Two numbers per run and a delta, from one harness. First published `resolved`-vs-`mergeable` gap anywhere |
| **3 — Causal proof + cost** | **Leadership** | Same corpus, grounding **on vs off** (`PKGCodegenGrounder` disabled) — does the PKG actually earn its keep? Plus `$/resolved` and `$/mergeable` from `RecordingLLMClient`. | ~1–2 w | A causal number for the graph, and a procurement number nobody else publishes |
| **4 — Publish** | **Leadership** | Methodology, corpus pin, harness version, model, all four numbers, reproducible command. Bad numbers get published or the claim gets dropped. | ~1 w | A page an outsider can reproduce and a skeptic can attack |

**If only two phases ever ship, make them 1 and 2.** Phase 1 alone is a follower's number. Phase 2
alone is unanchored — a strict metric with nothing to compare against reads as moving the
goalposts. Together they are the whole argument: *here is the standard number, here is the honest
one, here is what the industry is not telling you.*

## Why this makes Spine a leader rather than an entrant

1. **It reframes the benchmark instead of chasing it.** We do not need the highest `resolved%` to
   own the `resolved`-vs-`mergeable` delta. That axis is ours because the strict gate is already in
   production, not written for the paper.
2. **It is the same brand as the PKG accuracy work.** 3.17.0/3.18.0 established "we measure what we
   claim and publish the shortfalls" — including *withdrawing a false finding in the changelog*.
   This extends that reputation to delivery, and consistency is what makes it credible.
3. **It answers the procurement question.** `$/mergeable` is directly comparable to ACU-style
   pricing, and no competitor publishes it.
4. **It proves the graph pays for itself.** Grounded-vs-ungrounded on a public corpus is the causal
   claim the whole product rests on, and today it rests on a 3/3 internal A/B.

## Invariants you must not break

- **Phase 1 uses the official harness and official scoring.** A bespoke scorer on a public corpus
  is indistinguishable from cheating, and it forfeits the comparability the phase exists to buy.
- **`resolved%` and `mergeable%` come from the same runs.** Different runs would make the delta an
  artifact of sampling.
- **Publish the model and harness version with every number.** A benchmark figure without them is
  not reproducible and violates the standing rule below.
- **Never publish a number we can't reproduce on demand.** The command must be in the doc.
- **No cherry-picking instances.** Report the full subset, or state the exclusion rule and why.
- **A bad number gets published or the claim gets dropped.** Same rule as G6.

## Non-goals

- Topping the SWE-bench leaderboard. We are buying comparability, not a trophy; optimising for the
  leaderboard would corrupt the `mergeable` axis, which is the valuable one.
- Training or fine-tuning on the corpus.
- SWE-bench Multimodal / other variants until Verified is landed and published.
- Replacing `codegen_benchmark.py`. It stays as the fast internal signal against this repo.

## Open questions

1. Which model(s) do we report? A single reference model is cleanest; a small matrix is more useful
   and multiplies cost. (Lean: **one reference model** for headline numbers, note the config.)
2. Does `mergeable` use each instance repo's own lint/type config, or a fixed Spine-standard one?
   (Lean: **the repo's own** — that is what "would merge" means, and it is what the SDLC path
   already does.)
3. Cost ceiling for a full run, and does it need its own budget line? `RunBudget` caps a run, not a
   benchmark sweep.
4. Do we gate any of this in CI, or is it release-cadence only? (Lean: **release-cadence** — full
   sweeps are too slow and too expensive for per-PR gating.)
