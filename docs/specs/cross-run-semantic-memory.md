# Design: cross-run semantic memory (the experience-true layer)

**Status:** **Phase 1 (read path) implemented.** `MemoryRow` table (`registry/db/models.py`) +
migration `0006_agent_memory` + `MemoryRepo` (`registry/repositories.py`: `add` / `search` /
`record_hit`) + the `recall_memory` `Tool` (`agentic/memory_tools.py: build_memory_tools`).
Retrieval ranks by keyword overlap in Python (portable across SQLite + Postgres; pgvector/ANN
is the Phase 3 swap behind the same `search` signature). **Phase 1b (read path wired into the
live loop) also done** — `recall_memory` + passive priming in `LLMCodegenAdapter`, gated by
`ORCHESTRATOR_SEMANTIC_MEMORY` (default off, mirroring `SDLC_AGENTIC_CODEGEN`). **Phases 2 + 2b
done** — `knowledge/consolidate.py: consolidate_run` distills a run bundle's governance
episodes into memories (insert/reinforce), wired into `SDLCWorkflow` as a post-merge
`sdlc_consolidate_memory` activity (gated, no-op-safe). **Phase 3 done** — confidence decay +
prune-below-floor on every consolidation, plus tool-error episode widening; pgvector ANN
deliberately deferred behind the `search` seam. The full grow → decay → prune lifecycle is
closed on the Temporal path. Builds on shipped seams:
`LoopResult.trace` / `StepRecord` (`agentic/loop.py`), `build_run_bundle` (`agentic/export.py`),
`RecordingLLMClient` (`core/llm/recording.py`), `AuditLogRow` (`registry/db/models.py`),
and the memory-bank grounding slot (`knowledge/access.py`).
**Goal:** turn the episodic record of what the agent *did* into reusable **semantic
memory** the agent *consults* on the next run — closing the loop from amnesiac-between-runs
to learns-across-runs, without violating the locked **derived-not-authored** principle.

## Reframe
Today memory is two disconnected halves:
- **Episodic** — `LoopResult.trace` + the run bundle. Rich, but **write-only**: nothing
  reads a past run when a new one starts.
- **Semantic** — `memory-bank/*.md` (the [memory bank](project-comprehension-memory-bank.md)).
  Read and grounding-injected, but **code-derived only** — it learns nothing from experience.

This adds the missing middle: a **consolidation loop** that abstracts episodic traces into
durable, cited semantic facts, and an **in-loop retrieval tool** that pulls them back. The
memory bank stays the *code-true* layer; semantic memory is the *experience-true* layer on
top. Grounding becomes code facts **+** learned facts, both cited.

> This is the PRISM verbatim-vs-abstraction tension resolved in code: keep the verbatim
> episode (the run bundle, already persisted), but consult only the **abstraction** —
> bounded, deduped, decaying. It won't drown Funes-style because consolidation merges and
> decay prunes.

## Locked decisions
1. **Derived, not authored.** Every memory must cite its source run(s) in `evidence`. No
   free-floating model belief — a memory with no surviving evidence is prunable. Same
   discipline as the memory bank (canonical facts ↔ rendered view).
2. **The episode is canonical; the memory is the abstraction.** Run bundles
   (`build_run_bundle`) remain the permanent record. Semantic memory is a lossy, queryable
   index *over* them — regenerable in principle, never the system of record.
3. **Consolidation is itself budgeted + audited.** The reflector runs under
   `RecordingLLMClient` with `stage("memory_consolidation")` and a `RunBudget`, and writes
   an `AuditLogRow`. Learning is governed like every other agent action.
4. **Retrieval is governed.** `recall_memory` is a `Policy`-gated `Tool` like any other; its
   calls appear in the trace. No silent memory access.
5. **Highest-signal episodes first.** Human reject/modify decisions are the strongest
   learning signal (a human corrected the agent) and the lowest volume — Phase 2 consolidates
   only those before widening.

## Data model
New table alongside `AuditLogRow` in `registry/db/models.py`:

