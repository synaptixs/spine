# spec-kit — evaluated, declined

**Status:** declined 2026-08-25. Not adopted, not depended on, not interoperated with.
**Subject:** [github/spec-kit](https://github.com/github/spec-kit) — GitHub's Spec-Driven
Development toolkit.
**How it was assessed:** its README, command surface and stated design as of 2026-08-25. Its
**cost** was then measured by installing v1.0.11 and running it on three tickets: on
`claude-sonnet-5` through Claude Code (2026-09-25), and on `gpt-5.6-sol` and `gpt-6-astra`
through the Codex CLI (2026-09-26) ([reason 10](#10-what-it-costs-at-fleet-scale--measured)).
**Scope of that measurement:** cost, tokens and time. Every other claim below about spec-kit is
still a claim about what it says it does, and is labelled as such.
**Objections:** answered in [questions that come back](#questions-that-come-back) — tighter
requirements, engineer-validated context, and spec-kit with the PKG underneath.
**Revisit condition:** stated at the end. This is a decision with a tripwire, not a dismissal.

---

## The decision, in one paragraph

Spec-kit and Spine describe the same arc — spec → plan → tasks → implement — and share nothing
underneath it. Spec-kit is prompt scaffolding: markdown templates plus slash commands, portable
across 30+ agents, with **no deterministic step anywhere in the workflow**. Spine's entire claim
is the opposite — a no-LLM graph, a validator on every model output, acceptance criteria bound
to a `file:line` or the ticket refused, and a handful of model calls per ticket (4.4 measured,
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
without reading the repository is permanently in the arm that scored 3 of 68.

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
| Precision | **1.00** on every node and edge kind, all 8 front-ends |
| `CALLS` recall | 1.00 (c, sql) → 0.50 (typescript) — scored separately, never averaged away |
| Invention | **0** across 6 walked front-ends, gated `strict` at zero per language |
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

**How it was measured (2026-09-25).** Spec-kit v1.0.11 was installed and run headless through
Claude Code; Spine ran through its codegen benchmark plus its real intake step. Both arms got
the same three tickets (`NEW-SEVSUMMARY-1`, `NEW-LEDGERMD-1`, `NEW-DRIFTMD-1`) in the same words,
on the same model (`claude-sonnet-5`) and the same commit (`bd16dbb7`), three passes each.
Spec-kit was confined to its worktree with no MCP servers, so it had no access to Spine's graph.
Total spend $35.32. Six of spec-kit's nine runs completed. The other three stopped at
`/speckit-implement`'s own gate, which asks *"Some checklists have unchecked items. Do you want to
proceed with implementation anyway? (yes/no)"* about the checklist spec-kit had generated itself,
and a headless run has nobody to answer. Per-feature figures below use the six complete runs.

**Per feature:**

| | spec-kit (no PKG) | Spine (with PKG) |
|---|---|---|
| Model calls | 101–123 turns | 4.4 (2 intake + 2.4 codegen) |
| Tokens | 12,987,180 | 105,905 |
| of which cache reads | 12,476,807 (96.6% of input) | none (billed without prompt caching) |
| Output tokens | 77,263 | 4,367 |
| **Cost, `claude-sonnet-5`, as billed** | **$4.35** | **$0.25** |
| Wall time | 18.8 min | ~1.3 min |

**The token gap is 123×; the cost gap is 17.6×.** They differ because nearly all of spec-kit's
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

**Spec-kit's steps before any code cost $1.80, which is 7.3× Spine's entire pipeline ($0.25).**
`/speckit-constitution` runs once per repository and is excluded.

**Per developer** (4 features a month, about 21 working days):

| Per developer | spec-kit (no PKG) | Spine (with PKG) | Ratio |
|---|---|---|---|
| Per feature | 13.0M tokens | ~106k tokens | 123× |
| Per working day | 2.5M tokens | ~20k tokens | 123× |
| Per month | 51.9M tokens | ~424k tokens | 123× |
| Per year | 623.4M tokens | 5.08M tokens | 123× |

| Model | spec-kit: month | spec-kit: year | Spine: month | Spine: year | Ratio |
|---|---|---|---|---|---|
| `claude-fable-5-1` ($10/$50) | $49.58 | $595.00 | $4.93 | $59.22 | 10.0× |
| `claude-opus-5-5` ($4/$20) | $24.82 | $297.89 | $1.97 | $23.69 | 12.6× |
| `claude-sonnet-5` ($2/$10) | $17.40 | $208.83 | $0.99 | $11.84 | 17.6× |
| `claude-haiku-4-5` ($1/$5) | $8.70 | $104.42 | $0.49 | $5.92 | 17.6× |
| `gpt-5.6-sol` ($4/$20) | $34.81 | $417.66 | $1.97 | $23.69 | 17.6× |
| `gpt-5.6-terra` ($2/$12) | $18.02 | $216.25 | $1.02 | $12.26 | 17.6× |
| `gpt-5.6-luna` ($0.2/$1.2) | $1.80 | $21.62 | $0.10 | $1.23 | 17.6× |
| `grok-4.7` ($2/$6) | $30.27 | $363.27 | $0.92 | $11.01 | 33.0× |
| `grok-build-0.1` ($1/$2) | $12.33 | $147.98 | $0.44 | $5.29 | 28.0× |

**Tokens per year:**

| Developers | 500 | 1,000 | 1,500 | 2,000 | 5,000 | 10,000 |
|---|---|---|---|---|---|---|
| spec-kit (no PKG) | 311.7B | 623.4B | 935.1B | 1,246.8B | 3,116.9B | 6,233.8B |
|   of which cache reads | 299.4B | 598.9B | 898.3B | 1,197.8B | 2,994.4B | 5,988.9B |
| Spine (with PKG) | 2.54B | 5.08B | 7.63B | 10.17B | 25.42B | 50.83B |

**Cost per year:**

| Model | Setup | 500 | 1,000 | 1,500 | 2,000 | 5,000 | 10,000 |
|---|---|---|---|---|---|---|---|
| `claude-fable-5-1` ($10/$50) | no PKG | $297,499 | $594,997 | $892,496 | $1,189,995 | $2,974,987 | $5,949,974 |
| | with PKG | $29,610 | $59,220 | $88,830 | $118,440 | $296,099 | $592,198 |
| `claude-opus-5-5` ($4/$20) | no PKG | $148,944 | $297,888 | $446,831 | $595,775 | $1,489,438 | $2,978,876 |
| | with PKG | $11,844 | $23,688 | $35,532 | $47,376 | $118,440 | $236,879 |
| `claude-sonnet-5` ($2/$10) | no PKG | $104,416 | $208,832 | $313,249 | $417,665 | $1,044,162 | $2,088,325 |
| | with PKG | $5,922 | $11,844 | $17,766 | $23,688 | $59,220 | $118,440 |
| `claude-haiku-4-5` ($1/$5) | no PKG | $52,208 | $104,416 | $156,624 | $208,832 | $522,081 | $1,044,162 |
| | with PKG | $2,961 | $5,922 | $8,883 | $11,844 | $29,610 | $59,220 |
| `gpt-5.6-sol` ($4/$20) | no PKG | $208,832 | $417,665 | $626,497 | $835,330 | $2,088,325 | $4,176,650 |
| | with PKG | $11,844 | $23,688 | $35,532 | $47,376 | $118,440 | $236,879 |
| `gpt-5.6-terra` ($2/$12) | no PKG | $108,125 | $216,250 | $324,375 | $432,500 | $1,081,249 | $2,162,498 |
| | with PKG | $6,132 | $12,263 | $18,395 | $24,526 | $61,316 | $122,632 |
| `gpt-5.6-luna` ($0.2/$1.2) | no PKG | $10,812 | $21,625 | $32,437 | $43,250 | $108,125 | $216,250 |
| | with PKG | $613 | $1,226 | $1,839 | $2,453 | $6,132 | $12,263 |
| `grok-4.7` ($2/$6) | no PKG | $181,637 | $363,274 | $544,910 | $726,547 | $1,816,368 | $3,632,736 |
| | with PKG | $5,503 | $11,005 | $16,508 | $22,011 | $55,027 | $110,054 |
| `grok-build-0.1` ($1/$2) | no PKG | $73,992 | $147,984 | $221,976 | $295,968 | $739,919 | $1,479,839 |
| | with PKG | $2,647 | $5,293 | $7,940 | $10,586 | $26,465 | $52,931 |

**How to read these tables:**

- **Only the `claude-sonnet-5` rows were run.** The others price the same measured token mix
  (uncached input, cache writes, cache reads, output) at each model's list rates, including its
  own cache prices, with cache writes at the input rate where a vendor lists none. The formula
  reproduces the measured Sonnet 5 bill exactly for both arms ($4.351 and $0.247). Spec-kit
  would not run through Claude Code on GPT or Grok models, so read those rows as "this workload
  at that price", not as runs. The two OpenAI models were then measured directly; see below.
- **The ratio moves with cache pricing, from 10× to 33×.** Most of spec-kit's volume is cache
  reads, so a model with cheap cache reads (`claude-fable-5-1`, 2.5% of input) narrows the gap
  and one with dear cache reads (`grok-4.7`, 25%) widens it.
- **Model choice spreads spec-kit's bill 28×; the PKG cuts it 10–33× on any model.** At 10,000
  developers with no PKG the range is $216,250 (`gpt-5.6-luna`) to $5,949,974
  (`claude-fable-5-1`).
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
runs, all complete. Unlike the GPT rows above, these were run, not priced.

| | spec-kit, `gpt-5.6-sol` | Spine + PKG, `gpt-5.6-sol` | spec-kit, `gpt-6-astra` | Spine + PKG, `gpt-6-astra` |
|---|---|---|---|---|
| **Cost per feature** | **$5.35** ($4.05–$7.61) | **$0.42** | **$12.57** ($8.02–$15.92) | **$1.15** |
| Tokens per feature | 9.24M (97.8% cached) | 83k | 8.69M (97.3% cached) | 84k |
| Model requests | 90 | 4.2 | 79 | 4.0 |
| Wall time | 14.7 min | 1.9 min | 12.0 min | 3.9 min |
| **spec-kit ÷ Spine** | **12.7× cost · 112× tokens** | | **10.9× cost · 103× tokens** | |

Spec-kit's steps before any code cost $2.33 (sol) and $5.40 (astra): 44% and 43% of its bill, and
5.5× and 4.7× Spine + PKG's whole pipeline.

| Model | spec-kit tokens/yr | Spine tokens/yr | spec-kit $/month | spec-kit $/yr | Spine $/month | Spine $/yr | Cost ratio |
|---|---|---|---|---|---|---|---|
| `gpt-5.6-sol` | 444M | 4.0M | $21.39 | $256.68 | $1.68 | $20.21 | 12.7× |
| `gpt-6-astra` | 417M | 4.0M | $50.28 | $603.30 | $4.61 | $55.34 | 10.9× |

Tokens per year:

| Model | Setup | 500 | 1,000 | 1,500 | 2,000 | 5,000 | 10,000 |
|---|---|---|---|---|---|---|---|
| `gpt-5.6-sol` | spec-kit (no PKG) | 222B | 444B | 665B | 887B | 2,218B | 4,435B |
| | Spine (with PKG) | 2.0B | 4.0B | 6.0B | 7.9B | 19.8B | 39.7B |
| `gpt-6-astra` | spec-kit (no PKG) | 208B | 417B | 625B | 834B | 2,085B | 4,169B |
| | Spine (with PKG) | 2.0B | 4.0B | 6.1B | 8.1B | 20.2B | 40.4B |

Cost per year:

| Model | Setup | 500 | 1,000 | 1,500 | 2,000 | 5,000 | 10,000 |
|---|---|---|---|---|---|---|---|
| `gpt-5.6-sol` | spec-kit (no PKG) | $128,342 | $256,683 | $385,025 | $513,367 | $1,283,417 | $2,566,833 |
| | Spine (with PKG) | $10,107 | $20,214 | $30,321 | $40,427 | $101,068 | $202,137 |
| `gpt-6-astra` | spec-kit (no PKG) | $301,650 | $603,301 | $904,951 | $1,206,602 | $3,016,505 | $6,033,009 |
| | Spine (with PKG) | $27,671 | $55,342 | $83,013 | $110,684 | $276,709 | $553,418 |

On OpenAI, Codex stops at `/speckit-implement`'s checklist question every time; the runs answer it
once with a scripted "yes, proceed", as a person would. Spine + PKG could only call `gpt-6-astra`
through a harness-only shim: its client sends `max_tokens`, and OpenAI accepts tools with reasoning
for that model only on the Responses API. That is a Spine gap, tracked separately. Held-out tests
passed in 4 of 6 runs in every arm; one ticket failed in all of them.

**What the measurement corrected.** The estimate this section used before it was measured had
spec-kit at 1.25M tokens over 26 calls per feature with the coding loop at 83% of the cost, and
Spine at 3 calls. Measured: 13.0M tokens over 101–123 turns, with the steps before any code at
41%; Spine at 4.4 calls, because its intake step makes two. The dollar gap is wider than
estimated (17.6× against about 8×).

**Outcomes, for context.** Held-out tests passed in 4 of 9 spec-kit runs (three of the five
misses are the stalled runs) and 6 of 9 Spine runs; `NEW-DRIFTMD-1` failed in every run of both
arms. Spec-kit's agent did read code: in the first trial run it opened 13 files, including one
holding a convention Spine's grounding had not shown. It also updated this repo's count gate in
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
They are ordered as they tend to arrive: cost first, then review, then the combination.

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

**Would the PKG bring spec-kit's cost down?** Partly, and reason 10's measurements bound how
far. Spec-kit's bill has two parts, and the PKG can reduce only one:

| Part of spec-kit's bill | Measured share | Does the PKG reduce it? |
|---|---|---|
| Steps before any code (specify, clarify, plan, checklist, tasks, analyze) | 41–44% | **No.** These are model calls writing and checking documents; they happen whatever the model knows about the code |
| Implement and converge (exploring the code, writing it) | 56–59% | **Yes.** The graph replaces much of the agent's file-by-file reading |

Take the most generous case: the PKG makes spec-kit's implement and converge steps as cheap as
Spine + PKG's *entire* pipeline. Spec-kit still pays for its steps before code:

| Model | spec-kit + PKG, best case | Spine + PKG | Still |
|---|---|---|---|
| `claude-sonnet-5` | $1.80 + $0.25 = $2.05 | $0.25 | **8.2×** |
| `gpt-5.6-sol` | $2.33 + $0.42 = $2.75 | $0.42 | **6.5×** |
| `gpt-6-astra` | $5.40 + $1.15 = $6.55 | $1.15 | **5.7×** |

The remaining 6–8× can only be removed by replacing spec-kit's model-written planning steps with a
plan built deterministically from the graph. That is what Spine already is. Cutting spec-kit's
cost to Spine's level does not produce a cheaper spec-kit; it produces Spine, without spec-kit's
documents.

**This is a bound, not a measurement.** Nobody has run spec-kit with the PKG plugged in. The
best-case rows assume the PKG removes all of spec-kit's extra implement-and-converge cost, which it
would not in practice, and they leave out the graph output added to every call's input.

**The answer in one line:** adding the PKG to spec-kit gives it Spine's facts without Spine's
checks, and even in the best case it still costs 6–8× as much, because its model-written steps
before code remain. What is left of spec-kit is its prompts, and the one time a model-written plan
was measured against a graph-built one, it cost twice as much and did not help.

**In plain terms:** it is like handing a contractor the surveyed site map but letting them
decide whether to look at it, with nobody checking the build against it. The map only makes a
difference when something checks the work against it. Spine is the map and the inspector;
spec-kit with the PKG keeps the map and drops the inspector.

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
