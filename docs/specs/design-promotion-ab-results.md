# Design promotion A/B — should the design stage call a model?

**Run 2026-08-19 against `develop` @ Phase 2a** · `scripts/codegen_benchmark.py` with
`BENCH_DESIGN` · 5 `create` tickets × 2 frontier models × deterministic/llm design × **5 passes**
= **4 arms, 100 ticket-runs** · **0 aborts, 0 design fallbacks** · **$49.51**
(**$56** including calibration and pre-flight)

**Verdict: the promotion is declined.** `design` stays deterministic. The validator built to
guard it ships anyway, and earned its place on this run's own evidence.

---

## The question, and why it had to be asked

`docs/specs/graphir-sdlc-workflow.md` sets a rule: a node may be **demoted** to deterministic
freely, and **promoted** to a model node only with (a) a measurement showing the model beats the
deterministic version, (b) a validator on its output edge, and (c) room in the model-call budget.

`design` is the only stage that was ever a candidate. Its deterministic form is a template —
`interfaces` and `data_changes` come back empty, and `approach` is a sentence with the ticket
title interpolated — so a model writing those fields is a plausible improvement. `_llm_design`
has existed in `design.py` for months, unwired.

This is clause (a).

## Result

| model | arm | per-pass | accepted | 95% CI | held-out | cost |
|---|---|---|---|---|---|---|
| `claude-sonnet-4-6` | deterministic | 0,0,1,1,0 | 2/25 · 0.08 | [0.02, 0.25] | 15/25 | $7.23 |
| `claude-sonnet-4-6` | **llm** | 0,1,0,0,0 | 1/25 · 0.04 | [0.01, 0.20] | 14/25 | $17.40 |
| `gpt-5.6-sol` | deterministic | 0,0,0,0,0 | 0/25 · 0.00 | [0.00, 0.13] | 15/25 | $9.37 |
| `gpt-5.6-sol` | **llm** | 0,1,0,0,0 | 1/25 · 0.04 | [0.01, 0.20] | **6/25** | $15.51 |

**Pooled, n = 50 per arm:**

| | deterministic | llm |
|---|---|---|
| accepted | 2/50 · **0.04** [0.01, 0.13] | 4/50 · **0.08** [0.03, 0.19] |
| held-out | 30/50 · **0.60** [0.46, 0.72] | 20/50 · **0.40** [0.28, 0.54] |
| cost | **$16.60** | **$32.91** (1.98×) |

### How to read it

**Acceptance says nothing.** Two successes against four, at n=50, is not a comparison — it is
four events. The intervals overlap across almost their whole length. Anyone quoting "the model
design doubled acceptance" from `0.04 → 0.08` would be quoting noise, and the direction would
reverse on a different day.

**Held-out is the informative metric, and it favours the deterministic design.** 0.60 against
0.40, with intervals meeting only at 0.46/0.54. It is also the metric the model never authors,
which is why it is steadier than a self-graded rate.

**The cost is unambiguous.** The model arm cost **1.98×** the deterministic one for the same 50
tickets, and bought no measurable acceptance gain and a lower held-out rate.

## The caveat that keeps this honest

**The held-out gap is one model, not a general finding.**

| model | held-out, deterministic → llm |
|---|---|
| `claude-sonnet-4-6` | 15/25 → 14/25 — **no difference** |
| `gpt-5.6-sol` | 15/25 → **6/25** — 0.60 [0.41, 0.77] → 0.24 [0.11, 0.43] |

So the correct statement is **"the model design helped neither model and hurt one of them"**, not
"model-written designs are worse". A third model could behave differently. What the run does rule
out is the thing the promotion needed: evidence that it *helps*.

**Temperature cannot be pinned** on this model set (`claude-opus-5` rejects a pinned value), so
these are not reproducible to the number. Five passes per arm is what makes them worth quoting at
all.

## The validator's own result, from the same run

The validator was built to make the promotion *legal*, not to be measured here. It got measured
anyway, on 50 real model-written designs:

| | |
|---|---|
| Validator rejections in 100 runs | **0** |
| Silent design fallbacks | **0** |
| `llm` designs genuinely produced by a model | **50/50** |
| Fit failures, deterministic vs llm | 47 vs 45 — no difference |

**Zero false positives on 50 model-written designs** is far stronger evidence than the five-ticket
check that preceded the run, and it is exactly the case that would have exposed the prose bug
below. The validator ships.

## Three defects the pre-flight found, for about $2.50

Every one would have produced a confident wrong number had the sweep run first.

1. **The benchmark had no design stage at all.** It drives `LLMCodegenAdapter` directly and never
   calls `autorun`, so `produce_design` was never in its path. The 200-run grounding study
   measured codegen with no design in the loop whatsoever.
2. **The validator refused prose.** Models write sentences into `data_changes` and `interfaces` —
   *"No schema changes: nothing added to `src/orchestrator/registry/db/models.py`, no migrations"*
   is verbatim from the first real design it saw — and a sentence containing a path was judged as
   a path. Three false findings on one design; **every ticket in the model arm would have been
   rejected and reported as the model inventing code.**
3. **`_llm_design` had never worked.** It parsed with `json.loads`, the model answered inside a
   markdown fence, and `produce_design` catches every exception and returns the deterministic
   design. The `llm` arm would have **silently measured the skeleton and reported it as the
   model's work** — the "six tickets never reached the model" accounting error in a new place.

The third is why `build_design` now records a fallback as *the absence of a measurement*, and why
this run reports `0 design fallbacks` as a headline number rather than an aside.

## What the estimate got wrong

| | |
|---|---|
| First scoping | ~$30 |
| Calibration (8 runs) said | $105–175 |
| Actual | **$49.51** |

The calibration was run on `NEW-GRAPHMD-1`, which hits the refine cap every pass, so a two-ticket
sample was biased toward the most expensive ticket in the set. Cost here is driven by refine
count, which is behavioural — it depends on the quality of the output, which is the thing under
test — so per-run cost cannot be extrapolated from a small sample in either direction.

## Consequences

1. **`design` stays a deterministic node.** `_llm_design` remains unwired. The model-call budget
   stays at **three** — `intake`, `implement`, `review`.
2. **The validator ships and is enforcing**, on the strength of 0 false positives in 100 runs.
3. **Phase 2b closes as "promotion declined, measured"** — a documented outcome, not an open
   question.
4. **Phase B (edit tickets) was not run.** Its two purposes — the promotion comparison and the
   validator's false-positive rate — were both answered by Phase A, the second more strongly than
   Phase B could have. Skipping it saved roughly $10 and is recorded here rather than left as an
   unexplained gap in the design.

## What would reopen this

- A **third model** where the model design helps. The finding is model-specific by construction.
- **Evidence-conditioned design fields.** This run supplied `blast_radius` from Evidence but left
  `approach` and `interfaces` to the model with only the repo overview. A design prompt
  conditioned on the landing symbols with their `file:line` is a different treatment, and the one
  the hybrid split in the spec actually describes.
- **A larger corpus.** At n=50 with 2–4 successes, the acceptance metric cannot resolve anything.
  A ticket set where the deterministic baseline is not near-zero would make acceptance usable.

Anyone reopening it should say which of the three they changed, and re-run both arms — not
compare a new treatment against the numbers in this table.