| Column | Type | Meaning |
|---|---|---|
| `id` | uuid | PK |
| `tenant_id` | str | reuse existing tenant scoping |
| `repo_key` | str | which project this memory is about |
| `kind` | str | `convention` \| `pitfall` \| `decision` \| `fix-pattern` |
| `scope` | str | `repo` (project-specific) \| `global` (cross-project) |
| `statement` | str | the consolidated fact, one sentence |
| `evidence` | json | `{run_ids: [...], trace_steps: [...], files: [...]}` |
| `embedding` | vector | pgvector, for ANN retrieval |
| `confidence` | float | starts 0.5; reinforced on dedup-hit, decayed on disuse |
| `hits` | int | times retrieved-and-helped (feedback loop) |
| `created_at` / `last_used_at` / `trace_id` | — | mirror `AuditLogRow` |

Three tiers map onto existing structures:

| Tier | Lives in | Status |
|---|---|---|
| **Working** | `_State` (`agentic/loop.py`) | exists ✓ — per-step, discarded at run end |
| **Episodic** | `LoopResult.trace` + run bundle | exists ✓ — append-only, never re-read |
| **Semantic** | **new `MemoryRow`** | **new** — consolidated *from* episodic, *read by* the loop |

## Architecture (new `knowledge/consolidate.py` + memory tool)
### A. Consolidation loop (write path)
A Temporal **activity run after each run completes** — hook into `OrchestratorWorkflow`
after the merge gate, beside the existing `record_audit` call.

```
consolidate_run(run_bundle, repo_key, tenant_id):
  1. Select salient episodes from bundle.trace:
       policy_blocks          → candidate "pitfall"
       replan events          → candidate "decision" (what failed, what worked)
       test-failure → green   → candidate "fix-pattern"
       human reject / modify  → candidate "convention"  (strongest signal)
  2. One reflector LLM call per episode → candidate statement, REQUIRED to quote evidence.
  3. Dedup vs existing memories by embedding cosine sim > 0.9:
       near-duplicate → reinforce (confidence += δ, append run_id to evidence)
       novel          → insert (confidence = 0.5)
  4. Decay: memories not hit in N runs lose confidence; below floor → soft-delete.
```

### B. Retrieval (read path)
A new `Tool` in `agentic/tools.py`:

```
recall_memory(query: str, kind: str | None = None) -> str
  embed query → pgvector ANN scoped by (tenant_id, repo_key, scope)
  return top-k statements WITH evidence run_ids, confidence-ranked
  on use: hits += 1, last_used_at = now   (closes the feedback loop)
```

Two injection points, mirroring how `memory_bank_grounding` is used today
(`knowledge/access.py`):
1. **Passive priming** — at run start the planner prepends the top-N highest-confidence
   memories for this repo to the system prompt (same slot the memory bank fills). Cheap,
   always-on.
2. **Active recall** — the `recall_memory` tool lets the agent query mid-loop on unfamiliar
   ground. `Policy`-gated, appears in the trace.

## Consolidation vs the memory bank (two layers, one grounding block)
| | Memory bank (`memory-bank/*.md`) | Semantic memory (`MemoryRow`) |
|---|---|---|
| Source | code + docs (deterministic render) | run episodes (LLM-consolidated) |
| Truth | code-true | experience-true |
| Refresh | `understand --refresh` | consolidation activity per run |
| Grounding | `memory_bank_grounding` | passive priming + `recall_memory` |

Grounding at run start = code facts (bank) **+** learned facts (semantic), both cited.

## Phasing
- **Phase 1 — read path only.** ✅ **Done.** `MemoryRow` + migration + `MemoryRepo`
  (`add`/`search`/`record_hit`) + `recall_memory` tool, over a **manually-seeded** memory set.
  Retrieval is keyword-overlap ranked in Python (portable, no pgvector); the `search`
  signature is the contract an embedding/ANN backend swaps in behind. Tested on in-memory
  SQLite; migration round-trips on Postgres.
- **Phase 1b — wire into the loop.** ✅ **Done.** `LLMCodegenAdapter` takes
  `memory_factory` + `memory_repo_key`; when set **and** `ORCHESTRATOR_SEMANTIC_MEMORY` is on
  (`_memory_enabled()`), `_agentic_tools` adds `recall_memory` and `_memory_priming` prepends
  the top-N learned facts to the implement task (relevance-ranked, advisory). The SDLC worker
  (`sdlc/worker.py: _build_codegen`) threads the DB session factory + `SDLC_REPO_URL`. Inert
  unless the flag is set; priming failures are swallowed (advisory, never fatal).
