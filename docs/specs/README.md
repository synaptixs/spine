# Engineering Master Plan — Agent Orchestrator

**The single source of truth for engineering.** Status, roadmap, and the current
build focus (Product Knowledge Graph × ontomesh) — all in one place.
*Last updated: 2026-06-15 · `develop` · 810 tests green · **six autonomous features merged** (PR #16 create, #18 edit, #20 new-package, #21 create+edit; AEO-27 = first `--live` run against an external private repo via the GitHub App) · Phase-7 wedges (Slack notifier, `doctor`) built by the pipeline. G14 live-onboarding hardening landed: `doctor` bridges `.env`, codegen inherits `ORCHESTRATOR_INTAKE_MODEL`, GitHub-App clone/push auth, source-mismatch base rebuild, `sdlc complete` merge→Jira-Done. G7 Notion + `file://` adapters wired through the CLI/SDLC. **G1: codegen now grounds on the target repo's own PKG (CLI + worker), base fast-forwards so comprehension compounds; ontomesh ontology layer deferred (not on the critical path).***

> This doc merges what used to be three overlapping files (status report,
> platform roadmap, PKG plan). Superseded docs now live in [`archive/`](./archive/).
> `models.md` remains the data-model reference. Business/commercial docs live in
> `docs/` (the `bundle-*` files + `MASTER-INDEX`), a separate track.

---

## 1. Where we are now

**Two products, one substrate:** a domain-agnostic engine for *AI-native
software* (LLM agents that plan, act, and are verified, with durable execution,
human gates, and an audit trail), whose flagship vertical is an **AI-native
SDLC** (Confluence/Jira/GitHub → intents → specs → code → tests → review →
**merged, CI-green PR**, autonomous between two human bookends).

> **Scope:** pipeline ends at a reviewed, CI-green **merged PR**. Deployment is
> out of scope (hand off to existing CD); the two human bookends are **intent**
> and **merge** approval.

**The thesis:** most autonomous-SDLC attempts build the generative layer and bolt
on governance. We built **governance + orchestration first** — the part that makes
autonomy safe — and now build generative depth on top, grounded in a model of the
existing system so agents engineer *within* a codebase.

**Executed by agents.** The roadmap below is delivered by the orchestrator's own
agents building *this* repo's backlog; humans act only at the two bookends. Human
effort is the bootstrap to rung 1, after which the platform raises its own
autonomy and ships its own next phase.

### Built & proven

| Layer | State |
|---|---|
| **Platform substrate** | ✅ ~501 tests. Planner + IR + validator, verifier chain (Schema/Confidence/Evidence/Policy/Glossary), replan loop, Temporal durability, approval gates, append-only audit, registry + glossary + calibration, gateway + sandboxed tools, Postgres + S3/MinIO. |
| **Block A — PR Reviewer** | ✅ live-verified. Webhook → GitHub App auth → diff fetch → LLM review + Secrets/Security/Style verifiers → posted review. (Real 43-file PR → REQUEST_CHANGES.) |
| **Block B — Requirements→Backlog** | ✅ live-verified. Confluence adapter (read + create_page), intent extraction, gap analysis (YAML), spec writing, Jira adapter (dry-run default), `orchestrator ingest`. (Real space → 110 intents → child page; AEO backlog previewed.) |
| **Block C/D — SDLC pipeline** | ✅ **every merge-path adapter real**: PKG-grounded codegen (create-only guard + **anchored-edit patching of existing files**) · pytest runner + refine loop (worktree-aware PYTHONPATH) · semantic-correctness judge · real PR (`gh`) · fail-closed GHA CI (live-verified) · gate-approved merge-on-green · calibrated escalation · fan-out caps (`--max-features/--max-parallel`) · **per-run LLM budget cap (`SDLC_RUN_BUDGET_USD`)**. Stubs remain only as safe defaults; legacy deploy stages **removed** — the pipeline ends at the merge. |
| **Cost audit** | ✅ `RecordingLLMClient`/`TokenLedger` per-stage token/cost/latency; four-table traceability report. |

### Autonomous-agent scorecard

Where the platform stands against the canonical autonomous skill-using-agent blueprint
(control loop + the scaffolding that lets it run unattended). Status as of the cross-run
memory + live-observability work.

| Capability | Status | Evidence |
|---|---|---|
| **1. Core loop** (perceive→plan→act→observe→reflect) | ✅ Done | `AgentLoop` (think→act→observe, no-progress + step caps) + Temporal replan loop; live-proven self-refinement (tests fail→fix→pass). |
| **2. Skills / declarative discovery** | ✅ Done | `catalog/` capability registry + selector; per-run skill/MCP conditioning; in-loop tools (`build_readonly_tools`, codegen, MCP, `recall_memory`). |
| **3. Memory** | ✅ Done | Working (`_State`) + episodic (run bundle/trace) + **semantic cross-run memory** (`MemoryRow`: recall + priming + post-merge consolidation + decay). |
| **4. Planning that survives failure** | ✅ Done | Replanning planner + `VerifierChain` + failure dispatch (replan/escalate/insert-verifier); per-edge success criteria. |
| **5. Durable execution** | ✅ Done | Temporal workflows + Postgres LangGraph checkpointer + resumable `LoopCheckpoint` across approval pauses. |
| **6. Guardrails / approval gates** | ✅ Mostly | Policy-as-code (allow/deny/require-approval), two human bookend gates, in-loop pause/resume, write-gating. *RBAC/multi-tenancy ~10% (G11).* |
| **7. Observability** | ✅ Done | Append-only audit + run-bundle replay + **live OpenTelemetry** (LLM / loop-step / cross-process Temporal spans, joined on `trace_id`); Jaeger-verified end to end. |
| **8. Stop conditions / budgets** | ✅ Done | Per-run `RunBudget` cap, loop step cap, no-progress detector, per-activity timeouts, memory decay floor. |

**Net:** the autonomy substrate is complete — control loop, skills, cross-run memory, durable
execution, governance, live observability, and bounded stop conditions are all built and
(except RBAC) proven. The frontier is now *trust depth* (Rung 2→3: widening the class of
features merged unattended) and *breadth* (RBAC/multi-tenancy, more languages), not missing
agent machinery.

**Edit-based codegen is live-proven.** Runs #13–17 (2026-06-12) validated
Track 2.3 on real infra: ticket "median call count for PKG graph statistics" →
**PR #18 merged autonomously**, with `median_call_count` added to the existing
`src/orchestrator/pkg/stats.py` via anchored edits (+322 lines of agent-written
tests). Five new defects found live, each fixed once: snippet-anchored edits
miss → **anchor-repair retry** (resend exact file content); cloned-base push
identity (GH007) → neutral identity on clones; intake paraphrased away file
paths → verbatim-identifier fidelity rules; new root module shadowed stdlib
`statistics` → deterministic shadow guard (run #15's wrong PR was **correctly
denied at gate 2** — the trust stack caught it); tests-prompt lacked the edits
form → both forms + guard-skip repair. Refine loop converged twice in run #17
(red tests, then a mypy preflight failure).

**Phase 7 — agent-built control-plane wedges MERGED (runs #18–26,
2026-06-12/13).** The pipeline built its own Phase-7 backlog: **PR #20
(Slack notifier, G13)** and **PR #21 (`orchestrator doctor`, G14)** both
went Confluence-ticket → autonomous codegen → CI-green → human merge gate →
merged. PR #21 was a true create+edit feature (new `doctor.py` + an anchored
edit registering the command in the existing `cli.py`). Getting there
hardened the codegen pipeline with **seven fixes now on develop**, each a
latent bug only a new-directory / multi-file / create+edit / larger feature
could expose: (1) author_tests truncated mid-JSON → 16k completion-token cap;
(2) ScheduleToClose starvation → 15-min codegen-stage timeout; (3) reviewer
blind to source in a brand-new package dir (`git status` collapses untracked
dirs) → `-uall`; (4) literal newlines in generated `content` →
`json.loads(strict=False)`; (5) base-branch format hygiene (an unformatted
file poisoned every feature's preflight); (6) **intake genericized API
contracts** out of acceptance criteria → `Intent.acceptance_criteria` carries
stated contracts verbatim intent→spec (run #23's PR #19 was correctly denied
at the gate before this fix); (7) **create+edit dropped the edit** — a
full-content rewrite of an existing file was guard-skipped while the new file
landed → guard-skip now triggers the anchor-repair retry even when other
files succeed (run #25 denied; run #26 merged). Two merge-gate denials (#19,
#25) and two adversarial reviewer catches show the trust stack working.
**Lesson:** the contract that survives is the one in *acceptance criteria*,
not prose.

**Software-engineer persona (the current thrust).** Reframing the remaining
gaps as "what does a SWE do that the agent can't yet," three landed: the
child now **responds to review feedback** (a judge BLOCKER loops back through
codegen instead of ending the feature — validated live, run #30); codegen
**learns the repo's conventions** (G8 — derived house-style digest injected
into the prompt); and the intent gate **asks clarifying questions** (the
extractor's open questions surface to the approver, whose `clarifications`
fold into the specs before codegen). Slack alerts now fire from the gate.
The agent now also **responds to human comments on a live PR**
(`orchestrator sdlc address-review --pr <url>`: fetch the reviewers'
comments → refine → re-drive to green → push to the PR branch), closing the
second half of the review-feedback gap. Remaining persona gaps:
multi-language · identity/voice · richer comprehension/convention depth.

**Net: the loop is closed.** Run #12 went green end-to-end on real infra:
Confluence ticket → 1 intent → gate-1 approval → PKG-grounded codegen →
tests → **preflight (CI-parity ruff/format/mypy, refined locally)** →
semantic judge → escalation surfaced to the approver (blast radius 29) →
real PR #16 → real CI green → gate-2 approval → **merged autonomously**
(`9071af7`, `pkg/stats.py` + tests now on develop, suite green). Twelve
live runs hardened the pipeline: eleven distinct defects, each fixed once
(grounding wiring, test-runner path + env sanitization, observability,
create-only guard, graceful degradation, intake fidelity, fan-out caps,
push identity, lint autofix, preflight parity). **Trust ladder: rung 2.**

### Git state
`develop` is the single source of truth and the repo's **default branch**
(Blocks A+B+C merged, in sync with origin). Stale branches reconciled: the
Block-B web preview was cherry-picked from `infra-a-b-enablement` before
deletion; `block-c-skeleton` is gone. Secrets discipline: `.env`/`*.pem`
gitignored, kept out of history (verified).

---

## 2. Principles (non-negotiable)

1. **Everything grounded, everything verified** — every agent output is a Claim
   with Evidence, checked by a verifier; extended to *system knowledge* and
   *generated code*.
2. **Derived, not authored** — knowledge about the system is *extracted* with
   provenance; a build artifact, never a hand-maintained truth that rots.
3. **Adapters at every external seam** — source, tracker, VCS, CI, LLM,
   knowledge graph — all behind Protocols.
4. **Read-only / dry-run by default** — writes are gated.
5. **Wedge-first** — ship a narrow useful slice, validate on a real repo, widen.
6. **Humans at the bookends, rules in the middle.**

---

## 3. Gap taxonomy (G1–G17)

**% in place** = how much of each gap is already addressed in the codebase today
(grounded in source/test inventory + live runs; updated 2026-06-11 after PRs
#2–#8 + the acceptance milestone).

| # | Gap | Sev | **% in place** | What exists / what's missing |
|---|---|---|---:|---|
| **G1** | No model of the existing system (code + docs) | **Crit** | **84%** | Track 1 v0 + field nodes & IMPLEMENTS edges (1117 fields + 125 inheritance edges on this repo) + **codegen now GROUNDS on the target repo's own PKG** — `PKGCodegenGrounder` wired into both the `feature` CLI and the Temporal worker (per-worktree, greenfield-safe, brownfield-bounded); the reused base fast-forwards to the remote latest so comprehension *compounds* across merged tickets (proven live on AEO: grounded run reused the merged `stack_decision.py` vs inventing a new module). Plus extractor, commit-keyed cache, GroundingVerifier, doc-drift · *embeddings/semantic retrieval (brownfield-gated); field-level edges (READS/WRITES); ontomesh ontology layer deferred* |
| G2 | Code gen that reliably compiles/passes/fits | **Crit** | **85%** | **Benchmarked 9/10 accepted** (`codegen_benchmark.py`: edit 5/5, create 4/5; acceptance = tests + CI-parity preflight + fit; $0.68 total) + patch-based editing live-proven (PR #18) + stdlib-shadow guard + intake path-fidelity rules · *larger sample, cross-model* |
| G3 | Iterative refinement at depth | **Crit** | **70%** | Refines on **tests AND preflight** (run #12); **responds to review feedback both ways** — the internal semantic judge (loops a BLOCKER back through codegen, re-reviews ≤`max_review_iterations`) AND **human comments on a live PR** (`sdlc address-review`: fetch → refine → re-drive → push). refine cap 3→5 · *convergence on hard tickets; multi-pass human loop* |
| G4 | Semantic verification (correctness/security/perf/drift) | High | **65%** | + **semantic-correctness judge** (spec-vs-code, fail-closed, live-validated) + **Semgrep verifier** (fail-visible) · *no contract/perf verifiers* |
| G5 | Real PR + CI integration (up to merge) | High | **100%** | **Fully proven live**: real PR → real CI green → gate-approved autonomous merge (PR #16). Preflight gives local CI parity; legacy deploy stubs removed — merge is the terminal stage |
| G6 | Multi-language / multi-stack | High | **35%** | **Second language shipped — `JavaExtractor` (tree-sitter)** emits Module/Type/Function/Field + IMPORTS/CONTAINS/IMPLEMENTS onto the universal schema; seam decoupled (per-extractor module-naming); optional `java` extra, lazy-imported · *Java/Python only; no CALLS for Java; fixture-tested (no non-Python repo to dogfood)* |
| G7 | Adapter breadth (Notion/Linear/GitLab/ADO…) | Med | **48%** | Confluence/Jira/GitHub + **Notion + `file://` source adapters now wired end-to-end** through the CLI (`ingest`, `sdlc`) and SDLC pipeline via a `build_service_for(uri)` kind-dispatcher (`SUPPORTED_SOURCE_KINDS`); credential-free local-file intake · *5 source kinds; Linear/GitLab/ADO + MCP-transport option (Track 5) pending* |
| G8 | Repo-convention learning | Med | **35%** | **`conventions.extract_conventions` derives a house-style digest** from the repo's own files (future-imports, absolute-import style, docstrings, typed defs, line length, test shape; ≥60%-prevalence gated) → injected into the codegen prompt so output reads team-written · *style only — not yet layout/error-handling/naming patterns; not validated as a verifier* |
| G9 | Cost/budget governance | High | **80%** | **Per-run budget enforced**: `RunBudget` + `BudgetedLLMClient` cap each run's LLM spend (`SDLC_RUN_BUDGET_USD`, default $25); concurrent runs independently capped; a tripped budget terminates the run as `features_failed` · *budget-spend audit row, IR-level budgets for non-SDLC tasks* |
| G10 | Confidence-calibrated escalation | High | **60%** | **EscalationPolicy wired**: judge uncertainty + refine effort + PKG blast radius → flagged to the merge-gate approver · *thresholds static; calibration-history tuning pending* |
| G11 | Multi-tenancy + RBAC | High | **10%** | `approver_roles` field · *no `tenant_id`, no RBAC enforcement* |
| G12 | Console UI for gates + runs | High | **50%** | **Operator console shipped** (`GET /console`): server-rendered approval-gate queue (review risk/description/open-questions → approve / reject / clarifications-or-release-notes patch) + runs dashboard (state per SDLC run, link to `/trace`), backed by `GET /v1/runs`; data-free shell, all data/actions via the API-key'd JSON API + **a "live" poll toggle** (10s auto-refresh, paused while a detail is open or the tab is hidden) · *poll-based, not SSE; no per-tenant scoping (waits on G11)* |
| G13 | Notifications (Slack/email/webpush) | Med | **55%** | `SlackWebhookNotifier` (agent-built, PR #20) **now wired into `raise_approval_request`** — every gate posts a best-effort Slack alert (async, never blocks/raises, no-op when unset); verified end-to-end against a real webhook · *no email/webpush; not per-tenant routed* |
| G14 | Onboarding (`init` / `doctor`) | Med | **75%** | **`doctor` + `init` + live-run plumbing**: `init` scaffolds a commented `.env` from shared `ENV_GROUPS`; **`doctor` now bridges `.env`** so its report matches what the pipeline sees; codegen **inherits `ORCHESTRATOR_INTAKE_MODEL`** (one model drives the whole run); **private-repo clone/push auth** (env PAT or GitHub-App installation token); base repo **rebuilds on source mismatch** (`--safe`→`--live` no longer reuses a remote-less scratch base); **`sdlc complete --pr`** closes the merge→Jira-Done bookend for the linear path · *no repo-archetype/project scaffold beyond .env* |
| G15 | Secrets/vault beyond `.env` | Med | **10%** | `.env` + `load_local_env` · *no vault* |
| G16 | Scale, SRE, compliance, OTel | Med | **40%** | Temporal durability + audit log + **live OpenTelemetry tracing (Phases 1–3 complete)**: `orchestrator/obs/tracing.py` no-op-by-default seam (`span`/`traced`/`bind_trace_id`/`add_event`/`temporal_interceptors`, OTLP/HTTP via the `otel` extra) → `llm.complete` span per LLM call (every stage) + `agent.step`/`tool.<name>` spans + policy-block/needs-approval span events in the agentic loop + **Temporal OTel interceptor on client+worker** for the full cross-process trace (API → workflow → activities → loop → LLM/tool), `execute_graph_pass` binds the app `trace_id`; spans join the audit log on `trace_id` (`docs/specs/live-observability-otel.md`) · *no crypto-chaining/SOC2/compliance bundles* |
| G17 | Platform SDK / non-SDLC verticals | Med | **15%** | Registry + Protocols + substrate · *no public SDK/reference verticals* |

**Rolled-up completion:**
- **~35% of the named gaps** addressed (simple average; ~20% at first scoring).
- With the finished substrate (~35–40% of total effort) weighted in: **~65% toward
  the full platform vision.**
- **Phases 5 AND 6 complete, live-proven**: PKG v0 · grounded codegen (3/3 A/B,
  one real feature merged) · semantic judge · Semgrep · calibrated escalation ·
  preflight CI-parity · merge-on-green — all exercised in run #12's green E2E.
  **Trust ladder: rung 2** (autonomous-for-a-class). The post-merge hardening
  pass landed patch-based editing (Track 2.3), deploy-stub removal, and budget
  enforcement (G9); remaining frontier is live validation of edit-based
  features, then the agent-paced breadth phases (7–8).

---

## 4. Current focus — Product Knowledge Graph (G1) × ontomesh

The strategic gap is **comprehension**: agents generate and review without a
grounded model of the *existing* system. We close it with a **Product Knowledge
Graph (PKG)** built from code + docs + data and fed to the SDLC agents.

### 4.1 Ontomesh — what it is, and the fit

**Ontomesh** (`synaptixs/ontomesh`, v3.8.0, Apache-2.0, preview) — *"the ontology
mesh for GraphRAG."* It ingests **DB schemas** (via an annotation control plane),
**logs** (Drain3/HMM/Granger), and a **modeling wizard**; it produces **OWL/SHACL/
JSON-LD with PROV-O lineage, OWL-RL/SWRL/Datalog reasoning, a hybrid GraphRAG
retriever, and SPARQL** over Postgres/SQLite via REST.

**Critical caveat:** ontomesh **does not parse source code**. So the orchestrator
owns code→facts extraction; ontomesh owns the ontology/retrieval/validation layer.

| Track-1 wedge | Owner |
|---|---|
| Structural code extractor (AST, imports, ORM→entities, routes, test↔code) | **Orchestrator** |
| Doc/semantic layer (glossary, business rules, sensitivity) | Ontomesh |
| Retrieval API (GraphRAG + SPARQL) | Ontomesh |
| GroundingVerifier + freshness (SHACL + PROV-O + drift feed) | Ontomesh + thin Orchestrator wiring |
| Formal ontology / OWL growth | Ontomesh |

### 4.2 Posture & coupling (decided)

- **Hybrid:** integrate ontomesh as a **pinned black box** (`:3.8.0`) first; add
  ontomesh changes only where coercing code facts into its relational ingestion
  proves lossy.
- **Coupling:** **sidecar via REST/SPARQL** behind a `KnowledgeGraphAdapter`
  Protocol (not library import) sharing our Postgres (`ONTOMESH_DB_URL`).
- **Headline work split: ~75% orchestrator · ~25% ontomesh.**

### 4.3 Greenfield vs brownfield — one PKG, two seeds

Not two pipelines — the same PKG-grounded loop seeded two ways; greenfield
**matures into** brownfield as features land.

| | **Brownfield** | **Greenfield** |
|---|---|---|
| Seed source | *Extraction* (code+docs+data → facts) | *Modeling* (brief → intended ontology) |
| Flow | reality → PKG (conform to what **is**) | intent → PKG → generation → PKG (conform to what's **intended**) |
| Ontomesh path | schema/log/**code** ingestion | the **wizard** |
| Conventions | *learned* (G8) | *declared*, then accumulate |
| GroundingVerifier | **consistency** (don't break invariants) | **conformance** (match the model) |

One verifier, switched by **PROV-O provenance** (`extracted` vs `modeled` facts).
Greenfield reuses the **existing wizard** → almost no new ontomesh work; the new
build is orchestrator-side onboarding (mode detection, scaffold, convention
model).

### 4.4 Build phases & per-repo split

| Phase | What | Orch | Ontomesh |
|---|---|---:|---:|
| **0 — Contract & spike** | sidecar up, run `toolkit.py`, decide fact-ingestion contract, write `PRODUCT-KNOWLEDGE-GRAPH.md` | 80% | 20% (assess) |
| **B — Bootstrap** | `init` mode detect · brownfield crawl + drift · convention learning · greenfield wizard seeding · scaffold gen · provenance tagging · maturity handover | 90% | 10% (drive wizard) |
| **1 — Code extractor** | `RepoCodeExtractor` (AST/imports/ORM/routes/tests + `file:line`) + fact serializer + `pkg extract` CLI | 100% | 0% |
| **2 — Ingestion & round-trip** | `KnowledgeGraphAdapter` + push facts + reconcile docs↔code. *[only if lossy]* code-ingestion module / pgvector / retrieval endpoint | 60% | 40% |
| **3 — Grounding agents** | RAG codegen + reviewer + gap-analyzer · GroundingVerifier (SHACL) · merge-hook re-extraction | 85% | 15% |
| **4 — Hardening** | 2nd language · schema growth · monorepo · perf | 70% | 30% |
| | **Weighted** | **~75%** | **~25%** |

**Critical path:** Phase 0 sets the contract → Phases 1 and B run in parallel
(Phase 1 needs no ontomesh; greenfield half of B reuses the wizard) → Phase 2
(ingest/retrieve) → Phase 3 (ground agents) → Phase 4. Ontomesh changes are
deferred to Phase 2 and only the subset Phase 0 proves necessary.

### 4.5 Phase-0 open questions
1. Can code facts be coerced into `ontology_metadata` + relational rows without
   meaningful loss, or is a first-class code-ingestion module needed? *(Sets the
   real ontomesh %.)*
2. Is ontomesh's retrieval surface (Ask/SPARQL/`/api/ontologies`) stable enough
   to depend on headlessly?
3. pgvector in ontomesh (shared store) vs its FAISS/Chroma backend separately?
4. Postgres topology: shared DB + separate schema vs separate database.

---

## 5. Full roadmap — eight tracks (agent-paced)

The PKG (§4) is Track 1. The rest, condensed:

| Track | Theme | Gaps | Key wedges |
|---|---|---|---|
| **1** | **Comprehension (PKG)** — §4 | G1 | extractor · doc layer · GraphRAG · GroundingVerifier |
| **2** | Generative depth | G2,G3 | LLM CodegenAdapter (RAG from PKG) · refinement at depth · repo-context tools · acceptance harness |
| **3** | Verification depth | G4,G10 | semantic-correctness · security scan (Semgrep) · contract/invariant (PKG) · coverage/perf · calibrated escalation |
| **4** | Real PR + CI (to merge) | G5 | real PR (forge) · real CI (GHA) · merge on green. *Deploy/E2E/rollback out of scope — handoff to existing CD* |
| **5** | Generality | G6,G7,G8 | language packs · adapter breadth · **MCP-transport connectors** · convention learning · project archetypes |
| **6** | Control plane & adoption | G11–G15 | multi-tenancy+RBAC · console UI · notifications · `init` onboarding · secrets · cost governance |
| **7** | Operational hardening | G16 | scale · SRE · OTel · compliance · platform CI |
| **8** | Platformization | G17 | public SDK · extension points · reference verticals · authoring DX |

**Track 5 wedge — MCP-transport connectors (G7).** Connectors are direct `httpx`
REST clients today. Add an alternative transport so a connector can instead speak
to a vendor's official **MCP server** behind the *same* Protocols — agents
unchanged, config-selectable per connector (`transport: rest | mcp`), reusing the
session's MCP servers (e.g. `~/.codex/config.toml`) for auth. REST stays default;
MCP is opt-in per connector.

### Phases & milestones

| Phase | Tracks | Driver | Milestone |
|---|---|---|---|
| **5 — Comprehension + 1st codegen** | 1 + 2 | human-bootstrap | PKG-grounded, agent-generated feature on this repo, ≥50% acceptance |
| **6 — Trust + PR/CI** | 3 + 4 + harness | mixed | one agent-built feature Confluence→merged CI-green PR through both gates, ≥70% |
| **7 — Generality + adoption** | 5 + 6 | agent-led (fan-out) | a second team/stack self-serve onboards and ships |
| **8 — Hardening + platformization** | 7 + 8 | agent-led | enterprise-grade, multi-tenant; one non-SDLC vertical proves genericity |

### Agent-time

*Model:* ~10 concurrent agents; a moderate backlog item ≈ 3 agent-hours. Phase 5
is human-led (no agents yet to self-build).

| Phase | Items | Agent-compute | Wall-clock |
|---|---:|---:|---|
| 5 | ~6 | low (assistive) | **3–5 weeks** ← sets the clock |
| 6 | ~10 | ~30 agent-h | ~1 week (incl. approvals) |
| 7 | ~21 | ~63 agent-h | ~3–5 days |
| 8 | ~9 | ~27 agent-h | ~3–4 days |
| **Σ** | **~46** | **~120 agent-h** | |

**~12 months → ~6–8 weeks:** ~1 month human bootstrap (Phase 5, the gate) +
~120 agent-hours (~2–3 calendar weeks with fan-out) for everything after.

### Trust / maturity ladder
| Rung | Autonomy | Gated by |
|---|---|---|
| 0 Assistive | Review PRs, draft backlog (today) | — |
| 1 Supervised draft | Generates PRs; humans always merge | Phase 5 |
| 2 Autonomous-for-a-class | Trivial/well-specified merge on green | Phase 6 |
| 3 Autonomous feature | Moderate features → merged PR unattended; humans only at intent + merge gates | Phase 6–7 |

**On rung 0→1.** Each rung widens as verification (Track 3) + the PKG (Track 1)
make it safe — and the rung agents have earned is the rung at which they ship the
next phase.

### North-star metrics
Acceptance rate (≥70% on moderate features by Phase 6) · human-touch per run
(→2, the bookends) · escalation precision · drift caught · time-to-onboard
(<1 day) · cost per feature.

---

## 6. Immediate next steps

*(Done in the 2026-06-12 hardening pass: patch-based editing — **live-proven
by PR #18** · deploy-stub removal · per-run budget enforcement ·
secrets-verifier tuning · stale-branch reconciliation.)*

*(Also done: budget trips now write an `sdlc_budget_exhausted` audit row;
intake split-discipline tightened + pinned by a real-LLM eval; **G2
benchmark built and run — 9/10 accepted** (`scripts/codegen_benchmark.py`,
5 edit + 5 create tickets, production arm). Benchmark run 1 caught a real
prompt bug — `//` comments in the JSON output example taught the model
comment-laden JSON; fixed and pinned, acceptance went 2/10 → 9/10.)*

*(Done in the 2026-06-15 live-onboarding pass: first `--live` run against an
external private repo (AEO-27 → merged PR) drove out the gaps — `doctor`
now bridges `.env`; codegen inherits `ORCHESTRATOR_INTAKE_MODEL`;
private-repo clone/push auth (PAT or GitHub-App token); base repo rebuilds
on source mismatch; Notion + `file://` adapters wired through CLI/SDLC;
console live-poll toggle; `sdlc complete --pr` closes merge→Jira-Done for the
linear path. The `SlackWebhookNotifier`→`raise_approval_request` wiring is
also done (see G13).)*

1. **PKG (G1) × ontomesh — the headline next chapter.** Write the Phase-0
   deliverable `docs/specs/PRODUCT-KNOWLEDGE-GRAPH.md`, then build per §4.4.
   Biggest lever on codegen comprehension depth.
2. **More breadth** (agent-built): a fourth tracker/source adapter
   (Linear/GitLab/ADO, G7), email/webpush delivery (G13), console SSE/push
   (G12). RBAC/multi-tenancy (G11) is the larger prerequisite for per-tenant
   scoping.
3. **Codegen quality follow-ups**: raise the benchmark to multi-file
   create+edit tickets; consider a `max_refine_iterations` bump for features
   that need >3 lint passes.
4. **Merge→Done for the autonomous path**: `sdlc complete` closes the linear
   CLI loop; fold the same transition into the Temporal `merge_prs` activity
   so the gate-approved autonomous merge also moves Jira to Done.

> Build order: comprehension → generation → delivery → breadth →
> platformization. Prove each on this repo before widening to "any team, any project."

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Codegen never reaches useful acceptance | PKG-grounding is the lever; benchmark from Phase 5; humans on merge until proven |
| PKG rots / drifts | Derived-not-authored + provenance + merge-hook freshness + GroundingVerifier |
| Over-ontologizing the PKG | Small task-driven schema; grow only as queries demand |
| Verification false positives | Tune on real repos; calibrated severity; suppress dummy patterns |
| Scope sprawl across 8 tracks | Strict phase milestones; ship a real feature each phase |
| Cost blowout in autonomous loops | Enforce IR budgets early (Track 6.6) |
| Ontomesh pre-1.0 churn | Pin `:3.8.0`; keep it behind the `KnowledgeGraphAdapter` seam |

---

## 8. Document map

| Doc | Role |
|---|---|
| **`README.md`** (this) | **Single source of truth — status + roadmap + current focus** |
| `models.md` | Data-model reference |
| `archive/AI-NATIVE-PLATFORM-PLAN.md` | Full original roadmap (merged into §5 here) |
| `archive/PKG-INTEGRATION-PLAN.md` | Full original PKG plan (merged into §4 here) |
| `archive/STATUS-2026-06-09.md` | Original status snapshot (merged into §1 here) |
| `archive/SDLC-ORCHESTRATOR-PLAN.md` | Original Block A–E plan (delivered) |
| `archive/BLOCK-C-DESIGN.md` | Block C design (built) |
| `archive/LIVE-TESTING.md` | Block A/B live-test notes (done) |
| `../bundle-*.md`, `../MASTER-INDEX-v1.0.md`, `../full-development-tasks.md` | Business/commercial track (separate) |

*Next artifact to write: `docs/specs/PRODUCT-KNOWLEDGE-GRAPH.md` (Phase-0 deliverable).*
