# Spec index — every design record, and where it stands

**Generated 2026-08-15 against 3.18.1; refreshed 2026-08-21 for the completed GraphIR
programme; recounted 2026-09-02 at 3.29.0.** `docs/specs/` holds **85** markdown files —
**78 specs** plus three navigation documents ([README](README.md), this index,
[STATE-OF-SPINE](STATE-OF-SPINE.md)) — with 6 archived and 10 build documents. The count read
*63* until 2026-08-21, and **five specs were not listed at all**, including this file's own
companion matrix and both measurements it cites as evidence. An inventory that silently omits
things is the failure it was built to catch, so the count is now stated as a derivation anyone
can re-run:

```
ls docs/specs/*.md | wc -l          # 85
```

**It rotted anyway.** The line read *70* from 2026-08-21 until 2026-08-28 while the directory
grew to 76 — six specs listed in the table below but not in the count above it. A derivation a
reader *can* re-run is not a derivation anything *does* re-run, and nothing here fails when the
two disagree. [STATE-OF-SPINE](STATE-OF-SPINE.md) §8 tracks the missing gate ("CI gate on
spec-status drift"); until it exists, re-run the command above when adding a spec.

**How to read the Verified column — this matters.** Status is **self-reported by each spec**
unless marked ✔. This index was built after three separate cases in one session where a status
line contradicted the shipped code:

- `gap3-media-ingestion-roadmap.md` read *"Not started"* — it shipped in **3.10.0**, all phases.
- `gap-roadmap-index.md` read *"Not started"* for **G5** — its own spec said `✅ COMPLETE`.
- The G1–G17 scorecard in [`README.md`](README.md) scored RBAC/multi-tenancy at **10%**.
  **Multi-tenancy is implemented and enforced** (`Principal`, cross-tenant reads return 404).
  **RBAC is not** — this line said "both" until 2026-08-21, which was the correction
  overshooting: `has_role` is called at exactly one site, the approval decision. See
  [`capability-matrix.md`](capability-matrix.md) footnote ⁶, where the same cell is on record as
  corrected twice.

Treat an unverified status as a claim, not a fact. ✔ means checked against source this session.

**Companions:** [`README.md`](README.md) is the narrative programme doc (its G1–G17 percentages are
stamped 2026-06-11 and are stale). [`gap-roadmap-index.md`](gap-roadmap-index.md) indexes the
Graphify-gap series only. This file is the complete inventory.

---

> **Start with [STATE-OF-SPINE.md](STATE-OF-SPINE.md)** — one page, verified 2026-09-01 against
> 3.29.0, covering where the product stands, what is measured, the active programme, and what is
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
| [multi-repo-roadmap](multi-repo-roadmap.md) | **3.23.0** — all four phases: `.spine/repos.yaml`, three joiners, one scoped merged graph. Multi-repo *delivery* remains an explicit non-goal. **Was filed under 📋 "not started or proposal only" until 2026-08-30** while `pkg/repos.py`, `pkg/join_link.py` and `pkg/joins_propose.py` were in source — the §9 failure mode, found by re-reading the row against the code | **✔** |
| [shareable-report-spec](shareable-report-spec.md) | Phases 1–3 | |
| [comprehension-skill-spec](comprehension-skill-spec.md) | Phases 1–3 | |
| [live-observability-otel](live-observability-otel.md) | Phases 1–3 — OTel tracing end-to-end | **✔** |
| [sandboxed-test-execution](sandboxed-test-execution.md) | 1.4.0 — `VenvTestEnvironment` | |
| [sdlc-target-layout-scaffold](sdlc-target-layout-scaffold.md) | 1.1.0 — `layout.py`, `scaffold.py` | |
| [intake-backlog-progress](intake-backlog-progress.md) | 1.3.0 — decisions locked | |

## 🟡 Partial — shipped in part, remainder outstanding

| Spec | Done | Outstanding |
|---|---|---|
| [gap4-adoption-distribution-roadmap](gap4-adoption-distribution-roadmap.md) | **Phase 1 (2026-08-19)** — friction audit: ≈28s cold start, no key, measured; two findings fixed | **2** channels · **3** proof assets · **4** measurement |
| [graphir-sdlc-workflow](graphir-sdlc-workflow.md) | **Phases 1–3 (2026-08-18/19)** — `tool` nodes, Evidence, criteria binding, typed Case, design validator, issue-type profiles. All four research defects closed | **Phase 4 closed unshipped (2026-08-23)** — fan-out worth ~30ms, replan built then reverted as unreachable; reopen only if `design` is promoted |
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
| [watch-items-roadmap](watch-items-roadmap.md) | **Written 2026-08-30** — the spec three pages linked for a month and nobody had. WI-1 recommended for removal (premise unsourced; both readings already shipped) | **WI-2, all three phases.** Phase 1 is a live defect: `stale_findings` fabricates staleness on every non-Python file, in the PR-review path | **✔** |

## 📋 Outstanding — not started or proposal only

| Spec | State | Note |
|---|---|---|
| [gap6-benchmarks-roadmap](gap6-benchmarks-roadmap.md) | ✅ **Complete 2026-09-01** | All three phases: harness, 38-label gold set, ratchet gate. top-1 0.32 / top-10 0.71 against 0.085 chance; published as [BENCHMARK.md](../../BENCHMARK.md). This row read *Not started* for a day after the spec said COMPLETE |
| [codegen-benchmark-roadmap](codegen-benchmark-roadmap.md) | Not started | **New 2026-08-15.** SWE-bench comparability → `resolved`-vs-`mergeable` delta |
| [codex-plugin-keyless-roadmap](codex-plugin-keyless-roadmap.md) | Not started | **New 2026-08-15.** Phase 0 is a blocking spike |
| [phase5-agentic-codegen-loop](phase5-agentic-codegen-loop.md) | Proposed, under review | "The hinge phase" |
| [autonomous-run-agent](autonomous-run-agent.md) | Proposed, no branch | 9 phases; 0–3 strictly serial |
| [catalog-then-compose-roadmap](catalog-then-compose-roadmap.md) | Proposed, under review | Scope confirmed: Phases 0–3 |
| [standout-evals-and-personas](standout-evals-and-personas.md) | Proposed, under review | Strategy bets 1 & 2 |
| [persona-skill-system](persona-skill-system.md) | Proposed, spec only | |
| [conversational-source-access](conversational-source-access.md) | Proposed, not started | |
| [recorded-intent-tier](recorded-intent-tier.md) | **Written 2026-09-01** — producer shipped, nothing reads it | The design record for `--intents`. Phase 1 is ~2 hours: the export has no `--intents` flag while `understand`/`state` do | **✔** |
| [pkg-code-grounded-understanding](pkg-code-grounded-understanding.md) | Proposed, for review | |
| [language-support-roadmap](language-support-roadmap.md) | Design/blueprint, not started | Three new front-ends |
| [language-expansion-roadmap](language-expansion-roadmap.md) | Prioritization only | Go ✅ · Rust · Kotlin · Ruby |
| [typescript-call-resolution](typescript-call-resolution.md) | **Written 2026-09-01** — scoped, and argues **against** the compiler API | The recall figure was 0.50 in three places and is 0.57; the loss is one call-shape family, four fifths of it reachable locally. Widen the corpus first | **✔** |
| [tri-repo-integration](tri-repo-integration.md) | Design only | Spans ontomesh + infodrift |
| [ontomesh-integration-analysis](ontomesh-integration-analysis.md) | Analysis for decision | Nothing built |

## ⚠️ Status line contradicts reality

| Spec | Says | Actually |
|---|---|---|
| [current-state.md](current-state.md) | "Design / proposed (build **after** the PKG)" | **`orchestrator state` shipped long ago** — two lenses, HTML report, doc-drift section. Status never updated | **✔** |
| [preflight-baseline-diff.md](preflight-baseline-diff.md) | "Proposed — awaiting approval" | **Shipped.** `Baseline`, `capture_baseline` and baseline mode are in `sdlc/preflight.py` with 11 tests in `tests/sdlc/test_preflight.py`; the capability matrix scores it ✅. Found 2026-08-21 — the spec was not in this index either | **✔** |

## 📖 Reference — not work items

Analysis, comparisons, test plans and assets. No completion state applies.

| Doc | Kind |
|---|---|
| [README](README.md) | Programme narrative + G1–G17 scorecard — **percentages stamped 2026-06-11, stale** |
| [design-promotion-ab-results](design-promotion-ab-results.md) | The 100-run measurement behind Phase 2b's **declined** design promotion (2026-08-19) |
| [gap-roadmap-index](gap-roadmap-index.md) | Index of the Graphify-gap series |
| [knowledge-graph-architecture](knowledge-graph-architecture.md) | Descriptive, derived from code 2026-08-02 |
| [KNOWLEDGE-VISION](KNOWLEDGE-VISION.md) · [PRODUCT-KNOWLEDGE-GRAPH](PRODUCT-KNOWLEDGE-GRAPH.md) | Vision / concept |
| [capability-matrix](capability-matrix.md) | **The** capability matrix — 47 scored rows, 22 of them rows no competitor's public docs fill. Counts checked by `scripts/matrix-count.py --check` (2026-08-21) |
| [codegen-model-comparison-results](codegen-model-comparison-results.md) · [external-repo-grounding-results](external-repo-grounding-results.md) | The two measurements behind the matrix's ⁴ rows — 200 and 60 ticket-runs (2026-08-16) |
| [parsing-and-the-pkg](parsing-and-the-pkg.md) | How source becomes graph facts, front-end by front-end (2026-08-16) |
| [document-ingestion-reference](document-ingestion-reference.md) | How *prose* becomes graph facts, format by format — the five stages, per-format behaviour, bounds, and the measured cost of losing headings (2026-08-29) |
| [multi-repo-walkthrough](multi-repo-walkthrough.md) | What happens when you point Spine at three repositories: declare, extract, merge with scoped ids, join across the boundary, read. The mechanism at two pages, beside the roadmap's design record (2026-09-01) |
| [secrets-vault-and-identity](secrets-vault-and-identity.md) | Scoped, not built | Separates what §8 bundled: **RBAC is built and already opt-in**; the secrets seam does not exist at all. One `get_secret` whose default is today's env vars, a vault behind an extra, and the read-path invariant made testable (2026-09-02) |
| [endpoints-typescript-go](endpoints-typescript-go.md) | Scoped, not built | Only Java and C# emit `Endpoint` among the tree-sitter front-ends, so a Node or Go service **cannot be a provider in a cross-repo join** — and `impact_of` calls every Go/TS route handler safe to refactor. Mirrors `python_routes.py` exactly (2026-09-02) |
| [doc-file-binding](doc-file-binding.md) | The first deterministic move against the 55% of `Doc` sections that bind to nothing: a cited path owns exactly one `Module`, and the edge was being discarded. Measures what it reaches — 108 sections — and, more usefully, what nothing deterministic reaches (2026-09-01) |
| [doc-binding-walkthrough](doc-binding-walkthrough.md) | How prose becomes a `MENTIONS` edge, in six steps and then traced through one real page with the actual anchor counts — the mechanism, not the format-by-format reference beside it (2026-09-01) |
| [ticket-to-landing-sites](ticket-to-landing-sites.md) | How a ticket's words become `file:line` landing sites — tokenising, scoring, and what the result is bound to (2026-08-25) |
| [competitive-landscape](competitive-landscape.md) | Competitive **narrative and strategy** (2026-08-21). Its duplicate scored matrix was removed — see `capability-matrix.md` |
| [graphify-vs-spine-comparison](graphify-vs-spine-comparison.md) | One competitor in depth — **at v3.7.0, stale** |
| [spec-kit-integration-analysis](spec-kit-integration-analysis.md) | GitHub's spec-kit — **evaluated and declined 2026-08-25**. LLM-driven end to end, no validator, never reads the target codebase. Carries a revisit condition |
| [enhancement-index](enhancement-index.md) | Directory of the enhancements planned in the 2026-08-25 session — E1–E6, with state and what gates each. **Not a third backlog**; `STATE-OF-SPINE` §8 remains the authority |
| [constitution-roadmap](constitution-roadmap.md) | Durable project rules a run is *checked* against, not prompted with. **Spec only — Phase 0 is a blocking trigger probe** that may close it unbuilt |
| [cli-test-plan](cli-test-plan.md) | 50 commands, written against 3.16.0 |
| [pkg-accuracy-test-plan](pkg-accuracy-test-plan.md) · [comprehension-test-plan](comprehension-test-plan.md) | Manual test plans |
| [pkg-accuracy-gaps](pkg-accuracy-gaps.md) | Review of the accuracy programme |
| [invention-oracle-cross-language](invention-oracle-cross-language.md) | The invention oracle across 8 front-ends (2026-08-24) — 47 fabricated `CALLS` edges found on 11 pinned public repos, four front-ends fixed, gated | **✔** |
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
| ✅ Complete | 17 |
| 🟡 Partial | 13 |
| 📋 Outstanding | 15 |
| ⚠️ Stale status | 2 |
| 📖 Reference | 25 |

> **These five numbers have no derivation.** Unlike the file count above them, nothing re-runs
> them and they do not reconcile to the 74 specs in the header — reference rows double-count
> specs listed above and fold `archive/` and `build-documents/` into single lines. Treat them as
> a shape, not an inventory; the "CI gate on spec-status drift" in
> [STATE-OF-SPINE](STATE-OF-SPINE.md) §8 is what would make them checkable.

**The programme is further along than its own paperwork says.** Every discrepancy found this
session ran the same direction — work shipped, the status line didn't move. Nothing was claimed
as done that wasn't.

**Worth fixing at the source:** `pkg accuracy --check` gates graph accuracy and `understand --check`
gates the knowledge base, but nothing gates whether a spec's status line matches shipped reality.
That is the same class of drift `pkg docs` was built to catch for code claims — pointing it at
`docs/specs/` status lines would close it.
