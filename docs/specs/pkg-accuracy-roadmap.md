# Is the graph right? — validating and verifying PKG accuracy

**Status:** **phases 1, 2 and 3 complete** · phases 4–7 not started
**Written:** 2026-08-11, against 3.16.1 · **last updated:** 2026-08-13 10:31 EDT

## Progress

Start and end times are recorded per phase, so the effort estimates below get calibrated
against what the work actually took rather than staying guesses.

| phase | estimate | started | ended | elapsed | state |
|---|---|---|---|---|---|
| 1 — labelled corpus + `pkg accuracy` | ~1 week | 2026-08-12 21:55 EDT * | 2026-08-13 08:27 EDT | **~10.5 h** | **complete** |
| 2 — runtime oracle *(scope cut from 5 oracles to 1)* | ~2 days † | 2026-08-13 08:37 EDT | 2026-08-13 09:36 EDT | **~1 h** | **complete** |
| 3 — per-construct parity | ~1 day ‡ | 2026-08-13 10:23 EDT | 2026-08-13 10:31 EDT | **~8 min** | **complete** |
| 4 — sampled fact audit | ~3 days | — | — | — | not started |
| 5 — scoreboard + CI gate | ~3 days | — | — | — | not started |
| 6 — numbers in the document | ~2 days | — | — | — | not started |
| 7 — the intent layer | — | — | — | — | not started |

\* Reconstructed from file timestamps, not recorded at the time — this convention began
mid-phase. Every later start time is stamped before the work begins.

‡ Phase 3's estimate was revised from ~2 days to ~1 day after a 20-minute prototype, and
then took 8 minutes. The pattern across all three phases is the same: the estimates priced
in *discovering* something that measuring handed over immediately. The lesson is about the
estimates, not the work — a phase whose risk is "we do not know what we will find" is
cheap to de-risk and expensive to guess at.

† Phase 2's estimate was revised down from ~2 weeks after two decisions: the scope was cut
from five oracles to one (the other four become separate tickets), and a half-day spike
discharged the risk the remaining estimate was carrying. Revising an estimate mid-phase is
only honest if the revision is recorded — so it is.

**Phase 1 — what has landed so far**

| | | when |
|---|---|---|
| build document | [`build-documents/PKG-ACC-1-build.md`](build-documents/PKG-ACC-1-build.md) | 2026-08-13 01:45 EDT |
| corpus method + 2 Python cases | [`corpus/`](../../corpus/README.md) — `plain`, `dispatch` | 2026-08-13 01:37 EDT |
| comparator + CLI + 16 tests | `pkg/accuracy.py`, `pkg accuracy` | 2026-08-13 01:41 EDT |
| 2 more Python cases | `decorated`, `relative_imports` | 2026-08-13 07:05 EDT |
| 3 TypeScript cases | `plain`, `generics`, `instance_calls` | 2026-08-13 08:27 EDT |

**Estimated ~1 week; took ~10.5 hours.** The estimate assumed hand-labelling would dominate,
and it did — but seven cases rather than the feared dozens turned out to be enough to
saturate the findings. Treat the ~3× overshoot as a caution about the *later* phases'
estimates, which were sized the same way.

### The numbers — 7 cases, 2 languages

| | Python | | TypeScript | |
|---|---|---|---|---|
| | precision | recall | precision | recall |
| `CALLS` | **0.80** | **0.73** | **1.00** | **0.50** |
| `CONTAINS` | 1.00 | 1.00 | 1.00 | 1.00 |
| `IMPORTS` | 1.00 | 1.00 | 1.00 | 1.00 |
| `IMPLEMENTS` | — | — | 1.00 | 1.00 |
| `EXPOSES` | 1.00 | 1.00 | — | — |
| all node kinds | 1.00 | 1.00 | 1.00 | 1.00 |

**Every number outside `CALLS` is 1.00, in both languages.** Declarations are parsed, and
parsing is reliable. `CALLS` is the only kind that needs *resolution*, and it is the only
kind that loses anything — which is precisely what the front-ends' "precision-first" stance
predicts, now measured instead of asserted.

