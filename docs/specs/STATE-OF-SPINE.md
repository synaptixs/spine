# State of Spine — 3.21.0

**The one document to read.** Verified against source on **2026-08-23**, with 3.21.0 released.
Every number below was re-measured that day.

> **Why this exists.** `docs/specs/` holds **70** markdown files — **67 specs** plus this
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
| Version | **3.21.0**, released | merged to `main` (PR #220) |
| Languages extracted | **8** front-ends | Python, Java, TypeScript, C#, C, C++, Go, SQL |
| CLI commands | **54** | `grep -c '\.command(' src/orchestrator/cli.py` |
| Source modules | **319** | `find src/orchestrator -name '*.py'` |
| Test functions | **2,574** across 294 files | `grep -rh '^def test_\|^async def test_' tests` |
| Graph precision | **1.00** on every node and edge kind, all 8 front-ends | `orchestrator pkg accuracy` against a hand-labelled corpus |
| `CALLS` recall | **1.00** (C, SQL) → **0.50** (TypeScript) | same |
| Grounding effect, `create` tickets | **29/50 grounded, 0/50 ungrounded** | 200-run controlled A/B, 2 frontier models, 5 passes |
| Same, across two codebases | **47/68 vs 3/68** | replicated on an unrelated external repo |
| Control (`edit` tickets, target file named) | **122/124 either arm** | rules out a generic more-context effect |
| SWE-bench | **no number — none has been run** | ❌ means absent, not low |

**The one claim worth repeating:** every new module that integrated correctly came from a grounded
run. The graph pays exactly where the model cannot see the target, and ties where it can.

## 3. The PKG — what it is, how it is built, and why any of it matters

*For engineers. The narrative version is [`KNOWLEDGE_GRAPH.md`](../../KNOWLEDGE_GRAPH.md); the
full front-end detail is [`parsing-and-the-pkg.md`](parsing-and-the-pkg.md).*

Everything else on this page rests on one artifact. The **Program Knowledge Graph** is a typed
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
| `CALLS` recall | 1.00 (c, sql) · 0.73 (python) · 0.67 (cpp, csharp, go, java) · **0.50 (typescript)** |
| Invention | **0** on this repo across **15,982** `CALLS`; **47** across 23,746 bare calls on 11 public repos in TypeScript/Go/C++/C# (2026-08-24) |

**That precision row is a corpus score, and the corpus is missing a shape.** When a parameter
or local **shadows** a resolvable name, TypeScript, Go, C++ and C# each emit a `CALLS` edge to
the file-level definition the caller never reaches — the bug Python fixed in 3.18.0, in a form
the fix did not reach. C already refuses it (`c_extractor._bound_names`) and has a corpus case;
the other four have neither. Nothing caught it: the target is a real node so `pkg verify` sees
no dangling edge, no fixture carries the shape so precision reads 1.00, and the invention
oracle was Python-only until 2026-08-24. Measured rate on real code: **0.47% of bare calls in
C++, 0.02% in TypeScript, 0 in Go and C# on this sample** — real, and roughly an order of
magnitude rarer than Python's 3.16% was. Full record, with pinned SHAs and the reproducible
command: [`invention-oracle-cross-language.md`](invention-oracle-cross-language.md).

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
is allowed to sit at 0.50 on TypeScript instead of being padded.

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

**5. It is cheap enough to be unconditional.** A full extraction of this repository — **11,609
nodes, 33,684 edges** across 319 modules — takes **~2.2s** cold, and is cached per commit after
that. Nothing in the pipeline has to ration it.

### Where it is honestly weak

- **`CALLS` recall on TypeScript is 0.50** — half the call edges are missed. Structure is
  complete; the call graph is not.
- **`runtime` is still Python-only** (PEP 669). On a non-Python repo it reports nothing, and
  that is *not measured*, not clean. `invention` was the same until 2026-08-24 and now walks
  six front-ends, naming Java and SQL as not-applicable with reasons rather than scoring them
  0 — but the shadowing defect it found in four of them is **not yet fixed**.
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

| Item | State |
|---|---|
| Upgrade local `uv` past 0.8.0 | user's machine |
| RBAC role-gating beyond the approval decision + secrets vault | parked |
| CI gate on spec-status drift | not started |
| Decide `--intents` — shipped with no reader, no export | undecided |
| Rust front-end | not started |
| Express endpoint extraction (TypeScript) | unscheduled |
| Deployment image + reusable CI workflow for central adoption | not started. **Not a G4 phase** — it was recommended as though it were; it needs adding to that spec before it can be scheduled |
| `SPEC-INDEX.md` links `watch-items-roadmap.md`, which does not exist | not started |
| `docs/specs/current-state.md` has a mermaid block that falls back to `<pre>` in our own UI | not started |
| Shadowed-name `CALLS` invention in TypeScript, Go, C++, C# | **measured 2026-08-24, not fixed.** The oracle now sees it across six front-ends (Phase 1); porting C's `_bound_names` to the other four, adding a `shadowed_calls` corpus case each, and moving `invention` to a `strict`-at-zero gate are Phases 2–4. C++ first — it holds 46 of the 47 findings. [Record](invention-oracle-cross-language.md) |
| TypeScript resolution via the TS compiler API | **idea, not scheduled** (2026-08-21). `CALLS` recall is **0.50** for TypeScript against 1.00 for C and SQL — tree-sitter parses the syntax correctly but has no symbol table, so a call site resolves to the wrong target or none. The language's own toolchain would fix that. Costs: a Node runtime dependency, loss of tree-sitter's error tolerance, and a determinism risk if resolution depends on installed packages — the same commit could then give different graphs on different machines |
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
here without a "how it is known" is a number that should not be here.
