# SDLC Orchestrator — Build Plan

**Status:** Proposed · **Target:** Internal-only v1.0 · **Horizon:** 20 weeks
**Built on:** agent-orchestrator platform (Sprints 1–14 complete)

---

## 1. Vision

An automated SDLC pipeline that takes high-level requirements from existing
systems (Confluence) and drives them all the way to production:

```
Confluence (requirements)
   └─► derive intents
   └─► address gaps
   └─► clarify + approve intents (rules + human bookend)
   └─► derive feature/tech specs → create Jira issues
   └─► code generation (iterative)
   └─► unit testing
   └─► integration testing
   └─► deploy to staging
   └─► end-to-end testing
   └─► human approval → production
```

**Audit trail wherever an LLM is used** — already native to the platform; every
LLM call, tool invocation, rule evaluation, and approval writes an audit row.

This is **not** a new platform. It is a **domain layer** (Confluence, Jira, CI,
deploy, rule engine) stitched onto the existing agent-orchestrator primitives.

---

## 2. Why this is the right long-term bet

The orchestrator-as-product compounds across projects in a way that
building a single product never does:

| Project | Build cost | Delivery cost |
|---|---|---|
| #1 (first real backlog) | 20 weeks (platform) | ~4 weeks |
| #2 | 0 weeks | ~3–4 weeks |
| #3 | 0 weeks | ~3–4 weeks |
| #5+ | 0 weeks | human-approval time + minor rule tuning |

After ~4–5 projects the 20-week investment is recouped in pure time savings;
everything after is leverage. A one-off product build never reaches that
crossover.

---

## 3. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Adopter target** | Internal-only for v1.0 | No customer isolation, billing, SOC 2, or marketing surface needed. Fastest path to real-world validation. |
| **Approval surface** | Humans on bookends only | ✋ Intent approval + ✋ prod deploy. 🤖 Rule-gated everywhere between. Runs autonomously most of the time. |
| **CD/CI stack** | GitHub Actions + Google Cloud Run | Container-per-service, deploy = `gcloud run deploy --tag`. No k8s complexity. Swappable behind a deploy adapter later. |
| **Repo model** | Monorepo (`backend/` + `frontend/` + `infra/`) | One worktree per task; simpler than multi-repo coordination for v1.0. |
| **Default workflow pattern** | Sequential (plan → implement → test) with reviewer chain between | Already supported by the platform; no new pattern needed. |
| **Rule engine** | YAML DSL + LLM-judge fallback for grey-zone rules | Hard rules deterministic + audited; soft rules wrap an LLM-as-judge activity with rationale stamped on the audit row. |
| **Build philosophy** | Adoption-first, wedge-driven | First useful value at Week 4, not Week 18. Three standalone wedges before the full pipeline. |

---

## 4. The five adoption levers

Every architectural decision is shaped by these:

1. **Wedge-first shipping.** Three standalone tools ship in weeks 1-4, 5-8, 11.
   Each is useful on its own; adopters can take just one.
2. **Adapter layer for source systems.** Confluence + Jira are the first
   adapters behind a `SourceAdapter` / `IssueTrackerAdapter` interface. Notion,
   Linear, GitHub Issues swap in without touching agent logic.
3. **Dry-run / shadow modes everywhere.** Every workflow runs in
   `live | dry-run | shadow`. Shadow runs alongside the existing human process,
   makes no changes, and produces a "what I would have done" diff.
4. **Read-only default, write-on-approval.** First install = read from
   Confluence/Jira, every write needs approval. Trust ladder:
   read → suggest → execute-with-approval → autonomous.
5. **Opinionated defaults + escape hatches.** The recommended stack needs zero
   customization. Anything else = write one adapter.

---

## 5. Architecture

One long-running **`SDLCWorkflow`** (Temporal) per requirement, with child
**`FeatureImplementationWorkflow`** instances fanned out per Jira issue.

