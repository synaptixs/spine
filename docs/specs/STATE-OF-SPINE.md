# State of Spine — 3.28.0

**The one document to read.** Verified against source on **2026-09-02**, at the 3.28.0 release
cut. Every number below was re-measured that day.

> **Why this exists.** `docs/specs/` holds **83** markdown files — **80 specs** plus this
> page, [`README`](README.md) and [`SPEC-INDEX`](SPEC-INDEX.md) — with 6 archived, 10 build
> documents, and 17 root-level user documents.
> Answering "where do we stand?" required opening five of them and reconciling three that
> disagreed. This page carries the current answer; the others are the detail behind it.
> **If this page and another document disagree, this page was checked more recently — but fix
> the other one rather than trusting either blindly.**

---

## 1. What Spine is, in one paragraph

Spine reads a requirement, builds a deterministic graph of the target codebase, plans the change
against that graph, generates code and tests, gets them green, and opens a PR — with two human
gates (before building, before merging). The product is **Spine**; it ships as
**`synaptixs-spine`** with the **`orchestrator`** command.

## 2. Where the numbers stand

| | Value | How it is known |
|---|---|---|
| Version | **3.28.0** | cutting now; 3.27.0 is the last on PyPI until this ships |
| Languages extracted | **8** front-ends | Python, Java, TypeScript, C#, C, C++, Go, SQL |
| CLI commands | **57** | `grep -c '\.command(' src/orchestrator/cli.py` |
| Source modules | **332** | `find src/orchestrator -name '*.py'` |
| Test functions | **2,872** across 297 files | `grep -rh '^def test_\|^async def test_' tests`; files via the same pattern with `-rl` |
| Graph precision | **1.00** on every node and edge kind, all 8 front-ends | `orchestrator pkg accuracy` against a hand-labelled corpus |
| `CALLS` recall | **1.00** (C, SQL) → **0.86** (TypeScript, on 14 labelled edges) | same |
| Grounding effect, `create` tickets | **29/50 grounded, 0/50 ungrounded** | 200-run controlled A/B, 2 frontier models, 5 passes |
| Same, across two codebases | **47/68 vs 3/68** | replicated on an unrelated external repo |
| Control (`edit` tickets, target file named) | **122/124 either arm** | rules out a generic more-context effect |
| SWE-bench | **no number — none has been run** | ❌ means absent, not low |

**The one claim worth repeating:** every new module that integrated correctly came from a grounded
run. The graph pays exactly where the model cannot see the target, and ties where it can.

## 3. The PKG — what it is, how it is built, and why any of it matters

*For engineers. The narrative version is [`KNOWLEDGE_GRAPH.md`](../../KNOWLEDGE_GRAPH.md); the
full front-end detail is [`parsing-and-the-pkg.md`](parsing-and-the-pkg.md).*

Everything else on this page rests on one artifact. The **Product Knowledge Graph** is a typed
fact graph of the target codebase — **8 node kinds** (`Module` `Type` `Function` `Field`
`Endpoint` `Entity` `Doc` `Intent`) and **11 edge kinds** (`IMPORTS` `CONTAINS` `CALLS`
`IMPLEMENTS` `READS` `WRITES` `EXPOSES` `CONSUMES` `REFERENCES` `MENTIONS` `SERVES`) — where
**every fact carries `file:line` provenance**. No model builds it, and no model is consulted
while it is built.

### AST or CST — Spine uses whichever one the language's own toolchain uses

The distinction decides what a front-end can *see*, and where it breaks.

- An **AST (Abstract Syntax Tree)** is what a compiler keeps after discarding everything that
  does not change meaning — parentheses, whitespace, comments, exact token order. `a + (b)` and
  `a+b` produce the same tree.
- A **CST (Concrete Syntax Tree)** keeps every token and its byte range; you can reconstruct
  the source character-for-character. tree-sitter produces one.

| Front-end | Parser | Tree | Why this one |
|---|---|---|---|
| `python` | CPython `ast` (stdlib) | **AST** | The same parser that *runs* the code — no second implementation to disagree with |
| `java` `csharp` `c` `cpp` `go` `typescript` | tree-sitter + the language's official grammar | **CST** | Community-maintained, fast, and **error-tolerant** — a file that does not compile still yields the parts that parse |
| `sql` | `sqlglot` | **AST** | Dialect-aware (postgres / mysql / tsql / oracle …); treats SQL as a language, not as text |

**Structure comes from a real parser, never from pattern-matching source text** — any regex over
source is a second, worse implementation of a parser, and it fails exactly where the code is
hardest: nested generics, macros, string literals containing what looks like syntax. The
capability matrix scores this row *"real parser, never regex"*, and **"never" is three cases too
strong** — state them, because a reader will find them:

