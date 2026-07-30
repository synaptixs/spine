# AI-Native Software Platform — Development Plan

**Status:** Proposed roadmap · **Horizon:** ~12 months to a credible v1 of the
full vision, with shippable value every quarter.

This plan takes the current orchestrator from "governed pipeline with a stubbed
engine room" to a **generic platform for building AI-native software** whose
flagship application is an **AI-native SDLC** — a system that takes a product
idea from requirements to production with humans intervening only at critical
decision points.

---

## 1. Vision

Two products, one substrate:

1. **The platform** — a domain-agnostic engine for *AI-native software*: any
   workflow where LLM agents plan, act, and are verified, with durable
   execution, human gates, and an audit trail. SDLC is the first vertical; the
   same substrate runs research, ops automation, compliance workflows, etc.

2. **The AI-native SDLC** — the flagship vertical: Confluence/Jira/GitHub →
   intents → specs → code → tests → review → deploy → prod, autonomous between
   two human bookends (intent approval, prod approval), every LLM decision
   grounded and audited.

**The thesis:** most autonomous-SDLC attempts build the *generative* layer and
bolt on governance. We built the *governance + orchestration* layer first
(the part that makes autonomy safe) and now build generative depth on top —
grounded in a model of the existing system so the agents engineer *within* a
codebase, not in a vacuum.

---

## 2. Principles (non-negotiable)

1. **Everything grounded, everything verified.** Every agent output is a Claim
   with Evidence, checked by a verifier. This already governs task outputs; we
   extend it to *system knowledge* (the knowledge graph) and *generated code*.
2. **Derived, not authored.** Knowledge about the system (graph, ontology,
   conventions) is *extracted* from source + docs with provenance — a build
   artifact, never a hand-maintained parallel truth that rots.
3. **Adapters at every external seam.** Source, tracker, VCS, CI, deploy, LLM —
   all behind Protocols. Bring-your-own-stack is an adoption lever, not a fork.
4. **Read-only / dry-run by default.** Writes are gated; a misfire can't litter
   a real system. Trust is earned, then granted.
5. **Wedge-first.** Ship a narrow useful slice, validate on a real repo, widen.
   No 6-month dark tunnels.
6. **Humans at the bookends, rules in the middle.** Autonomy is the default
   between gates; humans decide what's genuinely theirs to decide.

---

## 3. Current capabilities (grounded inventory)

### Platform substrate — ✅ built, ~501 tests
- **Planner + IR + validator** — objective → typed workflow graph (GraphIR),
  structurally validated; multi-pattern (single / sequential / manager-specialists).
- **Verifier chain** — Schema, Confidence, Evidence (deterministic spot-check),
  Policy (PII/classification), Glossary. Per-edge, composable, audited.
- **Replan loop** — on verifier failure, the planner revises the IR and retries
  within a budget.
- **Temporal durability** — workflows survive worker restarts; activities for
  every side effect; signals; per-activity retry/timeout.
- **Approval gates** — `ApprovalRequest` persistence, REST decide API
  (approve/reject/modify_input), timeout sweep, audit-chained decisions.
- **Audit log** — append-only, one row per LLM call / tool call / decision /
  approval; trace UI.
- **Registry + glossary + calibration** — versioned agent templates + tool
  contracts; pinned glossary (a proto-ontology); confidence-calibration history.
- **Gateway + sandboxed tools** — MCP tool contracts; sandboxed code execution.
- **Storage** — Postgres + S3/MinIO artifact-by-reference.

### Vertical: AI-native SDLC
- **Block A — PR Reviewer (GitHub App)** ✅ live-verified. Webhook → App auth →
  diff fetch → LLM review + Secrets/Security/Style verifiers → posted review.
- **Block B — Requirements → Backlog** ✅ live-verified. Confluence adapter
  (read + create_page), intent extraction, gap analysis (YAML rules), spec
  writing, Jira adapter (first-class dry-run), `orchestrator ingest` CLI.
  Proven against a real space (110 intents extracted + uploaded; AEO backlog
  previewed).
- **Block C — SDLC skeleton (Temporal)** ✅ orchestration real, delivery
  stubbed. **Real:** per-issue git worktrees, `pytest` subprocess runner, the
  test→refine loop, the parent+child fan-out workflow, both approval gates,
  audit. **Stubbed behind Protocols (Block D fills in):** codegen (hardcoded
  module+test), CI (always-pass), deploy (synthetic revision), PR (synthetic URL).