```
                                 ┌──────────────── SDLCWorkflow (Temporal) ─────────────────┐
                                 │                                                            │
  Confluence link ─► confluence_ingest ─► intent_extractor ─► gap_analyzer                   │
                    │                            │                  │                          │
                    │                            └──► AUDIT ◄────────┘                          │
                    │                                                                          │
                    │                ✋ HUMAN GATE: "approve these intents"                     │
                    │                                  │                                        │
                    │                feature_spec_writer → tech_spec_writer                     │
                    │                                  │                                        │
                    │                  SpecCompletenessVerifier (rule)                          │
                    │                                  │                                        │
                    │                  jira_issue_creator (one issue per spec)                  │
                    │                                  │                                        │
                    │             ┌────────────────────┴───────────────────┐                    │
                    │             │ fan out: one child workflow per issue                       │
                    │             ▼                                                            │
                    │   ┌── FeatureImplementationWorkflow (child) ──────────────────────────┐  │
                    │   │ code_planner → implementer → test_author → TestRunVerifier (rule)  │  │
                    │   │       │                                          │ (replan on fail) │  │
                    │   │       ▼                                                              │  │
                    │   │ reviewer_agent → SecurityVerifier (rule) → open PR → auto-merge     │  │
                    │   └──────────────────────────────────────────────────────────────────┘  │
                    │             │                                                            │
                    │             │ fan-in: all issues merged                                  │
                    │             ▼                                                            │
                    │   trigger_integration_tests → IntegrationTestVerifier (rule)             │
                    │             │                                                            │
                    │   deploy_to_staging → smoke verifier → run_e2e → E2ETestVerifier (rule)  │
                    │             │                                                            │
                    │   ✋ HUMAN GATE: "promote to prod" (+ release notes via modify_input)     │
                    │             │                                                            │
                    │   deploy_to_prod (one Cloud Run revision) → post_deploy_smoke → DONE     │
                    └────────────────────────────────────────────────────────────────────────┘
```

### Platform primitives reused (✓ exist today)

| Stage need | Platform primitive |
|---|---|
| Multi-stage durable workflow + checkpointing + retries | Temporal workflow (Sprint 13) |
| Approval gates + REST API + audit chaining | Sprint 14 |
| Audit on every LLM/tool/decision | Native (Sprints 5/8/10/14) |
| Replan on verifier failure | Sprint 12 |
| Glossary + policy verification | Sprints 10/11 |
| Sequential / manager-specialists patterns | Sprints 8/9 |

### Domain layer to build (⬜ new)

| Layer | Pieces |
|---|---|
| Source connectors | `tool.confluence.*`, `tool.jira.*` (behind adapters) |
| Intent + spec templates | `intent_extractor`, `gap_analyzer`, `feature_spec_writer`, `tech_spec_writer` |
| Domain verifiers | `GapVerifier`, `SpecCompletenessVerifier`, `TestCoverageVerifier`, `SecurityVerifier`, `SecretsVerifier`, `StyleVerifier`, `IntegrationTestVerifier`, `E2ETestVerifier` |
| Workspace + code tools | `WorkspaceManager`, `tool.fs.*`, `tool.shell.run`, `tool.git.*`, `tool.github.*` |
| Code-gen templates | `code_planner`, `implementer`, `test_author`, `reviewer` |
| Iterative refinement loop | "test fails → patch → retest" inner loop (architecture change) |
| CI/Deploy connectors | `tool.gha.*`, `tool.gcloud.deploy_run` |
| Orchestrating workflows | `SDLCWorkflow` (parent) + `FeatureImplementationWorkflow` (child) |
| Rule engine | YAML DSL + LLM-judge fallback |

---

## 6. The 20-week roadmap

| Block | Weeks | What ships | Who can use it |
|---|---|---|---|
| **A** | 1–4 | Standalone PR-Reviewer (GitHub App) | Any eng team with a GitHub repo |
| **B** | 5–8 | Confluence → Structured Jira backlog | Tech leads, EMs, PMs (non-engineers too) |
| **C** | 9–11 | End-to-end SDLC pipeline skeleton (stubs OK) | Demo audience — leadership, prospective adopters |
| **D** | 12–18 | Real code-gen + iterative refinement | Eng teams running real features |
| **E** | 19–20 | Web UI + team scoping + onboarding | Any team, self-service |

### Block A (Weeks 1–4) — Adopter Wedge #1: Standalone PR Reviewer

A GitHub App adopters install in one click. Watches PRs, runs the verifier
chain over the diff, posts review comments with rationale. Zero infrastructure
on the adopter's side, zero workspace state. Pure read → analyze → comment.