| Where | What it recovers | Why the parser cannot |
|---|---|---|
| `java_extractor` `_PACKAGE_RE` · `csharp_extractor` `_NAMESPACE_RE` | the one-line `package` / `namespace` declaration, used to *name* the `Module` node | the tree-sitter walk reads the file's members; the declaration is read separately rather than walked to |
| `sql_extractor` `_CALL_RE` | `CALL` / `PERFORM proc()` — and these become **real `CALLS` edges** | sqlglot collapses both into opaque `Command` nodes, so the callee is not in the tree to read |

The first two name a node the parser already found. **The third emits edges**, which is the one
that would matter if it were wrong — and it is the thinnest-evidenced cell on this page: SQL's
`CALLS` scores 1.00 precision and 1.00 recall, **on a corpus of exactly one labelled call edge**.
That is a passing test, not a measurement. The fallback is confined to two keywords followed by a
parenthesis, so the blast radius is small, but anyone quoting SQL call accuracy should quote the
denominator with it. Everything else in all eight front-ends comes off the tree.

**Why not one parser for all eight?** Because a second implementation of a language is a second
*opinion* about what the language means, and the two diverge on the hard cases. Using CPython's
own parser for Python removes that risk entirely for the largest front-end.

### Parsing is not where accuracy is lost — resolution is

Measured against a hand-labelled corpus, all 8 languages
(`orchestrator pkg accuracy`, gated in CI):

| | Result |
|---|---|
| **Precision** | **1.00** on every node kind and every edge kind, all 8 languages — *on the corpus*, and see the caveat below |
| **Recall** | 1.00 on every kind **except `CALLS`** |
| `CALLS` recall | 1.00 (c, sql) · 0.73 (python) · 0.67 (cpp, csharp, go, java) · **0.86 (typescript)** — every one of the four denominators is small; TypeScript's is 14 labelled edges, doubled on 2026-09-01 |
| Invention | **0** on this repo and on 11 pinned public repos across 6 front-ends, gated `strict` at zero per language (2026-08-24) |

**That precision row was a corpus score over a corpus missing a shape, and both have been
fixed.** When a parameter or local **shadowed** a resolvable name, TypeScript, Go, C++ and C#
each emitted a `CALLS` edge to the file-level definition the caller never reaches — the bug
Python fixed in 3.18.0, in a form the fix did not reach. Nothing caught it: the target is a
real node so `pkg verify` saw no dangling edge, no fixture carried the shape so precision read
1.00, and the invention oracle was Python-only. Found by widening the oracle to six
front-ends, measured at **47 fabricated edges across 23,746 bare calls** on 11 public repos,
then fixed by porting C's `_bound_names` to the other four. **47 edges removed, 47
fabrications — no true edge lost.** Guarded by `corpus/*/shadowed_calls`, four cases written
and failed at 0.50 precision *before* the fix. Full record:
[`invention-oracle-cross-language.md`](invention-oracle-cross-language.md).

Structure — *this module contains this class which contains this method* — is either in the tree
or it is not. **`CALLS` is different because it needs *resolution*:** the tree tells you a call
happened and how it was spelled, not which definition it reaches. That is a judgement, and
judgements can be wrong. It is scored on its own for exactly that reason, rather than folded
into an average that would hide it.

**The rule is skip rather than guess** — and the cost of the other choice is on record.
`_resolve_call` used to invent an id for any unresolved bare name; every parameter, local and
nested function became an edge to a function that did not exist. That was **497 fabricated edges
on this repo alone — 3.16% of the call graph** — and worse on external code: **14.8% of unique
call relationships in Flask, 7.4% in httpx**. Two things about that bug are the whole argument
for this section:

- **It was self-consistent.** The inventor created the phantom *node* as well as the edge, so
  `pkg verify`'s dangling-edge check reported **0** the entire time it was live. *A graph can be
  internally perfect and externally false*, and no structural check will tell you.
- **It corrupted real nodes.** The invented id collided with legitimate ungrounded types, so
  `py:Exception` — a `Type` named `Exception` — became a `Function` literally named
  `"py:Exception"`.

The fix was one line, shipped in 3.18.0: return `None` instead of a guess. That is why the
invention oracle exists as a standing measurement rather than a one-off, and why `CALLS` recall
is allowed to sit below 1.00 on TypeScript instead of being padded.

### Why this matters

**1. The failure mode is silence, not fiction.** Everything the graph asserts exists; what it
cannot resolve, it drops. For a model consuming the graph those are not remotely equal costs. A
**missing** edge makes an agent go and look. A **fabricated** edge makes it confidently follow a
call into a function nobody ever wrote — and produce a change that is coherent, plausible, and
built on nothing. Precision is held at 1.00 and recall is allowed to be imperfect because that
trade is the right way round for the consumer.

**2. It is deterministic, so it is checkable.** Same commit in → same bytes out. Nothing else on
this page is possible without that property: `understand --check` can gate the knowledge base as
*provably* current rather than hopefully current; the extraction cache can be commit-keyed
(and is trusted only on a clean tree); a run's Evidence can be reproduced at a commit and
therefore replayed and diffed. A graph that redrew itself differently for identical input could
not be gated by anything.