The two languages fail differently, and the difference is informative: Python trades
precision for reach (0.80/0.73 — it emits phantoms), TypeScript keeps precision perfect and
emits less (1.00/0.50 — it refuses rather than guesses). TypeScript is honouring the stated
stance; Python is not.

### What the corpus found, none of it visible to `pkg verify`

- **A systematic false-positive class in the Python front-end.** An unresolvable local name
  becomes a phantom external node *plus* a `CALLS` edge to it — `py:cls` (a local variable,
  in `dispatch`) and `py:fn` (a parameter, in `decorated`). Two independent instances make
  it systematic. This is the entire gap between Python's 0.80 precision and TypeScript's
  1.00, and it is the strongest argument yet for phase 4's sampled audit.
- **A cross-language inconsistency.** Python emits `caller -CALLS-> Type` for construction;
  TypeScript emits nothing for `new Handler()`. Same vocabulary, same node kind — one
  front-end is simply behind the other.
- **Import targets are conditional**, not uniformly symbol-level: the fallback to the module
  fires only for targets with no node, so `rate` (a function) and `router` (a variable) are
  identical syntax and different edges. TypeScript always targets the module, because its
  import placeholders are paths. This document stated the rule wrongly at first; measurement
  corrected it.
- **Fixture source pollutes the host graph** — 73 phantom nodes in Spine's *own* graph until
  every fixture root became `.repo/`. `pkg verify` reported OK throughout. Recorded in
  `CLAUDE.md`.

**And the thesis, demonstrable in fifteen lines:** `pkg verify` reports `OK — 0 errors, 0
warnings` on `corpus/python/dispatch`, a fixture containing a call edge to a function that
does not exist. A graph asserting a call to a non-existent symbol is perfectly
self-consistent. Consistency was never accuracy, and now there is a fixture that proves it
rather than an argument that claims it.

### What phase 1 did not do

The number covers **7 hand-written fixtures in 2 languages**. It is not a statement about
Python, about TypeScript, or about any real repository, and nothing here measures the other
six front-ends at all.

**Corrected 2026-08-13 by phase 2's spike.** This section originally claimed the fixtures
were "pessimistic by construction" because they were built around hard shapes. They are not.
Traced against real execution of this repo's own test suite, `CALLS` recall is **0.61** —
against the corpus's **0.73**. The hand-written fixtures were *optimistic*, and the direction
of the error was the opposite of the one asserted here. A corpus can only contain the
difficulty its author thought to write down.

Everything Spine says about a codebase is rendered from the PKG. The build document's
blast radius, `investigate`'s landing symbols, `regression`'s coverage gaps, codegen's
grounding — all of it inherits the graph's mistakes without inheriting any way to notice
them. So: how do we know the graph is right?

**Today: we know it is *consistent*. We do not know it is *right*.** Those are different
questions, and only the first has an implementation.

---

## 1. Two questions, and which one is answered

| | question | needs an oracle? | exists |
|---|---|---|---|
| **Soundness** | Is what the graph asserts true? | yes | partly |
| **Completeness** | Is what's true in the code asserted? | yes | barely |
| **Consistency** | Does the graph contradict itself? | no | **yes** |

Consistency is cheap — it runs on any repo with nothing to compare against. Soundness and
completeness need something that already knows the answer. That is the gap.

### What exists today

**`orchestrator pkg verify`** ([`pkg/verify.py`](../../src/orchestrator/pkg/verify.py)) —
six checks, no oracle needed:

| check | severity | asks |
|---|---|---|
| `dangling-edge` | error | does every edge endpoint exist as a node? |
| `stale-provenance` | error | does every `file:line` resolve to a real line? |
| `orphan-rate` | error | are first-party modules implausibly unimported? |
| `external-ratio` | error | are `IMPORTS` implausibly all-external? |
| `phantom-module` | warning | does an `external` module shadow a first-party one? |
| `source-parity` | warning | does the source declare a construct the graph holds *no* node of? |