| Build | Effort |
|---|---|
| GitHub App scaffold: install URL, webhook receiver, JWT PR auth | 3 days |
| `tool.github.{fetch_pr_diff, post_review_comment, request_changes}` | 2 days |
| `agent.code_reviewer` template (structured: severity, file, line, suggestion) | 2 days |
| `SecurityVerifier`, `SecretsVerifier`, `StyleVerifier` | 5 days |
| Hosted preview instance (Cloud Run) pointing at a demo repo | 4 days |
| Docs: 5-minute install + 10-minute eval guide + 3 example reviews | 2 days |
| Internal dogfood + tuning | 4 days |

**Gate (end of Week 4):** A senior engineer installs the App in 5 minutes,
points it at a repo, sees real review comments on their next PR within an hour.
First named adopter.

### Block B (Weeks 5–8) — Adopter Wedge #2: Confluence → Structured Backlog

Takes a Confluence space URL, produces a structured Jira backlog. Read-only on
Confluence, write-on-approval on Jira. No code generation yet — pure
planning-stage value.

| Build | Effort |
|---|---|
| `SourceAdapter` interface + Confluence adapter (content extraction, hierarchy walk) | 5 days |
| `agent.intent_extractor` (intent, scope, dependencies, NFRs, gaps) | 3 days |
| `agent.gap_analyzer` + YAML rule format for gap rules | 4 days |
| `agent.spec_writer` → feature spec + acceptance criteria | 3 days |
| `IssueTrackerAdapter` + Jira adapter (issue/epic create, link, transition) + dry-run | 4 days |
| Approval gate before Jira creation (batch-create all issues on one approval) | 1 day |
| CLI: `orchestrator ingest --source confluence://space/X --dry-run` | 3 days |
| Web preview: paste Confluence URL → see backlog before committing | 5 days |

**Gate (end of Week 8):** Anyone with Confluence read access runs the CLI, gets
a structured Jira backlog proposal in 15 minutes, reviews/edits/approves in
batch. Non-engineers become adopters.

### Block C (Weeks 9–11) — End-to-end skeleton (everything else stubbed)

The full pipeline shape, sandwiched between two shipped wedges. Builds
confidence in the architecture without the deep code-gen work yet.

- Week 9: `SDLCWorkflow` parent + `FeatureImplementationWorkflow` child.
  `WorkspaceManager` (per-task git worktree). Stub code generator
  (writes one hardcoded file per issue).
- Week 10: Real Cloud Run deploy + stub E2E verifier. Both human approval gates
  wired (intent from Block B; add prod-deploy gate).
- Week 11: Demo — a Confluence page goes A→Z to a Cloud Run deployment,
  everything stubbed but the orchestration. Target ~10 minutes end-to-end.

**Gate (end of Week 11):** The full pipeline marches through on one toy input,
both approval gates firing, audit accumulating, trace UI showing every stage.
**Do not skip this phase.**

### Block D (Weeks 12–18) — Code-gen depth + iterative refinement

The hardest block. Push PR acceptance from ~40% to ~70%.

- Weeks 12–13: Real `code_planner` + `implementer` + `test_author` +
  `TestRunVerifier`. Single-shot codegen.
- Weeks 14–15: Real CI integration (`tool.gha.*`) + `IntegrationTestVerifier`
  + real Cloud Run staging deploy + real `E2ETestVerifier` (Playwright in a
  container against the staging URL).
- Weeks 16–18: **Iterative refinement loop** — implementer gets up to 5 inner
  iterations on test failure before escalating. The big architectural change;
  this is what makes code-gen useful.

**Gate (end of Week 18):** Full pipeline runs A→Z for simple-to-moderate
features at ~70% acceptance rate.

### Block E (Weeks 19–20) — Polish for internal adoption

- Week 19: **Web UI** — minimal SPA over the existing REST + trace HTML.
  Approval queue, backlog viewer, run timeline. No design system.
- Week 20: **Team scoping** (one `team_id` column + per-team approval routing),
  **templates as a forkable git repo**, **`orchestrator init`** onboarding
  runbook + 3–5 reference deployments documented.

**End of Week 20:** SDLC Orchestrator v1.0. Two production adopters minimum
(your team + one pilot). Three wedges that each work standalone.

---

## 7. Adopter-visible milestones