**3. Provenance is what makes a claim falsifiable.** Every node carries `file:line`, so a
downstream assertion is an address a human can open rather than a summary they must trust. This
is what lets acceptance criteria be **bound to a `file:line` or the ticket refused** (§7), and
what lets a design naming code that does not exist be caught instead of built.

**4. It is measurably what makes the delivery half work.** Across 260 runs on two frontier
models, **47 of 68** new modules integrated correctly with the graph in context versus **3 of
68** without — while tickets that already named their target file scored **122 of 124 either
way**, the control that rules out "more context just helps". The graph pays precisely where the
model cannot see the target, and ties where it can.

**5. It is cheap enough to be unconditional.** A full extraction of this repository — **11,761
nodes, 34,041 edges** across 320 modules — takes **~2.3s** cold, and is cached per commit after
that. Nothing in the pipeline has to ration it.

### Where it is honestly weak

- **`CALLS` recall on TypeScript is 0.86**, on 14 labelled edges — up from 0.36 on 2026-09-01, and the remaining loss is one shape,
  not a family: a receiver typed only by an object-literal field (`{ h: new Handler() }`). Every
  other probed shape — annotated parameter, `const`/`let` with `new`, and `new Handler().run()` —
  resolves, through a local pass over the same tree-sitter CST with no new dependency. Scoped in
  [`typescript-call-resolution.md`](typescript-call-resolution.md), which argues against the
  compiler API on determinism grounds and did not need it.
- **`runtime` is still Python-only** (PEP 669) — the last of the four oracles with that
  limit. On a non-Python repo it reports nothing, and that is *not measured*, not clean.
  `invention` was the same until 2026-08-24 and now walks six front-ends, naming Java and SQL
  as not-applicable with reasons rather than scoring them 0.
- **The invention oracle only knows about shadowing.** Other classes — `CONSUMES` matched on
  `(verb, path)`, `EXPOSES` composed from mount prefixes — have no detector at all, and rest
  on `sample_edges` plus a human reading the source.
- **8 languages**, against 21–40 for the graph-only tools. Additive against a fixed schema
  (`facts.py`) rather than a ceiling, but it is today's number.

## 4. The delivery pipeline as it actually runs

`sdlc autorun` is six stages. **`async` does not mean "calls a model" — four are `async`, three
call a model.**

| Stage | Model? | What it does |
|---|---|---|
| `intake` | **yes** | source document → one spec |
| `investigate` | no | PKG query → landing symbols with `file:line` |
| `validity` | no | judges the ticket against the graph; **the only stage that can stop a run before code is written** |
| `design` | **no** — `produce_design(..., llm=None)` | deterministic by decision, not by default: measured 2026-08-19 and declined. A design that fails its output-edge validator **parks** — Phase 4 tried to repair it within a budget and the repair proved unreachable (§7) |
| `implement` | **yes** | codegen + tests + refine |
| `review` | **yes** | review the worktree diff, fix findings, re-test |

Each model output has a deterministic validator downstream — intake's spec by `assess()`,
implement's code by tests + preflight baseline-diff + fit, review's fixes by re-running the tests.
**`design` has none**, which is safe only while it calls no model. That seam is the subject of §7.

**Since Phase 2a every run also writes `evidence.md` / `evidence.json` / `criteria.md` / `design-references.md` / `case.json`**
beside its brief. `validity` judges the ticket against that Evidence, `design` is handed its blast
radius rather than computing one, and every acceptance criterion is bound to a `file:line` or
refuses the ticket. Two verdicts are new and both can park a run that previously built: an
unbound criterion, and a design naming a place the repository does not have.

## 5. Where Spine is genuinely ahead, and where it is not

**Ahead — 21 rows in the capability matrix nobody else fills**, out of 46. The strongest four
are not about features: published precision/recall of its own graph, a published controlled A/B
for the context layer, replication on an external codebase, and held-out grading. Every published
benchmark found in the code-intelligence category measures *efficiency* ("70% fewer tokens"),
which answers *how cheap*, not *is it right*.

> This line and the matrix's own summary **disagreed** until 2026-08-21 — 11 here against 16
> there, for the same table, and the answer was 22 at the time. Both are now derived by
> `python scripts/matrix-count.py --check`, which fails if either drifts again. The count was
> the one number on this page nobody could re-run, which is why it was the one that rotted.

**Behind, and not disputed:** language breadth (8 against 21–40 for the graph-only tools) and
adoption by orders of magnitude.

**Genuinely absent:** a SWE-bench number, an in-path security verifier, a secrets vault beyond
`.env`. RBAC is **🟡** — identity and tenancy are enforced everywhere (16 of 23 route modules, 23
tenant-scoping sites, cross-tenant reads return 404), but the role check `has_role` is called at
**exactly one site**, the approval decision.