**Net:** the control plane and the orchestration spine are real and proven.
The generative + delivery muscle is stubbed-but-seamed, and there is no model
of the *existing system* yet. Those two are the heart of this plan.

---

## 4. Gap taxonomy

| # | Gap | Bucket | Severity |
|---|---|---|---|
| G1 | No model of the existing system (code + docs) | Comprehension | **Critical** |
| G2 | Code generation that reliably compiles/passes/fits | Generative | **Critical** |
| G3 | Iterative refinement quality (test→patch→retest at depth) | Generative | **Critical** |
| G4 | Semantic verification (correctness, security, perf, drift) | Trust | High |
| G5 | Real CI + deploy + E2E + rollback | Delivery | High |
| G6 | Multi-language / multi-stack support | Generality | High |
| G7 | Adapter breadth (Notion/Linear/GitLab/ADO/Jenkins/Argo…) | Generality | Medium |
| G8 | Repo-convention learning (branching, CODEOWNERS, monorepo) | Generality | Medium |
| G9 | Cost/budget governance (enforced, not modeled) | Control | High |
| G10 | Confidence-calibrated escalation (know when unsure) | Control | High |
| G11 | Multi-tenancy + RBAC | Adoption | High |
| G12 | Console UI for gates + runs (non-engineer approvers) | Adoption | High |
| G13 | Notifications (Slack/email/webpush) | Adoption | Medium |
| G14 | Onboarding (`init`: detect stack, wire connectors, first run) | Adoption | Medium |
| G15 | Secrets/vault management beyond `.env` | Adoption/Ops | Medium |
| G16 | Scale, SRE, compliance (SOC 2, audit chaining), OTel | Ops | Medium |
| G17 | Platform SDK / extensibility for non-SDLC verticals | Platformization | Medium |

---

## 5. The roadmap — eight tracks

Tracks run partly in parallel; §6 sequences them. Each track ships in wedges.

### Track 1 — Comprehension Layer: Product Knowledge Graph (PKG) · G1
*The missing substrate. A grounded, queryable model of the existing system.*

- **1.1 Structural extractor (one language: Python).** AST + import graph +
  ORM models (→ data entities) + API routes (→ endpoints) + test↔code links.
  Deterministic, provenance per node (`file:line`). Stored in Postgres (typed
  `nodes`/`edges` tables) + pgvector embeddings.
- **1.2 Doc semantic layer.** Reuse the Confluence/source-doc adapters → extract
  purpose / business rules / glossary, **reconcile against code anchors**.
  Unbound claims = drift findings.
- **1.3 Retrieval API (GraphRAG).** Subgraph + vector retrieval the agents query
  per task. Graph for precise relations, embeddings for fuzzy relevance.
- **1.4 GroundingVerifier + freshness.** A verifier that re-checks edges against
  current source (stale edge = finding); merge-hook incremental re-extraction so
  the graph is a CI artifact, never a one-time crawl.
- **1.5 Schema growth + second language** (TypeScript/Go) once the model proves out.

*Exit:* every SDLC agent can ask "what already exists, what does this touch,
what are the invariants" and get grounded answers. Dogfood on this repo.

### Track 2 — Generative Depth: real code-gen behind the seams · G2, G3
*Drop real LLM implementations into Block C's Protocols; make the loop good.*

- **2.1 LLM `CodegenAdapter`** behind the existing Protocol — plan → implement →
  test-author, **retrieval-augmented from the PKG** (Track 1) so generated code
  fits the codebase.
- **2.2 Iterative refinement at depth** — the real test→read-failure→patch→retest
  inner loop (Block C has the skeleton; make it converge over many cycles with
  failure-aware prompting + the PKG).
- **2.3 Repo-context tools** — symbol lookup, ripgrep, file read/write, LSP-light
  (pyright/tsserver) for import resolution.
- **2.4 Acceptance-rate harness** — labelled benchmark on real repos; track
  % PRs accepted without major edits. This is the north-star metric for autonomy.

*Exit:* on a real repo, ≥70% of small-to-moderate features produce a
mergeable PR with no human edits.

### Track 3 — Verification Depth · G4, G10
*Make autonomy safe to grant by catching what regex + schema can't.*

- **3.1 Semantic-correctness verifier** — does the change satisfy the spec's
  acceptance criteria? (LLM-judge + test evidence, adversarially checked.)
- **3.2 Security scan verifier** — beyond the Block-A regex (ast-grep / Semgrep
  integration) + dependency CVE checks.