| End of week | Internal adopter can... |
|---|---|
| 4 | Install a GitHub App, get code reviews on PRs |
| 8 | Run a CLI command on a Confluence space, get a Jira backlog proposal |
| 11 | Watch a demo of the full pipeline running A→Z |
| 15 | Submit a real feature spec, get a working PR (~40% acceptance) |
| 18 | Submit a real feature spec, get a merged + deployed-to-staging feature (~70%) |
| 20 | Self-serve onboard their team via web UI |

Each row is a concrete demo for leadership / prospective adopter teams.

---

## 8. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Verifier chain doesn't catch real code defects | High | Block A forces this question by Week 4 (precision/recall on 20 labelled PRs) |
| Code-gen quality stays low | High | Block D's 7 weeks + iterative refinement; ~60% of total effort budgeted here |
| Iterative refinement loop architecture proves wrong | High | Prototype aggressively in Week 16 even if revisited |
| Workspace state mgmt brittle | Medium | Block C builds + stress-tests it on the skeleton |
| LLM cost spirals on code-gen | Medium | Budget enforcement (IR fields exist); instrument cost-per-task from Week 1 |
| "Where's the product" at week 8 | Medium | Blocks A + B ship real adopter value by then |
| Skeleton phase blows up (Week 11) | Medium | If Week 11 ends with "still architecting," reduce scope to intent→spec→Jira only |

---

## 9. Effort + staffing

- **One focused engineer:** ~20 weeks to v1.0.
- **Two engineers:** ~12 weeks (one on domain connectors, one on workflow +
  templates + verifiers).
- Code generation (Block D) is ~60% of total effort — staff accordingly.

Build philosophy: **improve the orchestrator platform only when blocked.**
Platform improvements are reactive — build what the next block reveals as
missing, keep shipping adopter value.

---

## 10. Week 1 starting actions (Block A)

**Day 0 (prep, ~1 hour):**
1. Pick the target repo for Block A dogfood (your org's most-active repo).
2. Create a GitHub App (Settings → Developer settings → GitHub Apps → New).
   Permissions: `pull_requests: write`, `contents: read`. Save App ID + private key.
3. Decide hosting for the webhook receiver (Cloud Run recommended).

**Day 1 (Monday):**
4. Branch `git checkout -b block-a-pr-reviewer` from `develop`.
5. Wire the GitHub App: webhook receiver, JWT signing, install/uninstall handlers.
   Cloud Run deploy; callback URL points at the running revision.
6. End of Day 1: opening a PR fires the webhook and writes an audit row
   (no review yet — just proving the integration).

**Days 2–5:**
7. `tool.github.{fetch_pr_diff, post_review_comment}` + `agent.code_reviewer`
   template + 3 code-aware verifiers (`SecurityVerifier`, `SecretsVerifier`,
   `StyleVerifier`).
8. End of Day 5: open a PR with a deliberate bug (hardcoded secret, SQL
   injection, missing test) and verify a review comment posts within 60 seconds.

**End of Week 1:** Real PR review comments from the orchestrator on your org's
most-used repo. First adopter (you) using it for real.

---

## 11. Decision log (open items)

| # | Question | Status |
|---|---|---|
| 1 | Adopter target | ✅ Internal-only for v1.0 |
| 2 | Approval surface | ✅ Humans on bookends; rules between |
| 3 | CD/CI stack | ✅ GitHub Actions + Cloud Run |
| 4 | Repo model | ✅ Monorepo |
| 5 | Build philosophy | ✅ Adoption-first, wedge-driven |
| 6 | SSO provider for the web UI (Block E) | ⬜ Decide before Week 19 |
| 7 | Confluence space + Jira project for first real run | ⬜ Decide before Block B (Week 5) |
| 8 | Rule DSL syntax (YAML schema for gap + spec + code rules) | ⬜ Design in Block B (Week 6) |
| 9 | E2E framework (Playwright vs Cypress) | ⬜ Decide before Week 14 |

---

*This plan sits on top of the agent-orchestrator platform (Sprints 1–14,
branch `develop`). It re-scopes the spec'd Sprint 15+ work toward the
SDLC-orchestrator domain. The platform substrate — planner, verifier chain,
replan loop, Temporal workflows, approval gates, audit log — is complete and
directly reused.*