## 6. How Spine is adopted, without entering anyone's build image

Spine is never a dependency of the project it works on. It operates on a checkout from outside.

| Mode | Mechanism | Project's image |
|---|---|---|
| Developer / IDE | `pip install synaptixs-spine`, or MCP server from Claude Code / Codex | untouched |
| CI job | installed at job time, runs against the checkout | untouched |
| Central service | FastAPI registry + GitHub App; clones via `WorkspaceManager` into worktrees | untouched, project unaware |

What lands in a target repo is inert data — `episteme/`, `.spine/`, `.spine-media/`. No import, no
entry point, nothing resolved at build or runtime.

**The real coupling is the verification toolchain, not the build image.** Preflight shells out to
the repo's own `ruff`/`mypy`; Go needs `go build`/`go test`. So whatever *runs* Spine needs the
project's tools. Recommended shape: central service for orchestration, project's own CI container
for build and test, Spine installed at job time.

**Known gap for the central mode: there is no Dockerfile or published image in this repo.** Going
central today means building that image yourself.

## 7. The GraphIR programme — Phases 1–3 delivered, Phase 4 closed unshipped

Spine runs **two orchestration systems that never touch each other**: `sdlc/autorun.py` is an
imperative pipeline, and GraphIR + planner + verifier chain + Temporal is a typed, replannable
workflow engine reachable only from the task API. `autorun.py` contains no reference to any of it.

The programme makes the SDLC *be* a GraphIR workflow whose first act is **research** — deterministic
Evidence (investigate + RCA + blast radius) that design, codegen and the acceptance criteria are all
bound to — without converting a deterministic stage into a model call.

**Four defects it fixes, each verified in source:**

1. **RCA never runs.** `build_rca` is deterministic and is not reachable from `autorun` at all.
2. **Blast radius is computed from the design's own proposal**, so a wrong proposal yields a
   faithful analysis of a fiction — and it reads as verification.
3. **Evidence is discarded between stages.** `Landing{name, where, kind, callers, module}` is
   reduced to `where.split(":")[0]` — a filename.
4. **Acceptance criteria are never bound to evidence.** They come from intake (a model that had not
   read the code) and are read straight through by design, codegen and grounding.

| Phase | Deliverable | Closes | Status |
|---|---|---|---|
| 1 | `tool` node type + the **Evidence** artifact, SDLC as IR in shadow | defect 1 | ✅ **COMPLETE 2026-08-18** |
| **2a** | IR executes; Evidence consumed; criteria bound; `RunContext` → typed Case | **defects 2, 3, 4** | ✅ **COMPLETE 2026-08-18** |
| **2b** | `design` promoted to hybrid — validator, then `_llm_design`, then measure | none | ✅ **COMPLETE 2026-08-19 — promotion declined, measured** |
| 3 | Issue-type profiles as files a repo can carry | none — configurability | ✅ **COMPLETE 2026-08-19** |
| **4** | Parallel fan-out + bounded replan | none | ❌ **NOT DELIVERED 2026-08-23 — both halves measured and declined; per-node wall-clock kept** |

**Phase 2a shipped 2026-08-18 — every defect in the programme is now closed.** Evidence drives
the run: `validity` judges the ticket against it, `design` is handed a blast radius keyed off
where the ticket lands instead of computing one from its own guess, the landing facts keep their
symbols instead of collapsing to filenames, and each acceptance criterion is bound to a
`file:line` or refused. Gate: **20 runs / 5 commits, 0 unexplained verdict mismatches**, with 5
new parks — all the one ticket naming a symbol no repository has.

**Phase 4 delivered neither half, and is closed rather than complete.** The gate said *wall-clock
per ticket drops*; the research nodes were timed first and only `investigate` (0.034s) and `rca`
(0.057s) are independent, so the entire available saving is **~30ms** of a ~2.3s pass. Preflight
is serial too, but cold `mypy` is 16.3s against ruff's 0.1s; the coverage probes cannot be
parallelised at all (each `git stash`es the shared worktree); and the fan-out with real
wall-clock in it — one child workflow per issue, bounded by `max_parallel_features` — shipped
before the phase was written.

**The bounded replan was built and then reverted**, which is the more useful half of the story.
`Budget.max_replan_count` was honoured, a repair loop fed refused references back into the design
node, and six tests passed. Then the trigger was probed: `validate_design` refused **0 of 6** real
specs, including one naming `made_up_pkg/thing.py`. `_fallback_design`'s three sources cannot
fabricate — stated paths are filesystem-filtered, and the other two come from the graph — and the
one producer that could, `_llm_design`, is switched off because 2b declined that promotion. **The
budget could not be spent.** Underneath that: re-running a deterministic producer returns the same
answer, so a replan loop only means anything for a non-deterministic one. Every test had
`monkeypatch`ed the producer to manufacture the failure, so the mechanism was verified and the
trigger never was — internally perfect, externally inert, which is the failure §3 of this page
describes in another form.

