# Spec index — every design record, and where it stands

**Generated 2026-08-15 against 3.18.1; refreshed 2026-08-18 for the GraphIR programme.** 62 top-level specs, 6 archived, 10 build documents.

**How to read the Verified column — this matters.** Status is **self-reported by each spec**
unless marked ✔. This index was built after three separate cases in one session where a status
line contradicted the shipped code:

- `gap3-media-ingestion-roadmap.md` read *"Not started"* — it shipped in **3.10.0**, all phases.
- `gap-roadmap-index.md` read *"Not started"* for **G5** — its own spec said `✅ COMPLETE`.
- The G1–G17 scorecard in [`README.md`](README.md) scored RBAC/multi-tenancy at **10%** — both
  are **implemented and enforced** (`Principal`, `has_role`, cross-tenant 404).

Treat an unverified status as a claim, not a fact. ✔ means checked against source this session.

**Companions:** [`README.md`](README.md) is the narrative programme doc (its G1–G17 percentages are
stamped 2026-06-11 and are stale). [`gap-roadmap-index.md`](gap-roadmap-index.md) indexes the
Graphify-gap series only. This file is the complete inventory.

---

> **Start with [STATE-OF-SPINE.md](STATE-OF-SPINE.md)** — one page, verified 2026-08-18 against
> 3.19.0, covering where the product stands, what is measured, the active programme, and what is
> outstanding. Come here for the per-spec inventory.

---

## ✅ Complete — shipped

| Spec | Shipped in | Verified |
|---|---|---|
| [gap2-document-modality-roadmap](gap2-document-modality-roadmap.md) | 3.8.2 — HTML + Office ingestion | |
| [gap3-media-ingestion-roadmap](gap3-media-ingestion-roadmap.md) | 3.10.0 — all 3 phases (OCR, ASR, reader seam) | **✔** |
| [gap5-visualization-roadmap](gap5-visualization-roadmap.md) | 3.11.0 — Phases 1–2; **Phase 3 deliberately dropped** (Gephi does it better on our export) | **✔** |
| [pkg-accuracy-roadmap](pkg-accuracy-roadmap.md) | Phases 1–7 — corpus, scoreboard, 4 oracles, CI gate | **✔** |
| [python-frontend-parity](python-frontend-parity.md) | All 3 phases — `Endpoint`/`EXPOSES` | |
| [build-document](build-document.md) | All 6 phases — `sdlc plan` · `sdlc approve` | **✔** |
| [pkg-navigable-reports](pkg-navigable-reports.md) | All 4 phases | |
| [bet2c-in-loop-approval](bet2c-in-loop-approval.md) | Implemented — in-loop approval gate | |
| [bet2c-rbac-multitenancy](bet2c-rbac-multitenancy.md) | Implemented **as-built** — `Principal`, roles, tenant scoping | **✔** |
| [doc-ingestion-spec](doc-ingestion-spec.md) | Phases 1–3 — `Doc` + `MENTIONS` + PDF + drift | **✔** |
| [shareable-report-spec](shareable-report-spec.md) | Phases 1–3 | |
| [comprehension-skill-spec](comprehension-skill-spec.md) | Phases 1–3 | |
| [live-observability-otel](live-observability-otel.md) | Phases 1–3 — OTel tracing end-to-end | **✔** |
| [sandboxed-test-execution](sandboxed-test-execution.md) | 1.4.0 — `VenvTestEnvironment` | |
| [sdlc-target-layout-scaffold](sdlc-target-layout-scaffold.md) | 1.1.0 — `layout.py`, `scaffold.py` | |
| [intake-backlog-progress](intake-backlog-progress.md) | 1.3.0 — decisions locked | |

## 🟡 Partial — shipped in part, remainder outstanding

| Spec | Done | Outstanding |
|---|---|---|
| [graphir-sdlc-workflow](graphir-sdlc-workflow.md) | **Phases 1 + 2a (2026-08-18)** — `tool` nodes, Evidence, criteria binding, typed Case; both gates passed (20 runs / 5 commits each). All four research defects closed | **2b** the promotion itself (paid A/B; its validator is in and enforcing) · **3** profiles · **4** parallelism |
| [capability-recommendations-kg-grounded](capability-recommendations-kg-grounded.md) | C1–C6, C8–C10 (9 of 10) | **C7** — observability→defect |
| [sql-support-roadmap](sql-support-roadmap.md) | Track A complete, released 2.7.0 | **Track B** — greenfield SQL codegen |
| [go-support-roadmap](go-support-roadmap.md) | Phase 4.1 comprehension **done**; Go ships in 3.18.1 | Later phases; branch `feat/go-support` |
| [java-codegen](java-codegen.md) | 2a (1.8.0), 2b + 2c (1.9.0) | Remaining slices |
| [typescript-codegen](typescript-codegen.md) | Slice 1 comprehension (1.11.0) | Codegen slices |
| [multi-language-java](multi-language-java.md) | Slice 1 comprehension (1.7.0) | Superseded in part by `java-codegen` |
| [unified-ui](unified-ui.md) | P0–P3 — the buildable UI | P4 TUI, P5 SPA (**optional**) |
| [cross-run-semantic-memory](cross-run-semantic-memory.md) | Phase 1 read path, `MemoryRow` | Write/distil path |
| [project-comprehension-memory-bank](project-comprehension-memory-bank.md) | Phases 1 + 4 (1.5.0, 1.6.0) | Phases 2–3 |
| [persona-skill-measurement](persona-skill-measurement.md) | P0–P3 implemented | The P2 A/B `--live` run |