Its own docstring is honest about why it exists: the dangling-import bug "survived 204
tests because every existing assertion checked *soundness* — what we assert is true — and
none checked *completeness*".

**`GroundingVerifier`** ([`pkg/verifier.py`](../../src/orchestrator/pkg/verifier.py)) — SHACL
shape conformance, plus a genuine soundness check: re-extract the file and confirm the
symbol the graph asserts still exists. It is wired into code review, not into any PKG
command.

**`orchestrator pkg capabilities`** — what each front-end *can* emit, read from the
front-ends' own source and asserted byte-equal against `KNOWLEDGE_GRAPH.md`. Capability,
explicitly not coverage.

**Determinism** — `understand --check` fails when a fresh generation differs from the
committed bank. Same input, same output; it says nothing about whether the output is right.

### What does not exist

*Revised 2026-08-13. Three entries here were closed by phase 1 and are struck through rather
than deleted — the point of the list is what changed, not just what remains.*

- ~~**No ground truth.**~~ **Closed.** `corpus/` holds 7 hand-labelled fixture repositories
  across 2 languages, with the method published alongside.
- ~~**No precision or recall, for any edge kind, ever measured.**~~ **Closed for Python and
  TypeScript.** `CALLS` is 0.80/0.73 and 1.00/0.50 respectively; every other kind is 1.00.
  Still absent for the other six front-ends.
- ~~**No false-positive measurement.**~~ **Partly closed.** Precision is now measured, and it
  found a systematic phantom-node defect in the Python front-end. What is still missing is a
  *sample of a real repository* — the corpus can only find what a fixture was written to
  contain. That remains phase 4.
- **No number about real code.** Every figure above describes 7 fixtures deliberately built
  around hard shapes. They are pessimistic by construction and say nothing about any actual
  repository. **This is what phase 2 exists to fix**, and it is now the largest gap.
- **`source-parity` is per-language and binary.** It fires when a language has *zero* nodes
  of a kind while the source declares one. A repo with 40 routes and 3 `Endpoint` nodes
  passes it silently.
- **No trend.** Accuracy could halve between releases and nothing would say so. `pkg
  accuracy` reports; nothing records or gates.

---

## 1a. Why this is worth doing — the credibility argument

Every claim Spine makes about a codebase is a claim about the graph. Right now those claims
are *qualitative*: "grounded", "deterministic", "precision-first". None of them is a number,
and none survives the only question a serious evaluator asks — **"how do you know?"**

Consider the difference in these three sentences, all describing the same feature:

| today | after phase 1 | after phase 5 |
|---|---|---|
| "Blast radius is grounded in the call graph." | "`CALLS` recall is 0.83 on Python; this list is a lower bound." | "0.83, up from 0.71 last release, and CI fails below 0.80." |

The first is marketing. The second is engineering. The third is a **guarantee**, and it is
the only one a regulated buyer, a platform team, or a due-diligence process can act on.

**What each phase unlocks, concretely:**

- **A defensible answer to "is this accurate?"** — the question that ends most evaluations
  of a code-intelligence tool, currently unanswerable except by anecdote.
- **A bound we can publish per language.** Today the capability matrix says a front-end
  *can* emit `Endpoint` nodes. It cannot say the front-end finds 9 routes in 10. Buyers
  read capability as coverage; that mismatch is a support ticket waiting to happen.
- **A regression gate on comprehension quality.** We already fail CI when the knowledge
  base is not deterministic. We do not fail it when a front-end quietly stops resolving
  half the call sites — the more damaging failure of the two.
- **Honest degradation instead of silent under-reporting.** A recall number lets the build
  document say "this is a lower bound, and here is how low" rather than presenting a
  clipped list as complete.
- **A basis for pricing confidence.** Section 12's confidence band is currently five
  structural checks. With measured recall it becomes a statement about the *evidence*
  rather than about the plan's own tidiness.