**What was kept:** per-node wall-clock in the `Case` (every row read `0.00s` before, which made
"where did the time go" unanswerable from the artifact built to answer it), and two parallel-shape
validator rules that do not fire today but close a real hole — `_check_sequential_shape` inspects
only the agent condensation, so a fan-out over `tool` nodes passed validation with nothing having
looked at it. Detail: [`graphir-sdlc-workflow.md`](graphir-sdlc-workflow.md), Phase 4.

**Phase 3 shipped 2026-08-19.** Three profiles — `default`, `bug`, `enhancement` — chosen from
the ticket's issue type by a deterministic lookup, never a model. A repo may carry its own in
`.spine/workflows/`, where the same name wins. The enhancement profile has no `n_rca`: root-cause
analysis localizes a symptom and a feature request has none, so an enhancement run records it as
*not run for this issue type* rather than printing "not localized". **Acceptance impact was not
measured** — no bug corpus exists, and the phase is recorded as configurability rather than
claiming a number it did not take.

**Phase 2b closed 2026-08-19 with the promotion declined.** A 100-run A/B ($49.51, 0 aborts)
found no acceptance difference a 50-run arm can resolve, a held-out rate favouring the
deterministic design (0.60 vs 0.40), and 1.98× the cost. `design` stays deterministic; the
model-call budget stays at three. The validator ships regardless — **0 false positives across
100 runs**, including 50 real model-written designs. Detail:
[`design-promotion-ab-results.md`](design-promotion-ab-results.md).

**Phase 2 is split.** 2a is deterministic and its gate costs nothing; 2b is the design promotion
and its gate is a paid A/B. Splitting keeps the defect closure — the value of the phase — from
being held behind a benchmark run.

**Phase 1 shipped 2026-08-18.** `NodeType.TOOL`, an in-process tool registry with output digests,
`Evidence` (investigate + RCA + blast radius keyed off the landing sites), the pipeline as
`sdlc/profiles/default.yaml`, and a shadow pass in `autorun` that compared the graph's
deterministic nodes against the imperative stages — **superseded by 2a**, where those nodes
execute for real and there is no shadow left to compare. `orchestrator sdlc workflow` prints the
validated graph. Gate at the time: **20 runs, 5 commits, 0 divergences**, proved able to fail
three ways before it was believed.

**Phase 1 was enablement; 2a is where the value landed.** Phase 1 produced Evidence and nothing
read it — deliberate, since that is what made it shippable with zero behaviour change. All four
defects were shut by the end of 2a. Kept here because the sequencing is the part worth reusing:
produce the artifact in shadow first, consume it second.

Full record: [`graphir-sdlc-workflow.md`](graphir-sdlc-workflow.md). Its governing rule — a node may
be demoted to deterministic freely, and promoted to model only with a measurement, a validator on
its output edge, and inside the model-call budget.

## 8. Outstanding, everything else

*This table is the authority.* [`enhancement-index`](enhancement-index.md) and
[`gap-roadmap-index`](gap-roadmap-index.md) are snapshots that feed it — seven rows below were
merged in on 2026-08-30 from those two pages and from
[`document-ingestion-reference`](document-ingestion-reference.md), where they had been recorded
and never promoted. A list that claims to be the authority has to actually absorb them.