- **3.3 Contract/invariant verifier** — does the change break a known
  API/data contract or documented invariant? (Powered by the PKG.)
- **3.4 Coverage + perf-regression verifiers.**
- **3.5 Calibrated escalation** — wire the existing calibration history into a
  policy: low-confidence or high-blast-radius → escalate to human instead of
  proceeding. *Knowing when not to know.*

*Exit:* the verdict that gates autonomy is trustworthy enough that a green run
rarely needs human rescue.

### Track 4 — Real Delivery · G5
*Replace Block C's delivery stubs with real implementations.*

- **4.1 Real PR (`forge`)** — wire to the Block-A GitHub client; worktree branch
  → real PR, auto-review attached.
- **4.2 Real CI (`tool.gha.*`)** — trigger + await GitHub Actions; real
  `IntegrationTestVerifier`.
- **4.3 Real deploy (`DeployAdapter`)** — Cloud Run (`gcloud run deploy`),
  staging→prod, **canary + automatic rollback** on smoke/E2E failure.
- **4.4 Real E2E verifier** — Playwright in a container against the deploy URL.

*Exit:* a real feature goes Confluence → prod through the two gates, on real infra.

### Track 5 — Generality · G6, G7, G8
*Make it work for any stack and any team's tools.*

- **5.1 Language packs** — build/test/lint adapters for Node, Go, Java (each a
  small adapter set: how to install deps, run tests, lint).
- **5.2 Adapter breadth** — Source: Notion, Markdown-in-repo. Tracker: Linear,
  GitHub Issues, ADO. VCS: GitLab. CI: GitLab CI, Jenkins, CircleCI. Deploy:
  k8s/Argo, ECS, Fly.
- **5.3 Convention learning** — derive branching model, commit style, CODEOWNERS,
  monorepo layout from the repo (feeds the PKG) instead of assuming.
- **5.4 Project archetypes** — pipeline templates for API / library / data /
  frontend so the right stages run for the right project shape.

*Exit:* a non-Python team on a non-GitHub stack can adopt with one adapter set.

### Track 6 — Control Plane & Adoption · G11–G15
*Make it adoptable by a team that isn't us.*

- **6.1 Multi-tenancy + RBAC** — `tenant_id` + `team_id` scoping; approver-role
  checks on gates (the `approver_roles` field exists, unenforced).
- **6.2 Console UI** — SPA over the existing REST + trace: run timeline, the
  approval queue, the PKG explorer, audit browser. Non-engineer approvers.
