# Catalog-then-compose roadmap

> Status: proposed (under review). Owner-confirmed scope: build Phases 0–3 now.
> This document is the reviewable plan for evolving agent-orchestrator from a
> single fixed pipeline into a system that **assembles the right capabilities
> per project** — without sacrificing its durability/governance guarantees.

## The idea in one line

Instead of running one hardcoded pipeline for every repo, the orchestrator
inspects the project, then **selects and wires capabilities from a governed
catalog** (skills, MCP servers, workflow parameters) — and surfaces that plan at
the existing human gate for approval. Selection from a vetted catalog, never
improvisation.

## Why this matters (robustness)

- **Right tools per project = fewer wrong-context failures.** A Django service
  and a Java library no longer get identical treatment; the agent brings the
  matching conventions, schema grounding, and review depth.
- **Robust by construction, not by trust.** Composition draws from a governed
  catalog; the plan is gated and audited. "More dynamic" does not mean "more
  dangerous" — every run still passes a human-approved, recorded toolkit.
- **Reproducible and explainable.** A deterministic `profile → plan` yields the
  same toolkit for the same project, recorded in the audit log. "Why these
  tools?" has an answer.
- **Graceful degradation.** A missing skill or a down MCP server falls back to
  the base pipeline (onboarding is already best-effort), so composition can only
  *add* capability, never break the floor.
- **Evidence-gated growth, not bloat.** Start from a tiny matrix; expand only
  when a real project exposes a gap — the same discipline that deferred ontomesh.
- **It completes the persona.** A credible engineer sizes up a project and sets
  up their tools. Capability assembly *is* that behavior — moving the product
  from "a pipeline you operate" to "an engineer who configures their workspace."

## Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| What "skill" means | **Internal capability modules** (our own codegen/review/convention bundles) | Self-contained, governable, no external-runtime coupling. External CC/Agent-SDK skills revisited at Phase 5. |
| Build scope now | **Phases 0–3** (deterministic, no ReAct dependency) | Independently valuable; ships a gated "here's the toolkit I'll use" before the heavy brain work. |
| Workflow composition | **Parameterize `SDLCWorkflow`** (flag bundles) | One durable code path, lowest risk. Promote to separate templates only on evidence. |
| Catalog representation | Hybrid: code-registered built-ins + a declarative `capabilities` section | Mirrors what already works (`ENV_GROUPS` + `mcpServers`). |
| Plan gate | Fold into the **existing intent gate** (`sdlc-{id}-0`) | Human already approves specs there; `modify_input` already supports edits. No new gate. |
| Profiler v1 signals | Tiny: languages, framework, has_db, has_migrations, test_runner, task_type | Avoid speculative detection; expand on evidence. |

## Phase overview

| Phase | Goal | Reuses | Exit criteria | Size | Depends on |
|---|---|---|---|---|---|
| **0. Catalog** | Governed registry of capabilities | `ToolContract`, `ENV_GROUPS` pattern | `orchestrator catalog list` shows code + declarative entries | S | — |
| **1. Profiler** | Deterministic `ProjectProfile.from_repo()` | `RepoCodeExtractor` / `PKGCodegenGrounder.from_repo` | `orchestrator profile <repo>` prints a stable profile; fixture-tested | S–M | — |
| **2. Planner** | `plan_capabilities(profile, catalog, intent)` → `CapabilityPlan` | pure function over 0+1 | deterministic plan + per-selection rationale; golden tests | M | 0, 1 |
| **3. Plan at gate** | Render plan at intent gate; human edits; audit | `ApprovalRequestRepo`, gate `sdlc-{id}-0`, `AuditLogRepo` | plan in gate payload; edits flow; decision audited | S–M | 2 |
| **4. Compose / wire** | Apply `workflow_params`; onboard selected MCPs (governed) | `onboard_mcp_tools`, `SDLCWorkflowInput` | feature vs migration runs pick different params/MCPs from one entrypoint | M (partial rides with 0–3) | 3 |
| **5. Skills in an agentic loop** | Codegen/review as skills in a ReAct loop; PKG + MCPs callable mid-task | `LLMClient` seam, `feature_runner` | agent calls a selected skill + queries PKG mid-generation | L | ReAct loop ("Sprint 9") |
| **6. Adaptive assembly** | Planner learns which combos produce clean merges; still catalog-bound + gated | run history / audit | planner adapts selection from outcomes | L | 5 |

