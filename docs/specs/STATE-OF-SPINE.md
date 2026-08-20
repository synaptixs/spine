# State of Spine — 3.21.0

**The one document to read.** Verified against source on **2026-08-20**, at the 3.21.0 release
cut. Every number below was re-measured that day.

> **Why this exists.** There are **70 specs**, 6 archived, and 17 root-level user documents.
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
| Version | **3.21.0** | cutting now; 3.20.0 is the last on PyPI until this ships |
| Languages extracted | **8** front-ends | Python, Java, TypeScript, C#, C, C++, Go, SQL |
| CLI commands | **54** | `grep -c '\.command(' src/orchestrator/cli.py` |
| Source modules | **319** | `find src/orchestrator -name '*.py'` |
| Test functions | **2,569** across 293 files | `grep -rh '^def test_\|^async def test_' tests` |
| Graph precision | **1.00** on every node and edge kind, all 8 front-ends | `orchestrator pkg accuracy` against a hand-labelled corpus |
| `CALLS` recall | **1.00** (C, SQL) → **0.50** (TypeScript) | same |
| Grounding effect, `create` tickets | **29/50 grounded, 0/50 ungrounded** | 200-run controlled A/B, 2 frontier models, 5 passes |
| Same, across two codebases | **47/68 vs 3/68** | replicated on an unrelated external repo |
| Control (`edit` tickets, target file named) | **122/124 either arm** | rules out a generic more-context effect |
| SWE-bench | **no number — none has been run** | ❌ means absent, not low |

**The one claim worth repeating:** every new module that integrated correctly came from a grounded
run. The graph pays exactly where the model cannot see the target, and ties where it can.

## 3. The delivery pipeline as it actually runs

`sdlc autorun` is six stages. **`async` does not mean "calls a model" — four are `async`, three
call a model.**

| Stage | Model? | What it does |
|---|---|---|
| `intake` | **yes** | source document → one spec |
| `investigate` | no | PKG query → landing symbols with `file:line` |
| `validity` | no | judges the ticket against the graph; **the only stage that can stop a run before code is written** |
| `design` | **no** — `produce_design(..., llm=None)` | deterministic by decision, not by default: measured 2026-08-19 and declined |
| `implement` | **yes** | codegen + tests + refine |
| `review` | **yes** | review the worktree diff, fix findings, re-test |

Each model output has a deterministic validator downstream — intake's spec by `assess()`,
implement's code by tests + preflight baseline-diff + fit, review's fixes by re-running the tests.
**`design` has none**, which is safe only while it calls no model. That seam is the subject of §6.

**Since Phase 2a every run also writes `evidence.md` / `evidence.json` / `criteria.md` / `design-references.md` / `case.json`**
beside its brief. `validity` judges the ticket against that Evidence, `design` is handed its blast
radius rather than computing one, and every acceptance criterion is bound to a `file:line` or
refuses the ticket. Two verdicts are new and both can park a run that previously built: an
unbound criterion, and a design naming a place the repository does not have.

## 4. Where Spine is genuinely ahead, and where it is not

**Ahead — 11 rows in the capability matrix nobody else fills.** The strongest four are not about
features: published precision/recall of its own graph, a published controlled A/B for the context
layer, replication on an external codebase, and held-out grading. Every published benchmark found
in the code-intelligence category measures *efficiency* ("70% fewer tokens"), which answers *how
cheap*, not *is it right*.

**Behind, and not disputed:** language breadth (8 against 21–40 for the graph-only tools) and
adoption by orders of magnitude.

**Genuinely absent:** a SWE-bench number, an in-path security verifier, a secrets vault beyond
`.env`. RBAC is **🟡** — identity and tenancy are enforced everywhere (16 of 23 route modules, 23
tenant-scoping sites, cross-tenant reads return 404), but the role check `has_role` is called at
**exactly one site**, the approval decision.

## 5. How Spine is adopted, without entering anyone's build image

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

## 6. The active programme — GraphIR as the SDLC workflow

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
| 4 | Parallel fan-out + bounded replan | none | Not started |

**Phase 2a shipped 2026-08-18 — every defect in the programme is now closed.** Evidence drives
the run: `validity` judges the ticket against it, `design` is handed a blast radius keyed off
where the ticket lands instead of computing one from its own guess, the landing facts keep their
symbols instead of collapsing to filenames, and each acceptance criterion is bound to a
`file:line` or refused. Gate: **20 runs / 5 commits, 0 unexplained verdict mismatches**, with 5
new parks — all the one ticket naming a symbol no repository has.

**Only Phase 4 remains** — parallel fan-out and the bounded replan loop, which is throughput
rather than capability.

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

## 7. Outstanding, everything else

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
| G4 adoption — friction audit | ✅ **Phase 1 done 2026-08-19** — ≈28s cold start, no key; channels/proof/measurement outstanding |
| `episteme.yml` main-branch path produces orphan branches | ✅ **fixed 2026-08-19** — regeneration is `develop`-only; `main` inherits the bank verbatim |

## 8. The failure mode this project keeps having

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
