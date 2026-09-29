# spec-kit — evaluated, declined

**Status:** declined 2026-08-25. Not adopted, not depended on, not interoperated with.
**Subject:** [github/spec-kit](https://github.com/github/spec-kit) — GitHub's Spec-Driven
Development toolkit.
**How it was assessed:** its README, command surface and stated design as of 2026-08-25. Its
**cost** was then measured by installing v1.0.11 and running it on three tickets: on
`claude-sonnet-5` through Claude Code (2026-09-25), and on `gpt-5.6-sol` and `gpt-6-astra`
through the Codex CLI (2026-09-26), against Spine + PKG 3.52 on the same tickets, models and
commit ([reason 10](#10-what-it-costs-at-fleet-scale--measured)).
**Scope of that measurement:** cost, tokens and time. Every other claim below about spec-kit is
still a claim about what it says it does, and is labelled as such.
**Corrected 2026-09-29:** Q3's PKG saving is now a measured upper bound (8–12%, leaving
spec-kit 29–41× Spine + PKG) instead of an assumption, and `NEW-DRIFTMD-1`'s held-out test was
re-graded after its fixture was fixed ([SSPN-97](https://fibonacci-solutions.atlassian.net/browse/SSPN-97),
[SSPN-110](https://fibonacci-solutions.atlassian.net/browse/SSPN-110)).
**Objections:** answered in [questions that come back](#questions-that-come-back) — tighter
requirements, engineer-validated context, and spec-kit with the PKG underneath.
**Revisit condition:** stated at the end. This is a decision with a tripwire, not a dismissal.

---

## Spine + PKG, measured

Spine + PKG turns a ticket into working, reviewed code on an existing codebase, and backs every
step with evidence rather than trust. The PKG (Product Knowledge Graph) is built from the code
itself by parsers, with no model involved, so the same commit always yields the same facts. It
is gated at a precision of 1.00 on every kind of node and edge it records, with zero invented
symbols. Spine's pipeline uses those facts as evidence. Each acceptance criterion must bind to a
real `file:line` or the ticket is parked. A design that names code which doesn't exist is refused
before anything is written. Every model output then passes a deterministic check: tests, a
lint-and-type preflight, and a scope check. The graph pays off where it matters most, when the
model can't see where a change should land: new modules integrated correctly in **47 of 68**
runs with the graph, against **3 of 68** without it
([reason 3](#3-it-is-a-poor-fit-for-brownfield-work--which-is-the-case-that-matters)).

Because the graph supplies the context, the model does only the work that needs a model. Spine +
PKG 3.52 delivered each feature in **4.8 model calls, about 45,000 tokens and $0.12** on
`claude-sonnet-5`, in about 1.3 minutes. On the same tickets, model and commit, spec-kit took
101–123 turns, 13 million tokens, $4.35 and nearly 19 minutes. On OpenAI models it was $0.12 per
feature on `gpt-5.6-sol` against spec-kit's $5.35 (44.5×), and $0.39 on `gpt-6-astra` against
$12.57 (32.7×). Across three models Spine + PKG
was **30–45× cheaper per feature** and used **286–442× fewer tokens**, and no run of either tool
overlapped the other on cost. At 2,000 developers on `claude-sonnet-5` that is about **$11,800 a
year against $418,000**, and the gap scales linearly from there
([reason 10](#10-what-it-costs-at-fleet-scale--measured)).

The saving doesn't come at the expense of quality or control. On held-out tests, Spine + PKG
passed all 21 runs; spec-kit passed every run in which it produced code, but 3 of its 21 runs
stalled and produced none. Spine + PKG also stayed inside the ticket: across 21 runs on three
models it changed no file the ticket didn't call for, and all 21 passed Spine's own acceptance
gate. That discipline is itself a measured result. This research surfaced a scope defect in
Spine, and it was fixed, re-measured and shipped in 3.52. The comparison covered three small
tickets on one repository, so the claim is about the order of magnitude, not an exact
multiplier: Spine + PKG gets the same working code for a small fraction of the cost, and a
reviewer can check every step of it.

## What spec-kit lacks for an enterprise

Spec-kit is a well-made set of prompts, and nothing more: markdown templates and slash commands
that ask a model to write a spec, a plan and a task list, then the code. There is no
deterministic step anywhere in it. It has no parser or graph of the codebase, so on an existing
system its plan rests on whichever files the model decides to open. Spine's own tests show why
that matters: when a ticket did not say where new code belonged, models integrated a new module
correctly in 47 of 68 runs with Spine's graph and in 3 of 68 without it. spec-kit was not one of
the tools in those tests, but it gives the model no graph, so finding the right place in the code
is left to the model alone. Its acceptance
criteria are written by the same model that wrote the spec, and nothing binds them to real code.
It has no gate that can refuse a bad ticket, and it doesn't attempt blast radius or root cause.
Its own checks (`analyze`, `converge`) ask a model whether one document agrees with another, so
the model ends up grading its own work. The specs it writes into the repository go stale as the
code moves, and nothing detects that. It also publishes no accuracy figures, because nothing it
produces can be scored against the code.

For an enterprise those gaps become costs that grow with every team that adopts it. Headless, it
isn't reliable enough to automate: 3 of its 9 runs on Claude stopped at its own checklist
question waiting for a person, and through Codex it stopped there every time. At 2,000
developers on `claude-sonnet-5` its token bill is about **$418,000 a year**, against about
$11,800 for Spine + PKG. The only way to catch its errors is human review, since it has no
mechanical check. At 4 features per developer per month that is 96,000 specs a year for
engineers to validate from memory; ten minutes on each is about **$1.6 million a year** in
engineering time, more than the token bill on every model priced ([Q2](#q2-isnt-a-clean-context-validated-by-an-engineer-the-validator)).
None of that review leaves a record of what was checked. Spec-kit is a reasonable fit for a
greenfield prototype, where there is no codebase to get wrong. For an enterprise changing large
existing systems, it adds cost and risk and offers no way to verify either. The detailed case
follows.

## The decision, in one paragraph

Spec-kit and Spine describe the same arc — spec → plan → tasks → implement — and share nothing
underneath it. Spec-kit is prompt scaffolding: markdown templates plus slash commands, portable
across 30+ agents, with **no deterministic step anywhere in the workflow**. Spine's entire claim
is the opposite — a no-LLM graph, a validator on every model output, acceptance criteria bound
to a `file:line` or the ticket refused, and a handful of model calls per ticket (4.8 measured,
intake included, against spec-kit's 101–123 turns). Adopting spec-kit's
front end would mean importing an unvalidated LLM stage into the exact place where this project
has already measured that determinism wins. We are not doing that. We are also not building a
reader for its artifacts, because the only argument for one is vocabulary, and vocabulary is not
worth a maintained surface today.

## Why — the reasons, in order of weight

### 1. It is LLM-driven end to end, with nothing checking the model

Every spec-kit step is a model call, and no step has a deterministic validator on its output
edge. `/speckit.analyze` checks cross-artifact consistency and `/speckit.converge` validates the
implementation against the spec — both by asking a model whether documents agree with documents.

Model-checks-model has no floor. It is the same generator, the same blind spots, and a second
opinion from the same source is not evidence. Every model stage in Spine has a deterministic
check downstream of it: intake's spec by `assess()`, implement's code by tests plus the preflight
baseline-diff, review's fixes by re-running the tests. `design` has no validator, which is
exactly why it is not permitted to call a model.

This is not a preference. **The promotion was measured and declined**: a 100-run A/B ($49.51,
0 aborts) found no acceptance difference a 50-run arm could resolve, a held-out rate *favouring*
the deterministic design (0.60 vs 0.40), and 1.98× the cost. Adopting spec-kit's plan step is
that promotion with the validator removed. It would be paying to undo a decision already backed
by a measurement.

### 2. Nothing is deterministic, so nothing can be gated, cached, diffed or replayed

Spec-kit does not claim deterministic output, and could not have it — its artifacts are model
prose. Determinism is not an aesthetic property here; it is load-bearing for four things this
project depends on:

- `understand --check` can gate the knowledge base as **provably** current rather than hopefully
  current.
- The extraction cache is commit-keyed, and trusted only on a clean tree.
- A run's Evidence can be reproduced at a commit, and therefore replayed and diffed.
- A picture that redraws identically for an identical commit can be diffed; one that does not,
  cannot.

A graph that redrew itself differently for the same input could not be gated by anything. The
same is true of a spec that rewrites itself on every invocation.

### 3. It is a poor fit for brownfield work — which is the case that matters

**Spec-kit has no deterministic read of the codebase it is about to change.** The spec and the
plan are written from the prompt and whatever files the agent happens to open. In the measured
runs (reason 10) it did open some, but by choice, not through any mechanism that guarantees the
right ones or records what was missed. That is survivable on a greenfield
project, where there is nothing to be wrong about. It is the central problem on an existing one.

This is the gap Spine has actually measured, across 260 ticket-runs on two frontier models:

| Arm | New modules integrating correctly |
|---|---|
| Graph in context | **47 of 68** |
| No graph | **3 of 68** |
| Control — ticket already named the target file | **122 of 124, either arm** |

The control is the part that makes it an argument rather than an anecdote: when the model
already knows where the change lands, the graph makes no difference. **The graph pays precisely
where the model cannot see the target, and ties where it can.** A workflow that produces a plan
without a deterministic read of the repository leaves finding the right place in the code to the
model alone, which is the condition that scored 3 of 68.

The original 200-run A/B scored `create` tickets at **29/50 grounded against 0/50 ungrounded**;
replicating on an unrelated external repository takes the combined figure across two codebases
to the 47/68 vs 3/68 above.

### 4. Acceptance criteria are unbound

Spec-kit's criteria are written by the model that wrote the spec, and carried into
implementation unchecked. Spine refuses that: **every acceptance criterion is bound to a
`file:line` or the ticket is parked.** This closed a real defect — criteria used to come from
intake, a model that had not read the code, and were read straight through by design, codegen
and grounding.

An unbound criterion is a wish. It cannot be failed, so it cannot gate anything.

### 5. There is no stopping gate

Spec-kit has no stage that can refuse. Ambiguity is handled by asking for more prose
(`/speckit.clarify`), which produces a longer document, not a decision. Spine's `validity` stage
judges the ticket against deterministic Evidence and **can park a run before a line of code is
written** — including the case where a design names a symbol no repository has.

Refusing a bad ticket costs less than reviewing the code it produced.

### 6. Blast radius and root cause are not attempted

Neither is in spec-kit's surface. Both are in Spine's, deterministically, and one of them
carries a lesson worth restating: blast radius must be computed from **where the ticket lands**,
not from the design's own proposal. Radius computed from a proposal is a faithful analysis of a
fiction — and it reads as verification, which is worse than not having it.

### 7. Its artifacts rot, and it ships no detector for that

Spec-kit writes specs and plans into the repo. Those are exactly the documents that go stale the
moment the code moves, and there is no drift detection in the toolkit. Spine has `link_docs` and
`GroundingVerifier.stale_findings`: a documentation claim that no longer resolves to its line is
a finding, not a silence.

### 8. Nothing about it is measurable

Spec-kit publishes no accuracy figures, and this is a consequence rather than an oversight — it
has no artifact whose correctness could be scored. There is no ground truth for "was this plan
right" that does not require reading the code.

For contrast, what is currently gated in this repository:

| | Result |
|---|---|
| Precision | **1.00** on every node and edge kind, in all 12 languages (13 language front-ends) |
| `CALLS` recall | 1.00 (C, C++, SQL) down to 0.75 (Go, PHP), scored per language, never averaged away |
| Invention | **0**, on Spine's own repository and 11 public repositories, gated at zero per language |
| Grounding effect | 47/68 vs 3/68, with a control |

### 9. If we ingested its output, it would pollute the knowledge base

A concrete hazard rather than a philosophical one. `understand` ingests markdown from disk
whether or not git tracks it, so spec-kit's generated `specs/###-feature/*.md` would become `Doc`
nodes with `MENTIONS` edges into `episteme/` — **model-authored speculation about unbuilt
features, carrying the same standing as a hand-written design record.**

This is the same shape as the corpus-fixture trap already documented in `CLAUDE.md`, with worse
content: fixture source at least describes code that exists. Any future reader would have to
exclude generated specs by default, which is a decision someone has to remember to make.

### 10. What it costs at fleet scale — measured

Cost comes last on purpose: it is the weakest argument here. It is included because the
question is asked whenever this decision is weighed as an org-wide rollout, not as one team's
experiment.

**How it was measured (2026-09-25 to 2026-09-27).** Spec-kit v1.0.11 was installed and run headless through
Claude Code; Spine ran through its codegen benchmark plus its real intake step. Both arms got
the same three tickets (`NEW-SEVSUMMARY-1`, `NEW-LEDGERMD-1`, `NEW-DRIFTMD-1`) in the same words,
on the same model (`claude-sonnet-5`) and the same commit (`bd16dbb7`), three passes each.
Spine + PKG is version 3.52. Spec-kit was confined to its worktree with no MCP servers, so it had
no access to Spine's graph. Six of spec-kit's nine runs completed. The other three stopped at
`/speckit-implement`'s own gate, which asks *"Some checklists have unchecked items. Do you want to
proceed with implementation anyway? (yes/no)"* about the checklist spec-kit had generated itself,
and a headless run has nobody to answer. Per-feature figures below use the six complete runs.

**Per feature:**

| | spec-kit (no PKG) | Spine (with PKG) |
|---|---|---|
| Model calls | 101–123 turns | 4.8 (2 intake + 2.8 codegen) |
| Tokens | 12,987,180 | 45,476 |
| of which cache reads | 12,476,807 (96.6% of input) | none (billed without prompt caching) |
| Output tokens | 77,263 | 3,940 |
| **Cost, `claude-sonnet-5`, as billed** | **$4.35** | **$0.12** |
| Wall time | 18.8 min | ~1.3 min |

**The token gap is 286×; the cost gap is 35.5×.** They differ because nearly all of spec-kit's
tokens are cache reads: its agent re-sends a growing conversation on every turn, and the provider
bills those re-reads at a tenth of the input price.

**Where spec-kit's money goes:**

| Step | Mean cost | Tokens | Share of cost |
|---|---|---|---|
| specify | $0.25 | 0.56M | 6% |
| clarify (+ one scripted answer) | $0.34 | 0.73M | 8% |
| plan | $0.53 | 1.58M | 12% |
| checklist | $0.12 | 0.31M | 3% |
| tasks | $0.29 | 0.59M | 7% |
| analyze | $0.27 | 0.64M | 6% |
| **Before any code is written** | **$1.80** | **4.41M** | **41%** |
| implement | $1.81 | 7.48M | 42% |
| converge | $0.75 | 1.10M | 17% |

**Spec-kit's steps before any code cost $1.80, which is 14.8× Spine's entire pipeline ($0.12).**
`/speckit-constitution` runs once per repository and is excluded.

**Per developer** (4 features a month, about 21 working days):

| Per developer | spec-kit (no PKG) | Spine (with PKG) | Ratio |
|---|---|---|---|
| Per feature | 13.0M tokens | ~45k tokens | 286× |
| Per working day | 2.5M tokens | ~9k tokens | 286× |
| Per month | 51.9M tokens | ~182k tokens | 286× |
| Per year | 623.4M tokens | 2.18M tokens | 286× |

| Model | Basis | spec-kit: month | spec-kit: year | Spine: month | Spine: year | Ratio |
|---|---|---|---|---|---|---|
| `claude-fable-5-1` ($10/$50) | priced | $49.58 | $595.00 | $2.45 | $29.39 | 20.2× |
| `claude-opus-5-5` ($4/$20) | priced | $24.82 | $297.89 | $0.98 | $11.76 | 25.3× |
| `claude-sonnet-5` ($2/$10) | **measured** | $17.40 | $208.83 | $0.49 | $5.88 | 35.5× |
| `claude-haiku-4-5` ($1/$5) | priced | $8.70 | $104.42 | $0.24 | $2.94 | 35.5× |
| `gpt-5.6-sol` ($4/$20) | **measured** | $21.39 | $256.68 | $0.48 | $5.77 | 44.5× |
| `gpt-6-astra` | **measured** | $50.28 | $603.30 | $1.54 | $18.47 | 32.7× |
| `gpt-5.6-terra` ($2/$12) | priced | $18.02 | $216.25 | $0.52 | $6.26 | 34.6× |
| `gpt-5.6-luna` ($0.2/$1.2) | priced | $1.80 | $21.62 | $0.05 | $0.63 | 34.6× |
| `grok-4.7` ($2/$6) | priced | $30.27 | $363.27 | $0.43 | $5.12 | 70.9× |
| `grok-build-0.1` ($1/$2) | priced | $12.33 | $147.98 | $0.20 | $2.37 | 62.4× |

**Tokens per year:**

| Developers | 500 | 1,000 | 1,500 | 2,000 | 5,000 | 10,000 |
|---|---|---|---|---|---|---|
| spec-kit (no PKG) | 311.7B | 623.4B | 935.1B | 1,246.8B | 3,116.9B | 6,233.8B |
|   of which cache reads | 299.4B | 598.9B | 898.3B | 1,197.8B | 2,994.4B | 5,988.9B |
| Spine (with PKG) | 1.09B | 2.18B | 3.27B | 4.37B | 10.91B | 21.83B |

**Cost per year:**

| Model | Basis | Setup | 500 | 1,000 | 1,500 | 2,000 | 5,000 | 10,000 |
|---|---|---|---|---|---|---|---|---|
| `claude-fable-5-1` ($10/$50) | priced | no PKG | $297,499 | $594,997 | $892,496 | $1,189,995 | $2,974,987 | $5,949,974 |
| | | with PKG | $14,696 | $29,392 | $44,089 | $58,785 | $146,962 | $293,924 |
| `claude-opus-5-5` ($4/$20) | priced | no PKG | $148,944 | $297,888 | $446,831 | $595,775 | $1,489,438 | $2,978,876 |
| | | with PKG | $5,878 | $11,757 | $17,635 | $23,514 | $58,785 | $117,570 |
| `claude-sonnet-5` ($2/$10) | **measured** | no PKG | $104,416 | $208,832 | $313,249 | $417,665 | $1,044,162 | $2,088,325 |
| | | with PKG | $2,939 | $5,878 | $8,818 | $11,757 | $29,392 | $58,785 |
| `claude-haiku-4-5` ($1/$5) | priced | no PKG | $52,208 | $104,416 | $156,624 | $208,832 | $522,081 | $1,044,162 |
| | | with PKG | $1,470 | $2,939 | $4,409 | $5,878 | $14,696 | $29,392 |
| `gpt-5.6-sol` ($4/$20) | **measured** | no PKG | $128,342 | $256,683 | $385,025 | $513,367 | $1,283,417 | $2,566,833 |
| | | with PKG | $2,884 | $5,767 | $8,651 | $11,534 | $28,835 | $57,670 |
| `gpt-6-astra` | **measured** | no PKG | $301,650 | $603,301 | $904,951 | $1,206,602 | $3,016,505 | $6,033,009 |
| | | with PKG | $9,236 | $18,473 | $27,709 | $36,945 | $92,363 | $184,727 |
| `gpt-5.6-terra` ($2/$12) | priced | no PKG | $108,125 | $216,250 | $324,375 | $432,500 | $1,081,249 | $2,162,498 |
| | | with PKG | $3,128 | $6,257 | $9,385 | $12,513 | $31,283 | $62,567 |
| `gpt-5.6-luna` ($0.2/$1.2) | priced | no PKG | $10,812 | $21,625 | $32,437 | $43,250 | $108,125 | $216,250 |
| | | with PKG | $313 | $626 | $939 | $1,251 | $3,128 | $6,257 |
| `grok-4.7` ($2/$6) | priced | no PKG | $181,637 | $363,274 | $544,910 | $726,547 | $1,816,368 | $3,632,736 |
| | | with PKG | $2,561 | $5,122 | $7,683 | $10,244 | $25,610 | $51,221 |
| `grok-build-0.1` ($1/$2) | priced | no PKG | $73,992 | $147,984 | $221,976 | $295,968 | $739,919 | $1,479,839 |
| | | with PKG | $1,186 | $2,372 | $3,558 | $4,744 | $11,860 | $23,719 |

**How to read these tables:**

- **Rows marked *measured* were run; rows marked *priced* were not.** `claude-sonnet-5` is the
  Claude run above, and `gpt-5.6-sol` and `gpt-6-astra` are the OpenAI runs described below. The
  priced rows take the token mix measured on `claude-sonnet-5` (uncached input, cache writes,
  cache reads, output) and price it at each model's list rates, including its own cache prices,
  with cache writes at the input rate where a vendor lists none. The formula reproduces the
  measured Sonnet 5 bill exactly for both tools ($4.351 and $0.122). Read a priced row as "this
  workload at that price", not as a run.
- **Across the priced rows, the ratio moves with cache pricing, from 20× to 71×.** Most of spec-kit's volume is cache
  reads, so a model with cheap cache reads (`claude-fable-5-1`, 2.5% of input) narrows the gap
  and one with dear cache reads (`grok-4.7`, 25%) widens it.
- **Model choice spreads spec-kit's bill 28×; the PKG cuts it 20–71× on any model.** At 10,000
  developers with no PKG the range is $216,250 (`gpt-5.6-luna`, priced) to $6,033,009
  (`gpt-6-astra`, measured).
- **Every row scales linearly with the one remaining assumption,** 4 features per developer per
  month. Halve it and every figure halves.
- **Three small tickets, three passes each.** Enough to fix the order of magnitude and the
  direction; not a claim about larger tickets.
- **A cheaper model makes the calls cheaper, not checked.** The workflow's only check is still a
  model asking whether documents agree with documents (reason 1).

**Measured on OpenAI models (2026-09-26).** The same comparison was run on `gpt-5.6-sol` and
`gpt-6-astra`: spec-kit driven by the Codex CLI (Claude Code cannot drive GPT models), with
`CLAUDE.md` copied to `AGENTS.md` so both agents had the same repository instructions, against
Spine + PKG on the same model. Three tickets, two passes each (a $150 cap stopped the third), 24
runs, all complete. Their per-developer and yearly costs are in the tables above, marked
measured.

| | spec-kit, `gpt-5.6-sol` | Spine + PKG, `gpt-5.6-sol` | spec-kit, `gpt-6-astra` | Spine + PKG, `gpt-6-astra` |
|---|---|---|---|---|
| **Cost per feature** | **$5.35** ($4.05–$7.61) | **$0.12** | **$12.57** ($8.02–$15.92) | **$0.39** |
| Tokens per feature | 9.24M (97.8% cached) | 21k | 8.69M (97.3% cached) | 22k |
| Model requests | 90 | 4.2 | 79 | 4.0 |
| Wall time | 14.7 min | 1.3 min | 12.0 min | 2.0 min |
| **spec-kit ÷ Spine** | **44.5× cost · 442× tokens** | | **32.7× cost · 398× tokens** | |

Spec-kit's steps before any code cost $2.33 (sol) and $5.40 (astra): 44% and 43% of its bill, and
19.4× and 14.0× Spine + PKG's whole pipeline.

Tokens per year:

| Model | Setup | 500 | 1,000 | 1,500 | 2,000 | 5,000 | 10,000 |
|---|---|---|---|---|---|---|---|
| `gpt-5.6-sol` | no PKG | 222B | 444B | 665B | 887B | 2,218B | 4,435B |
| | with PKG | 0.5B | 1.0B | 1.5B | 2.0B | 5.0B | 10.0B |
| `gpt-6-astra` | no PKG | 208B | 417B | 625B | 834B | 2,085B | 4,169B |
| | with PKG | 0.5B | 1.0B | 1.6B | 2.1B | 5.2B | 10.5B |

On OpenAI, Codex stops at `/speckit-implement`'s checklist question every time; the runs answer it
once with a scripted "yes, proceed", as a person would. Spine + PKG could only call `gpt-6-astra`
through a harness-only shim: its client sends `max_tokens`, and OpenAI accepts tools with reasoning
for that model only on the Responses API. That is a Spine gap, tracked separately. Held-out tests
passed in all 6 runs in every arm (one ticket's test was corrected after the runs; see the outcomes
below).

**How confident these numbers are.** The cost gap is statistically significant on every model
tested; its exact size, and any difference in quality, are not.

| Model | spec-kit per feature (runs) | Spine + PKG per feature (runs) | Ranges overlap? | Exact Mann-Whitney p | Cost ratio, 95% bootstrap CI |
|---|---|---|---|---|---|
| `claude-sonnet-5` | $3.67–$5.33 (6) | $0.09–$0.16 (9) | No | 0.0004 | 35.5× (30.4–41.8×) |
| `gpt-5.6-sol` | $4.05–$7.61 (6) | $0.10–$0.14 (6) | No | 0.002 | 44.5× (35.5–55.4×) |
| `gpt-6-astra` | $8.02–$15.92 (6) | $0.34–$0.45 (6) | No | 0.002 | 32.7× (27.0–38.3×) |

- **On every model, spec-kit's cheapest run cost more than Spine + PKG's most expensive run.**
  The three stalled Claude runs, which produced no code, also cost more ($2.07–$2.36) than any
  Spine + PKG run, so excluding them does not flatter the result.
- **Runs on one ticket are not independent,** so the stricter unit is the ticket: all nine
  ticket-and-model combinations go the same way, at 26× to 48× (sign test p ≈ 0.004).
- **Quote the range, not a point.** "About 30–45× cheaper per feature" is supported; "44.5×" is
  one model's mean inside a wide interval.
- **Quality: no detectable difference in working-code rate.** Both tools passed the held-out
  tests in every run that produced code: Spine + PKG 21 of 21, spec-kit 18 of 18. spec-kit's
  other 3 runs stalled before producing code; counting those as failures, 21/21 against 18/21 is
  not a significant difference (Fisher's exact test, p ≈ 0.23). The supported statement is "no
  detectable difference in working-code rate", not "equal" or "better".
- **Scope.** Three small tickets, one repository, spec-kit v1.0.11, a headless protocol with two
  scripted answers. Per-run Spine + PKG cost is that run's codegen plus its model's mean intake
  cost. The rows priced rather than run carry no statistical claim, and the fleet
  tables multiply the measured per-feature gap by an assumed four features per developer per
  month.

**Outcomes, for context.** The held-out tests (written in advance, never shown to either tool)
show that both tools produced working code whenever they produced code at all:

- **Spine + PKG passed every run: 21 of 21,** on every ticket and every model.
- **spec-kit passed every run in which it produced code: 18 of 18.** Its three other runs, all on
  Claude, stalled at its own checklist question and produced no code to test.

**One ticket's test was corrected.** The benchmark's original held-out test for `NEW-DRIFTMD-1`
passed a hand-made fake finding instead of the real `DocDriftFinding` the ticket told both tools to
use. The fake's `kind` was a plain string rather than the real enum, and it had no `message`, so
code written correctly for the real type crashed on it: every run of both tools failed that
ticket, whatever it produced. Every run was re-graded from its kept code with a corrected test that
uses the real type and reads the rendered Markdown text, so an escaped name such as
`missing\_symbol` counts. No run was repeated; the same code was graded again.

Every Spine + PKG run passed Spine's own acceptance gate (tests, preflight and fit, 21 of 21), and
none changed a tracked file outside the ticket; spec-kit changed 1.1–1.5 per run
([Q4](#q4-does-either-tool-edit-files-it-shouldnt)). Spec-kit's agent did read code: in the first
trial run it opened 13 files, including one holding a convention Spine's grounding had not shown. It also updated this repo's count gate in
`STATE-OF-SPINE.md`, which Spine never does. Where both produced working code, the difference
this section measures is price.

The objections these tables usually draw, starting with whether tighter requirements would
bring the cost down, are answered in [questions that come back](#questions-that-come-back).

## The one case where it is the better tool

Stated because a record that only lists reasons to decline is advocacy, not a record — and
because this one is technical rather than circumstantial.

**Greenfield.** With no codebase there is nothing to ground against, so every objection above
except the validator one goes quiet, and Spine's cost buys correspondingly less. A workflow that
writes a plan without reading the repository is not wrong when the repository is empty.

That case is not the case this project is built for. Spine exists for changes landing in code
that already exists and that no model has read, which is where the 47/68 against 3/68 comes
from.

**The structural point underneath it:** spec-kit is agent-agnostic and language-unlimited
*because it is only prompts*. That is the same fact as every shortfall above, not a separate
one. There is no version of spec-kit that keeps the portability and gains a floor — gaining the
floor means building a parser and a graph, at which point it is no longer portable and no longer
spec-kit.

Deliberately not counted as advantages here: install friction, agent count, and GitHub's
distribution. Those are circumstantial — they describe how easily a tool is adopted, not whether
its output is right, and adopting a tool because it installs quickly is how a project ends up
with an unverified plan and no way to tell.

## The one idea worth taking anyway

Declining the tool is not declining everything in it.

**The constitution as a first-class artifact.** Durable project principles as an object every
run must honour, rather than a file the agent may or may not read. Spine's equivalents are
scattered across `CLAUDE.md` and `.spine/workflows/` profiles. This is a good idea we do not
have, and it needs neither spec-kit nor its file format.

## What we are not claiming

- **Not** that spec-kit is badly built. It is well-built for what it is.
- **Not** that spec-driven development is wrong. Spine *is* spec-driven; the disagreement is
  about what validates the spec.
- **Not** that the numbers compare capability. Reason 10 is a head-to-head on **cost**, on three
  small tickets with the same model; every other measured figure here measures Spine.
- **Not** that cost is the reason for declining it. Reason 10 prices the decision; reasons 1–9
  make it.
- **Not** that GitHub's backing is irrelevant. It is decisive for adoption and irrelevant to
  capability, and only the second question is what this record is about.

## Questions that come back

The objections raised most often when this decision is weighed, with the answer to each.
They are ordered as they tend to arrive: cost first, then review, then the combination, then
scope.

### Q1. "Wouldn't tighter requirements bring the cost down?"

The objection: grill the requirements until they are well defined and full of context (for
example, with a grill-me skill), so the model clarifies less and loops less.
It is partly right, and it is worth answering with numbers.

**Best case for spec-kit:** a thorough grill removes `/speckit-clarify` entirely and halves the
implement step. On the measured runs (reason 10) that saves **29%** of the cost and **34%** of the
tokens: $1.25 and 4.47M tokens per feature on `claude-sonnet-5`. In money:

| Model | Saving per feature | Saving per year, 2,000 devs | Saving per year, 10,000 devs | Break-even engineer time at $100/h |
|---|---|---|---|---|
| `claude-fable-5-1` | $3.55 | $341,219 | $1,706,096 | 2.1 min |
| `claude-opus-5-5` | $1.78 | $170,833 | $854,163 | 1.1 min |
| `claude-sonnet-5` | $1.25 | $119,761 | $598,806 | 0.7 min |
| `claude-haiku-4-5` | $0.62 | $59,881 | $299,403 | 0.4 min |
| `gpt-5.6-sol` | $2.50 | $239,523 | $1,197,613 | 1.5 min |
| `gpt-5.6-terra` | $1.29 | $124,015 | $620,075 | 0.8 min |
| `gpt-5.6-luna` | $0.13 | $12,401 | $62,007 | 0.1 min |
| `grok-4.7` | $2.17 | $208,330 | $1,041,651 | 1.3 min |
| `grok-build-0.1` | $0.88 | $84,866 | $424,329 | 0.5 min |

**The grill is paid for in engineer time, and that is the larger bill.** The last column above
turns the token saving into engineer time, at an assumed loaded cost of $100 per
engineer-hour. On the most expensive model, `claude-fable-5-1`, the grill has to take under
**about 2 minutes** per feature to pay for itself. On every other model it has to take at most
1.5 minutes, and on most under 1. No useful grilling session is that short.

What the grill costs in engineer time, at 4 features per developer per month:

| Engineer time per feature (at $100/h) | 500 | 1,000 | 1,500 | 2,000 | 5,000 | 10,000 |
|---|---|---|---|---|---|---|
| 10 min | $400,000 | $800,000 | $1,200,000 | $1,600,000 | $4,000,000 | $8,000,000 |
| 20 min | $800,000 | $1,600,000 | $2,400,000 | $3,200,000 | $8,000,000 | $16,000,000 |
| 30 min | $1,200,000 | $2,400,000 | $3,600,000 | $4,800,000 | $12,000,000 | $24,000,000 |

Even 10 minutes per feature costs more than spec-kit's entire token bill on every model priced
in reason 10. That includes `claude-fable-5-1`, the most expensive: $1,600,000 of engineer time
against $1,189,995 of tokens at 2,000 developers. Grilling does not lower cost; it moves cost
from tokens to people.

**It also leaves the validation cost where it was.** Better input does not make the output
checked. Whatever the model produces from a well-grilled requirement still has to be
validated, and there are only two ways to do it:

- **by a model**, which is model-checks-model again, with no floor (reason 1); or
- **by an engineer**, which is review time on every feature, on top of the grilling time.

Either way, engineering effort is spent on validation. Spine spends that effort once, in
parsers, and every ticket after that is checked mechanically: criteria bound to `file:line`,
designs checked against symbols that exist, and a validity gate that can refuse.

**The control arm shows what a grill has to reach to close the gap.** When a ticket already
names its target file, grounding makes no difference: **122 of 124 either way** (reason 3).
So a grill thorough enough to name where the change lands does close the brownfield gap, but
only because an engineer has done localisation by hand for that ticket. That is the PKG's job,
done by a person on every ticket, from memory, with nothing to tell them when their picture of
the codebase has gone stale.

**Grilling is not an alternative to the PKG; the two work together.** A well-grilled ticket
also improves Spine's intake. Do it to get better requirements, not as a way to make an
unverified workflow cheaper.

### Q2. "Isn't a clean context, validated by an engineer, the validator?"

This is the strongest version of the case for spec-kit: its artifacts give a clean, readable
context, and an engineer or subject-matter expert (SME) validates it before anything is built.
That, the argument goes, is the check reason 1 says is missing.

**What it gets right:**

- **A human review of intent is valuable, and a graph cannot do it.** Whether this is the right
  feature, and whether the requirement says what the business meant, are questions only a person
  can answer. The PKG knows what the code *is*, never why anyone wanted it.
- **Spec-kit's artifacts are readable.** A `spec.md` and a `plan.md` are easy to review.
- **Spine agrees, and has a human gate too:** approval gates in the loop, and the validity
  decision that can park a run. "A human validates it" is not something only spec-kit offers.

**Where it stops holding:**

1. **The reviewer checks intent well and location poorly, and brownfield work fails on
   location.** On an existing codebase the errors are a plausible but wrong integration point,
   a module that does not exist, or a caller nobody mentioned. In prose these look right, and
   the reviewer can only check them against their memory of the code. That failure is exactly
   what reason 3 measured: **3 of 68** without the graph, **47 of 68** with it. The control arm
   (**122 of 124 either way**) shows the gap closes only when someone has already named the
   target file. An SME can close it, but only by locating the change by hand on every ticket,
   which is the PKG's job done from memory.
2. **It checks a document against a memory, not against the code.** Spec-kit's own checks
   compare documents with documents. Adding a person makes it a document compared with what
   the person remembers. In Spine every claim carries a `file:line`, so the reviewer checks it
   against a line: faster, precise, and leaving a record of what was checked.
3. **Human review does not scale as a floor.** At 2,000 developers and 4 features a month that
   is 96,000 specs a year, about 1,850 a week. Review quality falls with volume and fatigue,
   approval turns into rubber-stamping, and none of it is measured. It is also paid for the same
   way as the grill: at 10 minutes of review per feature, $1.6M a year in engineer time at
   2,000 developers, more than spec-kit's entire token bill on every model priced in reason 10.
4. **A clean context is a snapshot.** It is validated on the day of review, and nothing
   re-checks it once the code moves (reason 7). In Spine a doc claim that no longer resolves to
   its line becomes a finding.
5. **Validating the spec does not validate the code.** The reviewed spec feeds
   `/speckit.implement`, which is 83% of the tokens and still has no deterministic check. Spine
   checks that stage with tests, the preflight baseline-diff, and a review that re-runs the tests.

**The answer in one line:** yes, put a human on the spec, and Spine does. But have the human
validate intent, and let the machine validate facts. An expert judging whether the requirement
is right is time well spent. An expert checking from memory whether a plan names real code is
doing a parser's job, 96,000 times a year, with no record of it.

**In plain terms:** asking an expert to approve the plan is like having a senior engineer sign
off a building design without a site survey. They can say whether it is the right building.
They cannot reliably say whether the foundations clear pipes they do not remember. Spine hands
them the survey, so their sign-off is about the decision, not about recall.

### Q3. "What if spec-kit uses the PKG too?"

The objection: plug the PKG into spec-kit, for example through Spine's MCP server, and the
brownfield gap in reason 3 goes away. The short counter is that the PKG is already grounded
fact, so spec-kit is not needed. That counter needs one sharpening to hold: **the PKG on its
own does not write a spec.** Spine is the PKG *plus* a workflow on top of it. The fair question
is what spec-kit's layer adds on top of the PKG that Spine's layer does not.

**Grounding that is available is not grounding that is enforced.** With the PKG plugged in,
spec-kit's model *can* look up the facts. Nothing makes it:

- The model decides whether to consult the graph and what to do with the answer.
- Acceptance criteria are still unbound (reason 4). Nothing refuses a criterion that does not
  resolve to a `file:line`.
- There is still no gate that can stop a run (reason 5).
- `/speckit.analyze` and `/speckit.converge` still have a model comparing documents with
  documents (reason 1).

Spine's pipeline uses the graph as evidence, not as a suggestion. Criteria are bound to a line
or the ticket is parked, a design naming a symbol that does not exist is refused, and blast
radius is computed from where the ticket lands. Adding the PKG to spec-kit gives it the facts
but not those checks, and the checks are what make the facts matter.

**The nearest measurement.** The 2026-08-19 design A/B
([design-promotion-ab-results](design-promotion-ab-results.md)) compared a model-written design,
with the knowledge graph's repo overview and blast radius in its prompt, against Spine's
deterministic design:

| | Deterministic design | Model design, graph in the prompt |
|---|---|---|
| Accepted | 2/50 | 4/50 (too few successes to compare) |
| Held-out rate | **0.60** | **0.40** |
| Cost | $16.60 | $32.91 (**1.98×**) |

That is the closest thing measured to "a model-written plan with the graph available": twice
the cost, no measurable gain, and a worse held-out rate on one of the two models. **Its limit:**
the model arm got the repo overview, not the landing symbols with their `file:line`. The
results doc names that fully grounded version as untested, and as one of its conditions for
reopening. So the claim is "the closest version measured did not help", not "a fully grounded
model plan has been shown not to help".

**What remains in spec-kit's favour:**

- **Portability and vocabulary:** 30+ agents, and the name people already know for
  spec-driven development.
- **Readable artifacts:** a spec and a plan are easy for a human to review (see Q2).
- **An adoption path:** a team already standardised on spec-kit can call Spine's MCP server for
  grounding. That is a legitimate way to use Spine, with Spine as the verifier underneath
  spec-kit. It does not meet the revisit condition below, because spec-kit itself has gained no
  deterministic step: the grounding is still Spine's.

**Would the PKG bring spec-kit's cost down?** Barely: by 12% at most on the measured runs. A
graph can only replace the work of *reading the code*, and spec-kit spends little on that. An
earlier version of this answer assumed the opposite: that its implement and converge steps (56–59%
of the bill) were mostly the agent reading files, which the graph would replace. Broken down
request by request, the saved runs show where those two steps actually spend:

| Implement step, per completed run | `claude-sonnet-5` | `gpt-5.6-sol` | `gpt-6-astra` |
|---|---|---|---|
| Reading the code | $0.16 (9%) | $0.05 (2%) | $1.03 (17%) |
| Writing the code | $0.33 (18%) | $0.29 (12%) | $0.36 (6%) |
| Running tests and lint, and waiting on them | $0.67 (37%) | $1.44 (59%) | $2.64 (44%) |
| spec-kit's own documents (`tasks.md`, checklists, its scripts) | $0.36 (20%) | $0.47 (19%) | $0.70 (12%) |
| Everything else | $0.29 (16%) | $0.20 (8%) | $1.33 (22%) |
| **Step total** | **$1.81** | **$2.45** | **$6.06** |
| …of which re-reading the conversation so far | 82% | 85% | 86% |

Converge reads even less code (2–9% of its cost). Its money goes to spec-kit checking its own
documents and re-running tests; on `claude-sonnet-5`, 69% of it is the first request re-caching
the whole conversation before any work starts.

So the most the PKG could save is the requests that read code, plus the cost of every later
request re-reading what those reads returned. Measured on every completed run:

| Model | spec-kit today | Reading code | Re-reading it later | **Most the PKG could save** | spec-kit + PKG, best case | Spine + PKG | Still |
|---|---|---|---|---|---|---|---|
| `claude-sonnet-5` | $4.35 | $0.44 | $0.07 | **$0.52 (12%)** | $3.83 | $0.12 | **32×** |
| `gpt-5.6-sol` | $5.35 | $0.15 | $0.28 | **$0.43 (8%)** | $4.92 | $0.12 | **41×** |
| `gpt-6-astra` | $12.57 | $1.13 | $0.18 | **$1.32 (10%)** | $11.25 | $0.39 | **29×** |

**This is a generous upper bound.** It assumes the PKG answers every code question for free and
adds nothing to the conversation, and neither is true. A real spec-kit + PKG run would save less.

**How it was measured:** from the saved logs of the published runs, with no model calls. Each
model request is credited to the tool call it made. A code read is `Read`/`Grep`, or a shell read
that touches `src/`, `tests/` or `scripts/`. "Re-reading it later" is the tokens those reads
returned, times the number of later requests in the same session, at the cache-read price. Runs
are counted exactly as in reason 10: completed runs only.

**Why the PKG can't do more:** spec-kit's cost is its model steps, not its reading. A feature
takes about a hundred model requests across specify, clarify, plan, checklist, tasks, analyze,
implement and converge, and each one re-reads everything said so far. Most of that conversation
is spec-kit's own prompts and documents, not code. A graph answers "where is this?"; it removes
none of the steps. The remaining 29–41× can only be removed by replacing spec-kit's model-written
steps with a plan built deterministically from the graph. That is what Spine already is. Cutting
spec-kit's cost to Spine's level does not produce a cheaper spec-kit; it produces Spine, without
spec-kit's documents.

**The answer in one line:** adding the PKG to spec-kit gives it Spine's facts without Spine's
checks, and saves at most 8–12% of its cost, because nearly all of that cost is model steps that
happen whatever the model knows about the code. Even in the best case it still costs 29–41× as
much as Spine + PKG. What is left of spec-kit is its prompts, and the one time a model-written plan
was measured against a graph-built one, it cost twice as much and did not help.

**In plain terms:** it is like handing a contractor the surveyed site map but letting them
decide whether to look at it, with nobody checking the build against it. The map only makes a
difference when something checks the work against it. Spine is the map and the inspector;
spec-kit with the PKG keeps the map and drops the inspector.

### Q4. "Does either tool edit files it shouldn't?"

**Spine does not; spec-kit edits a little, mostly bookkeeping.** In the measured runs, Spine + PKG
changed no tracked file outside the ticket, on any model: each run wrote one new module and its
test file, 21 runs out of 21. spec-kit changed 1.1 tracked files per run on `claude-sonnet-5`,
1.2 on `gpt-5.6-sol` and 1.5 on `gpt-6-astra`.

| Tracked files changed outside the ticket, per run | Spine + PKG | spec-kit |
|---|---|---|
| `claude-sonnet-5` | **0** (9 runs) | 1.1 (9 runs) |
| `gpt-5.6-sol` | **0** (6 runs) | 1.2 (6 runs) |
| `gpt-6-astra` | **0** (6 runs) | 1.5 (6 runs) |

**Why Spine stays in scope.** Its design lists only the files a ticket should change; every other
file it shows the model is marked as read-only reference. When the ticket names its files or
creates new code, an edit elsewhere that changes no code, such as a comment or docstring added
for justification, is refused before it is written. A real code change outside that list is
applied and reported in the change summary, so a reviewer sees it rather than finds it.

**What spec-kit's edits were.** Re-counted from its kept worktrees, most are integration work a
reviewer would want: a package `__init__.py` export, a `CHANGELOG.md` entry, and this
repository's STATE-OF-SPINE count gate. Spine touches none of those on a create ticket. That is
tighter, but it also means Spine does not yet do that bookkeeping; the count gate is tracked as
B46 ([SSPN-91](https://fibonacci-solutions.atlassian.net/browse/SSPN-91)).

## What this research has not measured yet

- **Larger tickets.** All three tickets were small. spec-kit's agent loop grows with the work, so
  the gap probably widens on bigger changes, but that is not measured.
- **spec-kit with the PKG, run for real.** Q3 measures an upper bound instead: the PKG could save
  at most 8–12% of spec-kit's cost, leaving it 29–41× Spine + PKG. A real run could still show
  a difference in *scope* (Q4: files changed outside the ticket), but not a material one in cost.
- **Prompt caching for Spine.** Spine is billed without prompt caching. Its uncached input is the
  main part of its cost on expensive models such as `gpt-6-astra`, so caching could lower it
  further.
- **Greenfield.** The record names it as the one case where spec-kit may be the better tool, but
  that has not been measured.

## Revisit condition

Re-open this record **if spec-kit ships a step that reads the target codebase deterministically**
— a real parser, symbol resolution, or any mechanism that binds a claim to a `file:line`. That
would change the analysis at its root, because every reason above descends from the same fact:
it has no floor under the model.

Additional templates, more slash commands, more agent integrations, or wider adoption do **not**
meet this condition. They make it more popular, not more correct.

## Where the detail lives

| Question | Document |
|---|---|
| The grounding measurement, in full | [codegen-model-comparison-results](codegen-model-comparison-results.md) · [external-repo-grounding-results](external-repo-grounding-results.md) |
| Why `design` stays deterministic | [design-promotion-ab-results](design-promotion-ab-results.md) |
| Evidence, bound criteria, the validity gate | [graphir-sdlc-workflow](graphir-sdlc-workflow.md) |
| What the graph holds and how it is built | [parsing-and-the-pkg](parsing-and-the-pkg.md) · [../../KNOWLEDGE_GRAPH.md](../../KNOWLEDGE_GRAPH.md) |
| Where we stand overall | [STATE-OF-SPINE](STATE-OF-SPINE.md) |