| Item | State |
|---|---|
| Upgrade local `uv` past 0.8.0 | user's machine |
| `orchestrator --version` | ✅ **added 2026-09-01.** It errored with *"No such option"* — the first thing anyone runs after installing, and the path [`BENCHMARK.md`](../../BENCHMARK.md) now sends strangers down. It prints the installed version **and where it is running from**, because CONTRIBUTING's own warning is that a bare command resolves to whatever is on `PATH`. Found alongside it: `orchestrator.__version__` was the literal `"0.0.0"` and no release step ever touched it, so every version ever shipped reported 0.0.0 to anything that imported it — now derived from the installed distribution |
| RBAC role-gating beyond the approval decision + secrets vault | parked |
| CI gate on spec-status drift | 🟡 **the numbers half shipped 2026-09-01** — `scripts/state-numbers.py --check`, in CI, re-derives the eight figures this page and `SPEC-INDEX` state and fails when prose and source disagree. It caught a stale test count on its first run, and again when adding its own tests moved the number. **The judgement half is still open:** whether a spec's *status line* matches shipped reality is not mechanically checkable, and that is the class that produced the multi-repo row reading "not started" while the code was in `src/` |
| Decide `--intents` — shipped with no reader, no export | ✅ **closed 2026-09-01** — decided *keep*, and phases 1–3 shipped the same day: it reaches the export, `FactStore` answers both directions, and `investigate --intents` names the tickets each landing was last changed for. Precision fixed on the way (§6.1: `SHA-256` and `ISO-8601` were being read as tickets). Spec: [`recorded-intent-tier.md`](recorded-intent-tier.md). Not abandoned and not obsolete: the producer works and is measured (37 intents, 1,418 `SERVES`, 11.5% of symbols, 3.0s), and **nothing reads it**. The export gap is narrower than recorded: the exporters do not filter by kind, `pkg export` simply has no `--intents` flag while `understand`/`state` do, so `link_intents` is missing beside the `link_docs` post-pass whose own comment makes the same argument. Phase 1 is ~2 hours. **The standing risk is that the tier's value is a function of the customer's commit hygiene**, and this repository — squashed on import — is a bad demo of it |
| Rust front-end | not started |
| Express endpoint extraction (TypeScript) | unscheduled |
| Deployment image + reusable CI workflow for central adoption | not started. **Not a G4 phase** — it was recommended as though it were; it needs adding to that spec before it can be scheduled |
| Watch-items (WI) | 🟡 **spec written 2026-08-30; Phases 1–2 shipped 2026-08-31, Phase 3's gate withdrawn** — [`watch-items-roadmap.md`](watch-items-roadmap.md). Freshness dispatches per front-end and drift reaches the review. The drift **gate** shipped ratcheted and was un-gated the same day, one PR later, after it failed a documentation change: the denominator was sections (which prose edits do not move) and **about a tenth of the population cannot bind by construction** — parameters, module constants, string literals, log event names have no node kind. Denominator corrected to `mentions` (0.1005, not 0.5829); the number is recorded and trended, and is an **upper bound**, not a defect count. Gating it needs the population narrowed first — unscheduled. **WI-1 still recommended for removal** |
| `docs/specs/current-state.md` has a mermaid block that falls back to `<pre>` in our own UI | ✅ **fixed 2026-08-31.** Two violations of the subset `md.js` renders: nodes declared inline in edge lines, and `\n` instead of `<br/>`. Nodes are declared first and edges reference bare ids. **All 47 mermaid blocks across every tracked markdown file now render as inline SVG** — `node scripts/check-mermaid.js $(git ls-files '*.md')` |
| `stale_findings` fabricated staleness on 7 of 8 front-ends | ✅ **fixed 2026-08-31** — dispatch is per-suffix through the same registry extraction uses, and a suffix this install has no front-end for is skipped into `skipped_freshness` rather than judged. Nine tests, one per front-end, all verified to fail with the dispatch reverted. **Found 2026-08-30.** [`verifier.py:143`](../../src/orchestrator/pkg/verifier.py) re-extracts every changed file with `PythonExtractor()`, and the `except` treats an unparseable file as *everything in it is stale*. [`codereview/grounding.py:133`](../../src/orchestrator/codereview/grounding.py) passes every changed filename with no language filter, so a polyglot PR gets a stale-graph **WARNING per symbol** when nothing is stale. Measured on an unmodified scratch repo: **Python 0, TypeScript 2/2, Go 3/3**. Invisible to the suite — `src/` is Python-only and both walkers skip `corpus/`'s dot-prefixed fixture roots — and it fires only where something constructs `GroundingVerifier` — **which the production review path does not**, see the row above; the claim that this reached pull requests was made without checking the wiring and is corrected here. [WI-2 Phase 1](watch-items-roadmap.md) |
| `doc_findings` was built and never wired | ✅ **fixed 2026-08-31.** It is now called from `PKGGroundingVerifier.scan`, kept when the doc is in the diff **or** its mention appears on a **removed line** — the second rule catching the case a doc-file filter cannot see, a renamed symbol whose docs are nowhere in the diff. Two defects fixed beside the wiring: the 20-cap ran before any filter (so a repo with pre-existing drift would have gone quiet on the PR's own docs), and the finding carried no line, anchoring every comment at line 1. ✅ **The base-graph delta landed 2026-08-31**, closing the truncated-patch and indirect-removal gaps; drift from a previous merge stays out by design, as the base already had it. **And `scan()` itself is not called in production** (row above), so this closed a no-reader gap one level inside another one. [WI-2 Phase 2](watch-items-roadmap.md) |
| The PKG-grounded review layer is unreachable in production | ✅ **wired 2026-08-31, opt-in.** `ORCHESTRATOR_REVIEW_CHECKOUT=1` materialises the PR head at `head_sha` (depth 1, via the shared `core/pinned_checkout`), builds the grounder and the verifier from it, and tears it down after the review. It had to land **per review, inside `_compute`** — the head SHA is not known until the diff has been fetched, which is why the wiring was never there. A checkout that cannot be made **degrades rather than blocking**, and the review says so in its body: absent must not read as clean. Off by default, because cloning per review is an operator's cost to accept. This is what makes the two rows below reach a pull request at last; both were correct and inert. **Found 2026-08-31.** [`webhook.py:211`](../../src/orchestrator/codereview/webhook.py) builds `ReviewService(github=…, llm_reviewer=…, audit=…)` and passes neither `verifiers` nor `impact_source`, so `verifiers` falls back to `default_code_verifiers()` — secrets, security, style — and the impact brief stays `None`. Nothing outside tests constructs `PKGGroundingVerifier` anywhere in `src/`. **The impact brief, the stale-fact check and the doc-drift finding therefore never run on a pull request.** The cause is structural rather than an oversight: all of them need a *checkout* (`from_repo(root)`) and the webhook path has none — no clone, no worktree, no filesystem path — while `default_code_verifiers()` takes no arguments and so cannot build one. **This is the outer instance of the failure the two rows above are the inner instances of:** WI-2 Phase 2 wired `doc_findings` into a `scan()` that nothing calls, and WI-2 Phase 1 fixed a freshness bug on a path no PR reaches. Both fixes are correct and neither is reached. The base-graph delta followed the same day: `PRDiff` carries `base_sha`, the base is materialised beside the head, and doc drift is reported as **what this change broke** rather than inferred from what the patch removed |
| **G6 comprehension benchmark — COMPLETE** | ✅ **2026-09-01, all three phases.** Scope decided 2026-08-30, harness 2026-08-31, gold set 2026-09-01. D1–D4 taken and written into [`gap6-benchmarks-roadmap.md`](gap6-benchmarks-roadmap.md): hand-labelled gold set only, two metrics (top-k localization + provenance validity), five pinned repositories, ratchet gate. The harness is in: a validated five-repo manifest, fetch-and-persist materialisation, **provenance validity at 1.0000 on 10,789 anchored facts** (ratcheted), and `pkg accuracy --oracle comprehension`. **Localization is measured: top-1 0.32, top-10 0.71 on 38 labelled issues**, against 0.085 for picking ten files at random — roughly 8× chance, from an issue title alone. Every label's issue link is confirmed against GitHub's own closing references and every path verified present in the pinned tree. **n=38 puts the 95% interval on top-1 at 0.17–0.47**, the labels are fixes touching 1–3 files so the number is optimistic, and `investigate` got the title only so it is also pessimistic. **Phase 2 landed 2026-09-01: localization is gated** on the ratchet tier, but only when the gold set's digest is unchanged and both sides were measured — so reshaping the corpus is a rebaseline, not a regression, and an offline run cannot ratchet it down. `--scoreboard --pinned-corpus` records it, `--check --pinned-corpus` compares it; CI's default gate stays offline. **Phase 3 published 2026-09-01: [`BENCHMARK.md`](../../BENCHMARK.md)**, linked from the README — corpus SHAs, label derivation, both metrics, the 0.085 chance baseline, six numbered limitations and the commands that produced the figures. Writing it for an outsider caught a reproducibility hole: the numbers depend on installed language extras, and without them a front-end silently yields no facts and the score drops for reasons unrelated to Spine — `--pinned-corpus` now warns by name. **G6 is complete.** Note: `stale_findings` could not be the provenance metric the spec assumed — it is zero by construction on a fresh extraction |
| LLM temperature refusal is re-learned every call | ✅ **closed 2026-08-30.** `temperature` was popped from a **local** dict, so every call to a refusing model paid a failed round-trip and printed the same warning. Now a process-local `_TEMPERATURE_REFUSED` set, written at the refusal and read at param assembly: two completions cost three round-trips instead of four, and the determinism warning is said **once** — the later skips stay recoverable at `debug` under `llm.temperature_skipped`. Not persisted, so a fresh process re-probes rather than downgrading a model on one bad call |
| MCP server spawned per operation | 🟡 **halved 2026-08-30, pooling declined.** `MCPRegistry.call` opened **two** sessions for one structured call — `_encoded` re-asked for a schema discovery had already been told. Schemas are now cached on the registry at discovery, so it opens one. **Full session pooling is not done and is not scheduled:** it would push an async lifecycle onto every caller, `registry/api/connections.py` included, to save a session the cache already saves — and nothing counts MCP calls per run, so "call volume warrants it" ([`mcp/client.py`](../../src/orchestrator/mcp/client.py) docstring) is still unanswerable. Measure before building it |
| Stale second `## Unreleased` heading in the changelog | ✅ **fixed 2026-09-01.** The row said it needed *"someone who knows which release those entries actually shipped in"* — **git knew.** The entries were committed in `bbe912a` on 2026-08-06; `3.14.0` was cut the same day in `cd88136` with `bbe912a` as its ancestor, and 3.14.0's own section never mentioned them. So they shipped in 3.14.0 and the cut simply never renamed the heading. Folded into that section, where they already physically sat. **The row was blocked on a question nobody had asked the repository** |
| **56% of `Doc` sections bind to nothing** | not started, and **not a parsing defect** — those sections are perfectly-parsed markdown with no identifier in them. Closing it needs semantic matching, which means a model, which collides with the determinism that makes `understand --check` a gate. Any fix belongs in a labelled second tier, measured and declared the way GraphIR Phase 2b was. [Record](document-ingestion-reference.md) |
| PDF, `.rst`/`.txt` and media collapse to one `Doc` node per file | unscheduled. Media is the one fixable without a new dependency — its segments already carry `start_ms`/`end_ms` and could become timestamped sections. **Unmeasured** |
| Diagram arrows never become graph edges | **idea, no spec.** A mermaid flowchart is read as text; its labels bind to nothing and `intake --> design` produces no relationship. The more promising framing is the inverse — a diagram as a set of assertions to *check* against the graph rather than facts to add to it |
| Shadowed-name `CALLS` invention in TypeScript, Go, C++, C# | ✅ **closed 2026-08-24** — oracle widened to 6 front-ends, all four fixed, 4 corpus cases added, `invention` gated `strict` at zero per language. 47 fabricated edges removed with no true edge lost. [Record](invention-oracle-cross-language.md) |
| TypeScript resolution via the TS compiler API | 🟡 **scoped 2026-09-01, and the spec argues against it** — [`typescript-call-resolution.md`](typescript-call-resolution.md). The number was **0.50 in three places, is 0.36**, and moved twice on the way: 0.50 was stale (the scoreboard said 0.571), and widening the corpus on 2026-09-01 took it to **5 of 14**. **The drop is measurement, not regression** — the code did not change; two fixtures were added for shapes nothing was testing. Probing six call shapes showed the loss is one family: `this.method()` resolves, every call through a *variable* misses — including `new Handler().run()`, which needs no type inference at all. Four of five missed shapes are reachable with a **local** pass over the same tree-sitter CST, no new dependency and no determinism risk, so the compiler API is not the first move. Its third cost is disqualifying as normally implemented: resolution that reads `node_modules` gives a different graph for the same commit, which invariant 2 forbids. **The scheduled piece is widening the corpus first** — 7 labelled edges cannot tell you whether a fix works |
| G4 adoption — friction audit | ✅ **Phase 1 done 2026-08-19** — ≈28s cold start, no key; channels/proof/measurement outstanding |
| `episteme.yml` main-branch path produces orphan branches | ✅ **fixed 2026-08-19** — regeneration is `develop`-only; `main` inherits the bank verbatim |

## 9. The failure mode this project keeps having

Worth stating once, because it explains most of the corrections in this document's history.

**Green checks over unexamined cases.** `pkg verify` reported 0 dangling while 497 fabricated edges
existed. A benchmark arm reported `4/10` that was really "6 tickets never reached the model". Four
spec status lines read *"Not started"* for shipped work. `state` produced three different reports
from identical input for months. The `episteme` workflow warns and exits 0 when it cannot open a
PR, so three releases left orphan branches nobody saw.

Each was a check that confirmed the expected path and stayed silent on the rest, and **silence read
as success**. The countermeasures now in use: read per-item rows rather than summary lines, and
revert the fix to confirm a test actually fails.

---

## Where the detail lives

Do not read these to answer "where do we stand" — read them to act on a specific area.

| Question | Document |
|---|---|
| How do I install and run it? | [SETUP.md](../../SETUP.md), [USER_GUIDE.md](../../USER_GUIDE.md) |
| What can it do, exactly? | [FEATURES.md](../../FEATURES.md) |
| Every command and flag | [CLI_REFERENCE.md](../../CLI_REFERENCE.md) |
| What the graph holds, and its limits | [KNOWLEDGE_GRAPH.md](../../KNOWLEDGE_GRAPH.md) |
| How the parsers work (for engineers) | [parsing-and-the-pkg.md](parsing-and-the-pkg.md) |
| Where we stand against competitors | [capability-matrix.md](capability-matrix.md), [competitive-landscape.md](competitive-landscape.md) |
| The grounding measurement, in full | [codegen-model-comparison-results.md](codegen-model-comparison-results.md), [external-repo-grounding-results.md](external-repo-grounding-results.md) |
| The benchmark programme | [codegen-benchmark-roadmap.md](codegen-benchmark-roadmap.md) |
| The agentic-workflow programme | [graphir-sdlc-workflow.md](graphir-sdlc-workflow.md) |
| Deploy and operate | [OPERATIONS.md](../../OPERATIONS.md) |
| Every spec and its status | [SPEC-INDEX.md](SPEC-INDEX.md) |

**Maintenance rule.** This page is refreshed at each release, from source, in one pass. A number
here without a "how it is known" is a number that should not be here — and since 2026-09-01 that
rule is enforced rather than trusted: `scripts/state-numbers.py --check` runs in CI and fails when
any stated figure disagrees with the source it claims to come from. It was written because the
rule had been true and unpoliced, and three of these numbers were stale simultaneously during one
release cut.
