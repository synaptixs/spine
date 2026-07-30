# Plan: two pre-build milestones — Repo Comprehension + Feature Design

Adds two milestones to the SDLC pipeline that run **before code is written**:

1. **M1 — Comprehend the repo** → produce + persist architectural artifacts (knowledge
   graph, memory bank, current-state).
2. **M2 — Feature/Issue Design** → for each issue, produce a *grounded design* (consuming
   M1's knowledge graph) and gate on it before implementation.

Both are **flag-gated and safe-by-default**: when off, the pipeline is byte-for-byte today's.

---

## Pipeline: today → target

**Today** (`SDLCWorkflow.run`, `sdlc/workflows.py:505`):
```
intake → capability-plan → GATE 1 (intents) → create issues
      → per-feature[ workspace → code_plan (discarded) → implement → test → review → PR ]
      → integration → GATE 2 (merge) → merge → consolidate
```

**Target**:
```
intake → capability-plan → [M1] comprehend repo → GATE 1 (intents + comprehension)
      → create issues → [M2] design wave (per issue) → GATE 1.5 (designs)
      → per-feature[ workspace → load approved design → implement (design-driven) → test → review → PR ]
      → integration → GATE 2 (merge) → merge → consolidate
```

Three human checkpoints, in the order a senior engineer would want them:
**what to build (intents) → how to build it (designs) → ship it (merge).**

---

## Milestone 1 — Comprehend the repo

### Where it slots
A new **parent** stage `sdlc_comprehend_repo`, right after `sdlc_profile_and_plan`
(stage 1b, `workflows.py:527`) and before Gate 1. Runs **once per run** (repo-level, not
per-feature). Needs the base repo checkout → `WorkspaceManager.ensure_base_repo()`
(`sdlc/workspace.py:79`) which already exists for read-only consumers.

### What it does (all existing, deterministic, no LLM)
1. `RepoCodeExtractor` / `load_or_extract` (commit-SHA-cached, `pkg/persistence.py`) → the PKG.
2. `build_overview(batch)` (`pkg/overview.py`) → the module-level graph overview.
3. `build_memory_bank(root)` (`knowledge/understand.py:26`) → the 7 `memory-bank/*.md`.
4. `build_current_state(root, lens="developer")` (`knowledge/current_state.py:700`) → report.

### Artifacts persisted
Via the existing `ArtifactStore` (`runtime/artifacts.py`, `put_json`/`put_bytes`), keyed
`run/<sdlc_id>/comprehension/`:
- `knowledge-graph.db` — PKG SQLite export (`export_sqlite`).
- `graph-overview.json` — the bounded module overview (what `/app/graph` renders).
- `memory-bank/*.md` — the 7 comprehension docs.
- `current-state.md` — the developer-lens report.
- `comprehension.json` — manifest: commit SHA, node/edge counts, greenfield flag, artifact refs.

Audit: new action `sdlc_repo_comprehended` (after_json carries the manifest → the UI's
Trace/Audit pages surface + download it; the graph artifact is exactly the `/app/graph` shape).

### Gate decision: **NO new gate**
Comprehension is read-only, deterministic, no external writes → nothing to approve. Instead
its **summary is folded into the Gate 1 payload**, so the human reviews "here's the system
Spine understood" alongside the extracted intents. (One fewer click; more context at the gate.)

### The efficiency win (bonus)
The comprehension artifact ref is threaded into each `FeatureImplementationWorkflow` child.
Codegen grounding (`context_for_spec` → `PKGCodegenGrounder`, `sdlc/grounding.py`) consumes the
**shared** PKG + memory-bank instead of **re-extracting per worktree** as it does today
(`codegen.py:734`). Extract-once-ground-many.

### Repo-write decision (persist choice)
- **Default**: run artifacts only — browsable/downloadable in the UI, **nothing committed**.
- **Opt-in** (`SDLC_COMMIT_MEMORY_BANK=1`): commit `memory-bank/` into the feature PR so future
  runs' grounding reads it from disk (`knowledge/access.py:24` already reads it if present).
  Deferred — not needed for M1's value.

### Cost
Deterministic, no LLM; SHA-cached → near-free on re-runs of the same commit.

---

## Milestone 2 — Feature/Issue Design

### Where it slots
A new **parent design wave** after Gate 1 + issue creation, **before** the implementation
fan-out. Two passes:
1. **Design wave** — fan out `sdlc_design_feature` per issue (design-only, no code), collect
   design artifacts.
2. **Gate 1.5** — one consolidated design gate for the whole run.
3. **Implementation fan-out** — the existing per-feature workflow, but its discarded
   `sdlc_code_plan` step (`workflows.py:126`) is replaced by "load the approved design."

> **Why parent-level, not per-feature?** A single run-level gate ("approve these N designs")
> beats N per-feature gates, and lets a human catch an architectural mistake **before any code
> is written across the whole run**. Trade-off: it splits today's single fan-out into two waves
> (design wave → gate → implement wave). A simpler fallback is in "Alternatives" below.

### How design consumes the knowledge graph (the core mechanic)
For each issue's spec, `sdlc_design_feature`:
1. **Locate impact** — match the spec title + acceptance criteria against PKG node names
   (`FactStore.find`); expand via **blast radius** (`callers_of` / `touches`,
   `pkg/store.py:43,63`) → the impacted symbol/module set.
2. **Pull structure** — from M1's overview: the affected modules, their inter-module
   dependencies, and FK / READS-WRITES edges (data-layer impact).
3. **Pull conventions/domain** — memory-bank `domain-model.md`, `conventions.md`, `glossary.md`.
4. **Design LLM** takes `{spec, impact map, module structure, data-layer edges, conventions}`
   → a **grounded design**:
   - approach / strategy;
   - **files & modules to touch** — real paths from the PKG;
   - **interfaces to add/change** — real signatures/types from the PKG;
   - **data-layer changes** — real entities / FK edges;
   - **risks / blast radius** — who calls what we're changing;
   - **test strategy**.

**Key property:** every design element is anchored to actual code — no hallucinated files or
APIs. Design = `spec × knowledge-graph`.

### Design artifact (per feature)
`design.json` (structured) + `design.md` (human-readable), stored
`run/<sdlc_id>/feature/<issue_key>/design.*`. Audit: `feature_designed` (per feature),
`sdlc_designs_ready` (wave complete).

### Gate decision: **one run-level Design gate (Gate 1.5)**
Raised after all designs are produced. **Configurable** (`SDLC_DESIGN_GATE`, default **on**),
risk-classified like the existing gates (reuses `sdlc_raise_approval_request`, `before_node="designs"`):
- **approve** → implementation fan-out;
- **modify** → edit a design / add clarifications, fed back into the design;
- **reject** → cancel (`sdlc_designs_denied`).
Shows in the **Inbox/Console** exactly like the intent + merge gates.

### Codegen consumes the design
`codegen.implement` (`sdlc/codegen.py`) gets the approved design as a first-class context block
(files-to-touch, interfaces, approach) → codegen becomes **design-driven**, not spec-only. The
deterministic `codegen.plan()` stub (`codegen.py:895`, currently discarded) is retired.

### Cost
One LLM design pass per feature, **before** any code — bounded and gate-controlled.

---

## Cross-cutting

**Config flags** (existing safe-by-default pattern):
| Flag | Default | Effect |
|---|---|---|
| `SDLC_COMPREHEND` | on | Run M1 (cheap, deterministic). |
| `SDLC_DESIGN` | on | Run the M2 design wave. |
| `SDLC_DESIGN_GATE` | on | Raise Gate 1.5 (else designs are produced but not gated). |
| `SDLC_COMMIT_MEMORY_BANK` | off | Commit `memory-bank/` into the PR. |

**UI (already built).** Comprehension + design artifacts render on the run **Trace** timeline
and download from the **Audit** / **Understand** surfaces; the knowledge-graph artifact is the
**/app/graph** view; the **Design gate** appears in the **Inbox/Console** like other gates.

**Data model.** Reuse `ArtifactStore` (`put_json`/`put_bytes`) + audit rows (`after_json` carries
artifact refs). **No new DB tables.** New audit actions: `sdlc_repo_comprehended`,
`feature_designed`, `sdlc_designs_ready`, `sdlc_designs_denied`.

**Backwards compatibility.** Both milestones flag-gated; off → today's pipeline exactly.

**Build order (each shippable on its own):**
1. **M1** stage + artifacts + fold into Gate 1 + feature-child reuse (grounding from the shared
   artifact). Ship behind `SDLC_COMPREHEND`. *(Highest value/effort ratio; unlocks M2.)*
2. **M2 design activity** consuming M1's graph → design artifact → codegen consumes it, **no gate
   yet**. Ship behind `SDLC_DESIGN`.
3. **M2 gate** — restructure to the design wave + Gate 1.5. Ship behind `SDLC_DESIGN_GATE`.

**Alternatives / decisions to confirm:**
- **Design gate placement.** Recommended: run-level wave + one gate (a two-pass restructure).
  Simpler fallback: keep design inside the per-feature child (upgrade `code_plan` → design) with
  **no gate** — much smaller change, but loses the pre-code checkpoint. Recommend the gated version
  for the "design before you build" value the request is really about.
- **Commit memory-bank to the repo?** Default no (run artifacts only); opt-in flag.
- **Design LLM cost/latency** — mitigated by gating and by only running the wave for live /
  multi-feature runs.

**Estimated effort:** M1 ≈ M (mostly wiring existing engines + artifact persistence + grounding
reuse). M2 ≈ M–L (new design activity + the two-wave/gate restructure is the bulk).