**The risk of not doing it** is specific, not vague: the graph can degrade without anyone
noticing. That is not hypothetical — it already happened once. Relative imports stopped
resolving, 91% of a real package looked unimported, and **204 passing tests said nothing**.
`pkg verify` exists because of that incident. It catches the *catastrophic* version. It
would not catch a front-end quietly dropping a third of its edges, and nothing else would
either.

---

## 2. The roadmap

Ordered so each phase makes the next one cheap, and so the first one is worth having alone.
Effort is one engineer, and deliberately front-loaded: the expensive thinking is in phase 1.

| phase | ships | effort | the number it produces |
|---|---|---|---|
| 1 | labelled corpus + `pkg accuracy` | ~1 week | precision and recall per kind, per language |
| 2 | oracle-based checks | ~2 weeks | recall lower bound on *any* repo, unlabelled |
| 3 | per-construct parity | ~2 days | per-file recall for routes and tables |
| 4 | sampled fact audit | ~3 days | false-positive rate on the joins |
| 5 | scoreboard + CI gate | ~3 days | the derivative — accuracy over time |
| 6 | numbers in the document | ~2 days | the caveat a reader can reason with |

### Phase 1 — a labelled corpus, and the two numbers ✅ SHIPPED 2026-08-13

**Shipped:** `orchestrator pkg accuracy` reports precision and recall per node kind and edge
kind, per language, over 7 hand-labelled cases. Numbers and findings are in
[Progress](#progress) above; the build document is
[`build-documents/PKG-ACC-1-build.md`](build-documents/PKG-ACC-1-build.md).

*Original text follows, unedited, so the plan can be read against the outcome.*

**Ships:** `orchestrator pkg accuracy --corpus <path>` reporting precision and recall per
node kind and edge kind, per language.

A handful of small repositories per language, each with a hand-written expected-facts file:
these functions exist, this calls that, this route maps to that handler. Small enough to
label by hand and re-check when a front-end changes; real enough to include the shapes that
break extractors — decorators, generics, partial classes, dynamic dispatch.

Then the two numbers nobody has:

- **precision** — of the edges we emitted, what fraction are real?
- **recall** — of the edges that are real, what fraction did we emit?

**Why first:** every later phase is an argument about a number. Without this there is no
number, and "the graph is good" stays an opinion. It also converts each front-end's
"precision-first" stance from a claim into a measurement — and if precision is not ~1.0,
that stance is not being honoured.

### Phase 2 — oracles that need no hand-labelling ✅ SHIPPED 2026-08-13 (runtime oracle)

**Planned:** [`build-documents/PKG-ACC-2-build.md`](build-documents/PKG-ACC-2-build.md)
covers the **runtime oracle only** — the first of the five below, and the one this section
already calls highest-value. The other four are separate tickets against the same report
surface, because bundling five oracles into one ~2-week ticket is how a phase stops being
reviewable.

**Shipped.** `orchestrator pkg accuracy --oracle runtime` traces a repository's own test
suite and reports `CALLS` recall from real execution. On this repo: **0.70 lower bound**
over 1,357 observed call pairs, **zero unmapped**, at 21.2% statement coverage, in 34s.

**The number moved twice in one morning, and that is the feature working.** The spike
measured 0.61 over 1,019 pairs; an hour later the same codebase measured 0.70 over 1,357.
Nothing about the graph changed — `tests/pkg` gained 18 tests. A recall figure from a trace
is bounded by what the suite exercises, and here is that bound behaving exactly as the
caveat says it does. Quote it with its coverage or not at all.

Two things the plan establishes that this section did not:

- **The oracle measures recall and nothing else.** A call the trace never observed is
  *untested*, not *false*, so precision is not computable from a trace. Any implementation
  reporting one is lying.
- **`sys.monitoring` (PEP 669) is guaranteed**, not merely available — `requires-python` is
  `>=3.12`. It gives both ends of the call directly, where `sys.settrace` would force the
  caller to be inferred from the stack. The phase is cheaper to build now than when it was
  written.

**Ships:** `pkg accuracy --oracle <kind>` against sources that already know the answer.

Hand-labelling does not scale past a few thousand lines. These do:

| oracle | checks | catches |
|---|---|---|
| **Runtime tracing** (`sys.settrace`, `coverage`) over a repo's own test suite | `CALLS` recall | a call that *demonstrably happened* and has no edge |
| **The language's own toolchain** — `tsc`, Roslyn, `javac` symbol tables | node and edge recall | what a compiler resolves and we did not |
| **Import machinery** — actually importing the module | `IMPORTS` soundness | a resolved import that does not resolve in reality |
| **A live database** — schema introspection | `Entity` / `Field` | the SQL front-end's reading versus the real schema |
| **OpenAPI documents** the service already publishes | `Endpoint` / `EXPOSES` | routes declared in ways the extractor cannot see |

Runtime tracing is the highest-value one: it produces a **lower bound on recall from real
execution**, with no labelling at all, on any repo with tests.

### Phase 3 — parity per construct, not per language ✅ SHIPPED 2026-08-13

**Shipped**, and the roadmap's one-line plan for it was wrong. This section said *"same
regex signals already in `_source_signals`, counted instead of tested for non-emptiness"*.
Counting the regex produces **0.74**, and it is a fabrication: 19 of the 25 apparent misses
were route decorators quoted inside test fixtures and the route extractor's own docstrings.

**The regex was correct for the question it was asked.** `_source_signals`' docstring says
so — *"the question is 'does this source declare any?', not 'which ones'"*. Existence-
checking tolerates a false signal because one real node anywhere in the language silences
the check. Counting turns every false signal into a phantom missing route, so the
justification for regex expires exactly when this phase begins. It is an AST ticket.

With AST counting: **68 declared, 70 in graph — shortfall 5, surplus 7.** Reported apart,
never averaged: a doubly-mounted router legitimately yields more nodes than decorators, so
a combined ratio (1.03 here) reads as recall while hiding both halves.

**It immediately found two invisible production routes.** `registry/api/app.py:110`
declares `GET /healthz` and `GET /readyz` inside the `create_app()` factory; the router is
a local variable the extractor cannot resolve, so the graph holds nothing. Blast radius,
impact analysis and the build document all believe those endpoints do not exist — and the
previous check could never have said so, because Python *does* have `Endpoint` nodes.

Plan: [`build-documents/PKG-ACC-3-build.md`](build-documents/PKG-ACC-3-build.md).

*Original text follows, unedited.*


**Ships:** `source-parity` counting rather than existence-checking.

Today it asks "does this language have any `Endpoint` node?". It should ask "this file
declares 4 route decorators and the graph holds 1 — where did 3 go?". Same regex signals
already in `_source_signals`, counted instead of tested for non-emptiness, reported per
file with `file:line`.

Cheap, needs no corpus, and turns a coarse tripwire into a usable recall estimate for the
constructs that matter most.

### Phase 4 — the false-positive side

**Ships:** `pkg accuracy --sample N` — a reviewable sample of emitted facts with the source
line each was derived from.

Every existing check hunts for absence. Nothing hunts for invention. The joins are where
this bites: `CONSUMES` matches on `(verb, path)`, `EXPOSES` composes mount prefixes,
ORM `REFERENCES` guesses at class names. Each is a place where a wrong edge is *plausible*,
and a wrong edge is worse than a missing one because every surface downstream presents it
as grounded.

A sampled audit costs a person twenty minutes per release and is the only thing that would
catch a systematically wrong join.

### Phase 5 — a scoreboard, and a regression gate

**Ships:** accuracy numbers per release, committed, and CI failing on a drop.

One table per language per edge kind, versioned. The point is not the absolute number —
it is the derivative. A front-end that silently stops resolving something should fail a
build, exactly as `understand --check` does for determinism today.

### Phase 6 — carry the number into what people read

**Ships:** the build document's sections 4 and 5 stating the measured recall for the
language they are describing.

Section 5 already says *"method calls through an instance emit no `CALLS` edge, so
per-method counts under-report"* — a qualitative caveat, hand-written, and true. With a
measurement behind it, it becomes *"`CALLS` recall on Python is 0.71 — this list is a lower
bound"*. That is the difference between a caveat a reader skims and a number they can
reason with.

---

### Phase 7 — from mechanism to meaning: the intent layer

**Ships:** `Intent` nodes and a `SERVES` edge, so `explain_symbol` can answer *"what is this
for"* rather than only *"what calls it"*.

Accuracy asks whether an edge is real. This asks what it **means** — and it is the larger
gap, because the graph's entire vocabulary is mechanical. Seven node kinds, all physical
artefacts. Ten edge kinds, all mechanical relations. Exactly one — `MENTIONS`, doc → symbol
— carries meaning rather than mechanism.

Nothing in the graph says what a subsystem is *for*, which requirement a function satisfies,
or why an edge exists. `episteme/`'s "domain model" does not fill this: it renders tables and
foreign keys, which is structure wearing a semantic name.

**The intent already exists in this system. It is simply not in the graph.** `Intent` is
defined in intake as *"a discrete, buildable capability derived from requirements"*, with
verbatim acceptance criteria. Build documents, the journey, tickets, `docs/specs/` records,
commit messages, and branch names encoding an issue key are all intent — and **none of them
links to a symbol.** Same shape as the `EXPOSES`/`CONSUMES` gap: two halves, no join.

Build it by **evidence tier**, reusing the provenance vocabulary the build document already
established, strongest first:

| tier | how intent is established | deterministic |
|---|---|---|
| **stated** | a doc, spec or ticket names the symbol | yes — `MENTIONS` does this today |
| **recorded** | git says commit *C* touched this symbol; branch `feat/…/SSPN-49` says *C* served ticket 49 | **yes, and unbuilt** |
| **structural** | it sits behind an `Endpoint`; only tests call it; it is an area's fan-in | yes |
| **inferred** | a model read it and summarised | no — label `derived · model` |

**The recorded tier is the free win, and it should be built first.** Spine *creates* those
branch names and already has `_issue_key_from_branch`. Walking `git log` yields
symbol → commit → issue key with no model, no labelling, and line precision. That single
join turns "this function has 6 callers" into "this function was built for SSPN-49, whose
criterion 3 it satisfies, and last changed for SSPN-12".

**The trap is starting at the inferred tier** — asking a model to narrate the graph. It
produces fluent, unfalsifiable prose about every symbol, and it would destroy the one
property that makes the graph worth trusting. Intent facts must carry provenance labels for
exactly the same reason the build document's sections do.

---

## 3. What this is really about

The graph is trusted **because it is deterministic and grounded**, and those are real
properties. But neither is accuracy. A deterministic extractor can be reliably wrong, and
`file:line` provenance proves a fact came from somewhere, not that the reading was correct.

Two live examples from this codebase:

- **`CONSUMES` resolves only literal paths.** `cli.py`'s template/contract group builds
  `f"/v1/{entity}"`, so five of six call sites emit nothing. The graph is not wrong — it is
  *incomplete in a way nobody can quantify*, and the build document has to say "silence
  rather than absence" because no number exists to say more.
- **Pointing `sdlc plan` at `/tmp`** produced a well-formed document about leftover build
  copies and reported "the brief agrees with the design on 5 file(s)". Every section was
  correctly labelled and every one was useless. Nothing in the pipeline notices it is
  grounded in garbage.

**Phase 1 alone changes the conversation**, and it is a week's work rather than a quarter's.
Everything after it is refinement — and, as with the build document's own roadmap, the
temptation will be to build the scoreboard before there is anything true to put in it.

**Two phases stand apart from the rest.** Phase 2's runtime oracle is the only one that
scales to a customer's repository without anyone labelling anything — point it at a repo
with tests and it returns a recall floor from real execution. Phase 7's recorded tier is
the only one that adds *meaning* rather than confidence, and it needs no model at all.
If only two things are built, build those.

**What would make this credible rather than merely measured:** publish the corpus and the
method alongside the numbers. A recall figure nobody can reproduce is an assertion with a
decimal point in it, which is the failure mode this whole document exists to avoid.