## 📋 Outstanding — not started or proposal only

| Spec | State | Note |
|---|---|---|
| [gap6-benchmarks-roadmap](gap6-benchmarks-roadmap.md) | Not started | **Rewritten 2026-08-15** against 3.18.1 — reuse the scoreboard, don't rebuild |
| [codegen-benchmark-roadmap](codegen-benchmark-roadmap.md) | Not started | **New 2026-08-15.** SWE-bench comparability → `resolved`-vs-`mergeable` delta |
| [codex-plugin-keyless-roadmap](codex-plugin-keyless-roadmap.md) | Not started | **New 2026-08-15.** Phase 0 is a blocking spike |
| [gap4-adoption-distribution-roadmap](gap4-adoption-distribution-roadmap.md) | Not started | No prerequisites (Phase 3 excepted) |
| [phase5-agentic-codegen-loop](phase5-agentic-codegen-loop.md) | Proposed, under review | "The hinge phase" |
| [autonomous-run-agent](autonomous-run-agent.md) | Proposed, no branch | 9 phases; 0–3 strictly serial |
| [catalog-then-compose-roadmap](catalog-then-compose-roadmap.md) | Proposed, under review | Scope confirmed: Phases 0–3 |
| [standout-evals-and-personas](standout-evals-and-personas.md) | Proposed, under review | Strategy bets 1 & 2 |
| [persona-skill-system](persona-skill-system.md) | Proposed, spec only | |
| [conversational-source-access](conversational-source-access.md) | Proposed, not started | |
| [pkg-code-grounded-understanding](pkg-code-grounded-understanding.md) | Proposed, for review | |
| [language-support-roadmap](language-support-roadmap.md) | Design/blueprint, not started | Three new front-ends |
| [language-expansion-roadmap](language-expansion-roadmap.md) | Prioritization only | Go ✅ · Rust · Kotlin · Ruby |
| [tri-repo-integration](tri-repo-integration.md) | Design only | Spans ontomesh + infodrift |
| [ontomesh-integration-analysis](ontomesh-integration-analysis.md) | Analysis for decision | Nothing built |
| **watch-items** | ⚠️ **Spec missing** | `watch-items-roadmap.md` does not exist in `docs/specs/` or `archive/`; the index linked it as a live track | **✔** |

## ⚠️ Status line contradicts reality

| Spec | Says | Actually |
|---|---|---|
| [current-state.md](current-state.md) | "Design / proposed (build **after** the PKG)" | **`orchestrator state` shipped long ago** — two lenses, HTML report, doc-drift section. Status never updated | **✔** |

## 📖 Reference — not work items

Analysis, comparisons, test plans and assets. No completion state applies.

| Doc | Kind |
|---|---|
| [README](README.md) | Programme narrative + G1–G17 scorecard — **percentages stamped 2026-06-11, stale** |
| [gap-roadmap-index](gap-roadmap-index.md) | Index of the Graphify-gap series |
| [knowledge-graph-architecture](knowledge-graph-architecture.md) | Descriptive, derived from code 2026-08-02 |
| [KNOWLEDGE-VISION](KNOWLEDGE-VISION.md) · [PRODUCT-KNOWLEDGE-GRAPH](PRODUCT-KNOWLEDGE-GRAPH.md) | Vision / concept |
| [competitive-landscape](competitive-landscape.md) | Competitive analysis (2026-08-15) |
| [graphify-vs-spine-comparison](graphify-vs-spine-comparison.md) | One competitor in depth — **at v3.7.0, stale** |
| [cli-test-plan](cli-test-plan.md) | 50 commands, written against 3.16.0 |
| [pkg-accuracy-test-plan](pkg-accuracy-test-plan.md) · [comprehension-test-plan](comprehension-test-plan.md) | Manual test plans |
| [pkg-accuracy-gaps](pkg-accuracy-gaps.md) | Review of the accuracy programme |
| [models](models.md) · [spine-vignette-runbook](spine-vignette-runbook.md) | Reference / runbook |
| [build-document](build-document.md) *(also above)* | The 12-section template |
| [sdlc-tracking-blueprint](sdlc-tracking-blueprint.md) · [design-and-comprehension-milestones](design-and-comprehension-milestones.md) | Blueprints |
| [bet2-trust-spine](bet2-trust-spine.md) | Strategy bet framing |
| [executive-brief-plan-before-code](executive-brief-plan-before-code.md) | Leadership brief |
| [engineering-memory-diagram-prompt](engineering-memory-diagram-prompt.md) · [knowledge-foundation-diagram-prompt](knowledge-foundation-diagram-prompt.md) | Diagram assets |
| `archive/` (6) · `build-documents/` (10) | Superseded records · per-ticket build docs |

---

## Tally

| State | Count |
|---|---|
| ✅ Complete | 16 |
| 🟡 Partial | 11 |
| 📋 Outstanding | 16 *(incl. 1 missing spec)* |
| ⚠️ Stale status | 1 |
| 📖 Reference | 18 |

**The programme is further along than its own paperwork says.** Every discrepancy found this
session ran the same direction — work shipped, the status line didn't move. Nothing was claimed
as done that wasn't.

**Worth fixing at the source:** `pkg accuracy --check` gates graph accuracy and `understand --check`
gates the knowledge base, but nothing gates whether a spec's status line matches shipped reality.
That is the same class of drift `pkg docs` was built to catch for code claims — pointing it at
`docs/specs/` status lines would close it.