- **Phase 2 — write path, narrow.** ✅ **Done (engine).** `knowledge/consolidate.py:
  consolidate_run(bundle, repo_key, session, llm, model, run_id, …)` selects governance
  episodes from a run bundle (`policy_blocks`: human **reject** → `convention`, policy
  **deny**/`require_approval` → `pitfall`; identical blocks collapsed), runs one reflector LLM
  call per episode (SKIP drops no-lesson episodes), dedups by token-Jaccard ≥ 0.6, and either
  reinforces (`MemoryRepo.reinforce`: confidence += 0.1, append `run_id` to evidence) or
  inserts at confidence 0.5. Never raises into the caller. Tested deterministically
  (MockLLM + SQLite). *Replan/test-fix episodes (decision/fix-pattern) deferred — they need
  richer trace plumbing than the bundle currently carries.*
- **Phase 2b — wire the hook.** ✅ **Done.** `SDLCWorkflow.run` calls a new
  `sdlc_consolidate_memory` activity **after `sdlc_merge_prs` passes** (the one unambiguous
  completion point). The agentic loop's `policy_blocks` are surfaced out of codegen
  (`ImplementOutcome.policy_blocks` → `sdlc_implement` result → `FeatureWorkflowResult` →
  parent), unioned across merged features into one bundle, and consolidated under the SDLC
  run's id. The activity no-ops (never fails the merged run) unless
  `ORCHESTRATOR_SEMANTIC_MEMORY` is on, an LLM is wired into `SDLCDeps`, and `SDLC_REPO_URL`
  resolves. *Linear `feature_runner` path not wired — it discards the `LoopResult`; Temporal
  is the live path.*
- **Phase 3 — widen + decay.** ✅ **Done (decay + widening).** `MemoryRepo.decay` ages out
  memories untouched since a cutoff (confidence −0.05) and prunes below floor 0.15;
  `consolidate_run` runs it on every consolidation (tying decay to the per-merge cadence —
  "unused for a while" is the prune signal), and `reinforce`/`record_hit` refresh
  `last_used_at` so active memories are spared. Hard-delete below floor is safe (a memory is a
  lossy, regenerable index over the canonical run bundles). Episode widening: `_select_episodes`
  now also mines **tool-error** observations from the trace → `pitfall`. *Decay runs as a step
  in the consolidation activity rather than a separate `ApprovalTimeoutSweepWorkflow`-style cron
  — the per-merge cadence is the natural clock and needs no extra workflow.*
  - **pgvector ANN — deliberately deferred, not built.** Keyword-overlap retrieval stays the
    default: it is portable (SQLite tests + Postgres prod, no extension), dependency-free, and
    adequate for the bounded per-repo memory set. pgvector would add a vector column + migration,
    an embeddings provider (new dep + per-write API calls), and dialect-specific ANN SQL that
    breaks the portable test path — cost not yet justified. The `MemoryRepo.search` signature is
    the seam an embedding backend swaps in behind when scale warrants it.

## Flag & dependency
- `ORCHESTRATOR_SEMANTIC_MEMORY=0` default-off (conservative-by-default convention).
- One new dependency: **pgvector**. Deferrable — Phase 1 can run on Postgres FTS.

## Tests
- Consolidation: golden run bundle → asserted `MemoryRow` set (deterministic with
  `MockLLMClient` reflector script); dedup reinforces rather than duplicates; decay
  soft-deletes below floor.
- Retrieval: seeded memories → `recall_memory` returns confidence-ranked, evidence-bearing
  results; `hits`/`last_used_at` updated; Policy `deny` blocks and records a trace entry.
- Grounding: passive priming injects top-N into the system prompt without exceeding budget.

## Ties to observability
`evidence.trace_steps` deep-links a memory to the [OTel span](live-observability-otel.md)
for the step that produced it (via shared `trace_id` + step index): a recalled memory that
proves wrong links straight back to its origin run for pruning. Derived, cited, inspectable.