- **6.3 Notifications** — approval requests → Slack/email/webpush (the
  `notification_channels` field exists, delivery doesn't).
- **6.4 Onboarding (`orchestrator init`)** — detect stack, wire connectors,
  validate creds, run a first dry-run. (We hand-wired this in live testing; productize it.)
- **6.5 Secrets** — vault/secret-manager integration per tenant (beyond `.env`).
- **6.6 Cost governance** — enforce `IR.budget` (tokens/$/wall-clock) end-to-end;
  per-tenant quotas; cost-per-run telemetry.

*Exit:* a new team self-serve onboards and runs a feature without our help.

### Track 7 — Operational Hardening · G16
- **7.1 Scale** — worker pools, queue partitioning, concurrent-run limits.
- **7.2 Reliability/SRE** — dead-letter handling, replay tooling, health/readiness.
- **7.3 Observability** — OTel spans through activities → a collector (the
  deferred Sprint-15 item); cost + latency dashboards.
- **7.4 Compliance** — finish cryptographic audit chaining; SOC 2 controls;
  data-residency options.
- **7.5 CI for the platform's own integration tests** — provision Temporal +
  Postgres + MinIO in CI so the currently-skipped E2E tests run on every push.

### Track 8 — Platformization (generic AI-native software) · G17
*Make the substrate reusable beyond SDLC.*

- **8.1 Public SDK** — clean Python (and TS) client for: define an agent
  template, register a tool, compose a workflow, run + await, read the audit.
- **8.2 Extension points** — documented seams for custom verifiers, custom
  patterns, custom adapters; a plugin/registry mechanism.
- **8.3 Reference verticals** — 1–2 non-SDLC examples (e.g. a research workflow,
  a compliance-review workflow) proving the substrate is genuinely generic.
- **8.4 Workflow authoring DX** — templates, local dev loop, dry-run everywhere.

*Exit:* a third party builds a non-SDLC AI-native app on the platform without
touching core.

---

## 6. Sequencing (4 phases, ~12 months)

Phases overlap; the ordering reflects *dependencies* and *value-first*.

**Phase 5 — Comprehension + first real codegen (Q1, ~3 mo).** Track 1 (PKG
wedges 1.1–1.4) + Track 2 (2.1–2.3). *Why first:* G1 unblocks G2/G3; grounded
generation is the only path to useful acceptance rates. Dogfood on this repo.
**Milestone:** a real feature on this repo, PKG-grounded, ≥50% acceptance.

**Phase 6 — Trust + real delivery (Q2, ~3 mo).** Track 3 (verification depth) +
Track 4 (real PR/CI/deploy/E2E) + Track 2.4 (acceptance harness). **Milestone:**
one real feature Confluence→prod through both gates on real infra at ≥70%
acceptance; verdict trustworthy.

**Phase 7 — Generality + adoption (Q3, ~3 mo).** Track 5 (language packs +
adapters) + Track 6 (multi-tenancy, UI, notifications, onboarding, cost). 
**Milestone:** a second team on a second stack self-serve onboards and ships.

**Phase 8 — Hardening + platformization (Q4, ~3 mo).** Track 7 (ops/compliance/
CI) + Track 8 (SDK + reference verticals). **Milestone:** production-grade,
multi-tenant; one non-SDLC vertical proves genericity.

Parallelizable: with 2–3 engineers, Tracks 1 and 2 run together in Phase 5; one
engineer on PKG/verification, one on codegen/delivery, one on adapters/UI.

---

## 7. Trust / maturity ladder

Autonomy is *granted progressively*, per the verification it has earned:

| Rung | What the system does autonomously | Gated by |
|---|---|---|
| 0 Assistive | Review PRs, draft backlog (Blocks A/B today) | — |
| 1 Supervised draft | Generates PRs; humans always merge | Phase 5 |
| 2 Autonomous-for-a-class | Trivial/well-specified changes merge on green | Phase 6 (trustworthy verdict) |
| 3 Autonomous feature | Moderate features Confluence→staging unattended | Phase 6–7 |
| 4 Autonomous to prod | Full pipeline; humans only at intent + prod gates | Phase 7–8 (calibrated escalation + rollback) |

The platform is on **rung 0→1**. Each rung widens only as verification (Track 3)
+ the PKG (Track 1) make it safe.

---

## 8. Success metrics

- **Acceptance rate** — % agent PRs merged without major human edits (north star;
  target 70%+ on moderate features by Phase 6).
- **Human-touch per pipeline run** — trend toward 2 (the bookends).
- **Escalation precision** — when the system escalates, is it right to? (calibration)
- **Drift caught** — doc/code + contract divergences surfaced (PKG value).
- **Time-to-onboard** — a new team/stack to first green run (Phase 7 target: < 1 day).
- **Cost per feature** — $ + tokens, trending down with PKG-grounded generation.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Codegen never reaches useful acceptance | PKG-grounding is the lever; benchmark hard from Phase 5; keep humans on merge until proven |
| PKG rots / drifts | Derived-not-authored + provenance + merge-hook freshness + GroundingVerifier |
| Over-ontologizing the PKG | Small task-driven schema; grow only as queries demand |
| Verification false positives (cf. Block-A secrets noise) | Tune on real repos; calibrated severity; suppress dummy/test patterns |
| Scope sprawl across 8 tracks | Strict phase milestones; ship a real feature each phase |
| Cost blowout in autonomous loops | Enforce IR budgets early (Track 6.6), not late |

---

## 10. Immediate next steps (this quarter)

1. **PKG wedge 1.1** — Python structural extractor (AST + imports + ORM +
   routes + tests) → Postgres + pgvector, provenance per node. Dogfood on this repo.
2. **Codegen 2.1** — LLM `CodegenAdapter` behind Block C's Protocol, grounded by
   the PKG retrieval API.
3. **Acceptance harness 2.4** — labelled benchmark on this repo's own backlog.
4. **Tune Block-A secrets verifier** — the live-testing false-positive cleanup
   (skip test fixtures/dummy values) — small, immediate quality win.

> Build order rationale: comprehension before generation, generation before
> delivery, delivery before breadth, breadth before platformization. Prove each
> on this repo before widening to "any team, any project."

---

*Sits on top of the platform (Sprints 1–14) + SDLC Blocks A–C, all on
`develop`. The Product Knowledge Graph design detail belongs in a companion
`docs/specs/PRODUCT-KNOWLEDGE-GRAPH.md`.*