## Phases 0–3 in detail (build-now slice)

### Phase 0 — Catalog
- `orchestrator/catalog/models.py` — `Capability(id, kind, applies_to, side_effects, requires)` where
  `kind ∈ {SKILL (internal module), MCP_SERVER (governed onboard ref), WORKFLOW_PARAM (flag bundle)}`;
  `applies_to` is a predicate over the profile/intent.
- `orchestrator/catalog/catalog.py` — `CapabilityCatalog` loads code-registered SKILLs plus a declarative
  `capabilities` section (MCP refs, param bundles). Governance reuses the `ToolContract` model so the
  catalog is not a new trust surface.
- CLI `orchestrator catalog list`.
- **Exit:** catalog enumerates all entries from both sources; unit-tested.

### Phase 1 — Profiler
- `orchestrator/catalog/profile.py` — `ProjectProfile(languages, framework, has_db, has_migrations,
  test_runner, task_type)` + `ProjectProfile.from_repo(root, intent=None)`. Language detection extends the
  existing `RepoCodeExtractor` pass; framework / DB / test-runner are lightweight file heuristics;
  `task_type` derives from the intent.
- CLI `orchestrator profile <repo>`.
- **Exit:** stable, deterministic profile; fixture-tested (python / Django / Java).

### Phase 2 — Planner
- `orchestrator/catalog/planner.py` — `CapabilityPlan(skills, mcp_servers, workflow_params, rationale[])`
  + `plan_capabilities(profile, catalog, intent)`. Rule-based selectors on a **language × task-type**
  matrix; deterministic and reproducible; every selection carries a one-line rationale.
- **Exit:** golden tests per known profile.

### Phase 3 — Plan at the gate
- `feature_runner` / `run_control` compute `profile → plan` and attach it to the intent-gate
  (`sdlc-{id}-0`) payload; `modify_input` lets the human drop/add a capability; the approved plan is
  written to `AuditLogRepo`.
- **Exit:** plan shows in the gate, edits flow through, decision is audited.

### Phase 4 (partial, rides with 0–3)
- Apply approved `workflow_params` to `SDLCWorkflowInput`; onboard the selected MCP servers for that run
  via the existing governed `onboard_mcp_tools`. Skills remain prompt-blocks until Phase 5 turns them into
  loop-callable tools.

## v1 planner matrix (seed — open to change)

| Profile signal | Selected capabilities |
|---|---|
| `python` + `feature` | python-conventions skill, repo-PKG grounding |
| `*` + `migration` | fan-out `workflow_params`, extra adversarial-review stage |
| `repo_has_db` | onboard a DB MCP server for schema grounding |
| `java` + `feature` | java-conventions skill, repo-PKG grounding |
| (fallback) | base pipeline — no extra capabilities |

## Firebreak (applies to every phase)

Select from the vetted catalog; **never** install MCP servers or author workflow
code at runtime. This is the precondition that lets dynamic assembly stay
durable, governed, and auditable.

## Current progress

The substrate the early phases stand on is already shipped — which is why
Phases 0–3 are mostly assembly of existing parts:

- **Comprehension** — `PKGCodegenGrounder.from_repo`, per-target, compounding (~85%).
- **Governed tool plane** — MCP onboarding, `ToolContract`, write-gating, audit, approval policy (~85%).
- **Durable gated execution** — `SDLCWorkflow`, intent + merge gates, `ApprovalRequestRepo`, `modify_input` (~85%).
- **Distribution** — local + remote OAuth plugin, released `0.1.4` (~90%).

Against **catalog-then-compose specifically: ~15–20%** — the foundations exist,
but none of the four new pieces (catalog, profiler, planner, plan-gate) are
built yet. Phases 0–3 need no agentic loop; the payoff compounds at Phase 5 when
skills execute inside the loop, which is the shared milestone with the
ReAct/tool-use work.
