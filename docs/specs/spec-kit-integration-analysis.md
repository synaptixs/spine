# spec-kit — evaluated, declined

**Status:** declined 2026-08-25. Not adopted, not depended on, not interoperated with.
**Subject:** [github/spec-kit](https://github.com/github/spec-kit) — GitHub's Spec-Driven
Development toolkit.
**How it was assessed:** its README, command surface and stated design as of 2026-08-25.
**It was not installed and not benchmarked** — every claim below about spec-kit is a claim
about what it says it does, and is labelled as such. Nothing here is a measurement.
**Revisit condition:** stated at the end. This is a decision with a tripwire, not a dismissal.

---

## The decision, in one paragraph

Spec-kit and Spine describe the same arc — spec → plan → tasks → implement — and share nothing
underneath it. Spec-kit is prompt scaffolding: markdown templates plus slash commands, portable
across 30+ agents, with **no deterministic step anywhere in the workflow**. Spine's entire claim
is the opposite — a no-LLM graph, a validator on every model output, acceptance criteria bound
to a `file:line` or the ticket refused, and a model-call budget of three. Adopting spec-kit's
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

**Spec-kit never reads the codebase it is about to change.** The spec and the plan are written
from the prompt and whatever the agent happens to open. That is survivable on a greenfield
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
- **Not** that these numbers compare the two tools. Every figure here measures Spine. Spec-kit
  has not been run, and no head-to-head exists.
- **Not** that GitHub's backing is irrelevant. It is decisive for adoption and irrelevant to
  capability, and only the second question is what this record is about.

## Revisit condition

Re-open this record **if spec-kit ships a step that reads the target codebase deterministically**
— a real parser, symbol resolution, or any mechanism that binds a claim to a `file:line`. That
would change the analysis at its root, because every objection above descends from the same fact:
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
