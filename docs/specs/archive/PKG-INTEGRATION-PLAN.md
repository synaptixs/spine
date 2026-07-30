# PKG Integration Plan — Orchestrator × Ontomesh

**Date:** 2026-06-09
**Goal:** Close Gap **G1** (a grounded model of the existing system) by building a
**Product Knowledge Graph (PKG)** from any repo's code + docs + data, and feeding
it to the SDLC agents.
**Posture (decided):** **Hybrid** — integrate ontomesh as a pinned black box
first to prove the round-trip, then add minimal ontomesh changes only where the
relational-coercion path proves too lossy.
**Headline split:** **~75% orchestrator · ~25% ontomesh**, phased so ontomesh
work only starts after Phase 1 proves what's actually needed.

---

## 1. Division of labor — the rule

| Concern | Repo | Why |
|---|---|---|
| Reading source code → facts (AST, imports, ORM, routes, tests) | **Orchestrator** | Ontomesh doesn't parse code; this is the bridge |
| Adapter seam + REST/SPARQL client | **Orchestrator** | Consumer owns its client |
| Wiring PKG retrieval into agents + verifier chain | **Orchestrator** | These are orchestrator subsystems |
| Ontology / SHACL / GraphRAG / reasoning / SPARQL | **Ontomesh** | Its core competency — do not duplicate |
| Code-fact ingestion *path* | **Both** | Black-box (coerce to relational) first → first-class module only if lossy |
| pgvector backend, stable retrieval API | **Ontomesh** | Currently scaffolded / wizard-oriented |

**Default bias:** anything that *can* be done by shaping data on the orchestrator
side is done there. Ontomesh changes are a deliberate, justified exception.

---

## 2. Greenfield vs brownfield — one PKG, two seeds

Greenfield and brownfield are **not two pipelines** — they are the same
PKG-grounded feature loop seeded two different ways. The steady state is
identical; only the *bootstrap* differs, and greenfield continuously **matures
into** brownfield as features land.

| | **Brownfield** (existing product) | **Greenfield** (net-new) |
|---|---|---|
| **Seed source** | *Extraction* — code + docs + data → facts | *Modeling* — product brief → intended ontology |
| **Flow direction** | reality → PKG (conform to what **is**) | intent → PKG → generation → PKG (conform to what's **intended**) |
| **Ontomesh path** | schema / log / **code** ingestion | the **wizard** (Domain→Entities→Events→Rules) + starter industries |
| **Conventions** | *learned* from the repo (G8) | *declared* up front, then accumulate |
| **GroundingVerifier asks** | "does this change break an existing invariant?" (**consistency**) | "does this match the modeled ontology + conventions so far?" (**conformance**) |

**One verifier, distinguished by provenance.** The GroundingVerifier is the same
component in both modes — it reads the PKG. Ontomesh's **PROV-O lineage**
(`wasDerivedFrom`) marks each fact as *extracted-from-source* vs
*derived-from-a-design-model*, which is exactly what lets one verifier enforce
"consistency" for brownfield and "conformance" for greenfield.

**Greenfield becomes brownfield.** The first feature establishes conventions from
nothing; every merged feature emits its facts back into the PKG (the same
incremental re-extraction hook as Phase 3). After N features the graph is dense
enough that the brownfield loop simply takes over — no mode switch, declared
conventions harden into learned ones.

```
GREENFIELD                          BROWNFIELD
brief → ontomesh wizard → seed PKG  existing repo → extract → seed PKG
   │                                       │
   └──────────► feature loop ◄─────────────┘   (identical from here on)
        intent → retrieve PKG → codegen → GroundingVerifier → merge → re-ingest
                                  (every merge feeds the PKG; greenfield densifies into brownfield)
```

**Repo-split impact:** greenfield adds *almost no new ontomesh work* — it reuses
the **existing wizard** (driven via the API from the product brief instead of by
hand). The new build is orchestrator-side onboarding: mode detection, a scaffold
generator, and the declared-vs-learned convention model (just more PKG facts).
This is captured as **Phase B (Bootstrap)** below.

---

## 3. Phased plan with per-repo split

### Phase 0 — Contract & spike *(1–2 wks)*
**Split: 80% orchestrator · 20% ontomesh (investigation only)**

| # | Task | Repo |
|---|---|---|
| 0.1 | Stand up ontomesh `:3.8.0` sidecar in `compose.yml`; `ONTOMESH_DB_URL` → our Postgres (separate schema) | Orch |
| 0.2 | Run `toolkit.py` against a tiny seed DB; capture the OWL/SHACL/JSON-LD outputs + REST/SPARQL responses | Orch |
| 0.3 | **Decide the fact-ingestion contract**: can code facts be coerced into `ontology_metadata` + relational rows, or is a new path needed? | Both |
| 0.4 | Document the contract in `PRODUCT-KNOWLEDGE-GRAPH.md` (companion spec) | Orch |
| 0.5 | Confirm ontomesh's programmatic retrieval surface (Ask/SPARQL/`/api/ontologies`) is stable enough to depend on; log gaps | Ontomesh (assess) |

**Exit:** a documented, version-pinned ingestion + retrieval contract. Phase 0
output decides how much ontomesh work Phases 2–3 actually need.

---

### Phase B — Bootstrap: greenfield vs brownfield onboarding *(2–3 wks, parallel with Phase 1)*
**Split: ~90% orchestrator · ~10% ontomesh (drive existing wizard)**

This is where the two field types diverge; everything after converges on the
shared feature loop.

| # | Task | Repo | Mode |
|---|---|---|---|
| B.1 | `orchestrator init` — **mode detection** (empty vs populated repo) + project manifest | Orch | both |
| B.2 | **Brownfield onboarding** — drive the full extraction crawl (Phase 1 extractor → ingest), produce a baseline drift report | Orch | brownfield |
| B.3 | **Convention learning** — infer branching, CODEOWNERS, layout, naming from the repo → PKG facts (G8) | Orch | brownfield |
| B.4 | **Greenfield seeding** — drive the ontomesh **wizard** from the product brief → intended ontology (seed PKG) | Orch + Ontomesh (API) | greenfield |
| B.5 | **Scaffold generator** — project skeleton, stack choice, declared conventions doc → first commit | Orch | greenfield |
| B.6 | Mark fact **provenance** (`extracted` vs `modeled`) so the GroundingVerifier can switch consistency ↔ conformance | Orch + Ontomesh (PROV-O) | both |
| B.7 | **Maturity handover** — once the PKG passes a density threshold, greenfield projects flip declared→learned conventions automatically | Orch | greenfield→brownfield |

**Exit:** any project — empty or existing — can be onboarded to a seeded PKG and
enter the shared feature loop. Greenfield reuses the existing wizard (no new
ontomesh ingestion module); brownfield reuses the Phase 1 extractor.

---

### Phase 1 — Repo-code extractor (Track 1.1) *(3–4 wks)*
**Split: 100% orchestrator · 0% ontomesh**

| # | Task | Repo |
|---|---|---|
| 1.1 | `RepoCodeExtractor` — Python AST: modules, classes, functions, call graph | Orch |
| 1.2 | Import/dependency graph + `file:line` provenance on every node/edge | Orch |
| 1.3 | ORM models → data entities; FastAPI/route decorators → endpoints | Orch |
| 1.4 | Test↔code links (which tests cover which symbols) | Orch |
| 1.5 | **Fact serializer** — emit nodes/edges in the Phase-0 contract shape (relational rows + annotation rows, or JSON-LD) | Orch |
| 1.6 | CLI: `orchestrator pkg extract <repo>` (dry-run prints fact counts) | Orch |

**Exit:** point at any Python repo → a stream of provenanced code facts in the
agreed shape. Dogfood on this repo. *No ontomesh dependency yet.*

---

### Phase 2 — Ingestion & round-trip (Track 1.2 + 1.3) *(2–3 wks)*
**Split: ~60% orchestrator · ~40% ontomesh** *(ontomesh % only spent if 0.3 found coercion lossy)*

| # | Task | Repo |
|---|---|---|
| 2.1 | `KnowledgeGraphAdapter` Protocol (`ingest_facts`, `retrieve`) + ontomesh REST impl | Orch |
| 2.2 | Push code facts via the Phase-0 contract; trigger ontomesh ontology build | Orch |
| 2.3 | Reconcile doc-derived semantics (existing Confluence adapter) against code anchors → drift findings | Orch |
| 2.4 | **[only if needed]** First-class **code-fact ingestion module** in ontomesh (vs coercion) | **Ontomesh** |
| 2.5 | **[only if needed]** Finish **pgvector** backend so embeddings share our Postgres | **Ontomesh** |
| 2.6 | **[only if needed]** Stable programmatic **retrieval endpoint** for task-scoped subgraph queries | **Ontomesh** |

**Exit:** `extract → ingest → retrieve` works end to end on this repo; an agent
can ask "what exists / what does this touch / what are the invariants" and get a
grounded, cited answer.

---

### Phase 3 — Grounding the agents (Track 1.4 + 2.1) *(2–3 wks)*
**Split: ~85% orchestrator · ~15% ontomesh**

| # | Task | Repo |
|---|---|---|
| 3.1 | Retrieval-augment `CodegenAdapter` — inject PKG subgraph into codegen prompts | Orch |
| 3.2 | Retrieval-augment reviewer + gap-analyzer | Orch |
| 3.3 | **GroundingVerifier** in the verifier chain — calls ontomesh SHACL; stale edge / unbound claim = finding | Orch |
| 3.4 | Merge-hook **incremental re-extraction** so the PKG is a CI artifact, not a one-time crawl | Orch |
| 3.5 | **[only if needed]** Expose SHACL validation results + PROV-O lineage in a verifier-friendly response shape | **Ontomesh** |

**Exit:** generated/reviewed code is grounded in the PKG; SHACL invariants gate
merges; the graph stays fresh on every merge.

---

### Phase 4 — Hardening & second language *(later, Track 1.5)*
**Split: ~70% orchestrator · ~30% ontomesh**

| # | Task | Repo |
|---|---|---|
| 4.1 | Second-language extractor (TypeScript or Go) | Orch |
| 4.2 | Schema growth driven by real agent queries (avoid over-ontologizing) | Both |
| 4.3 | Multi-repo / monorepo fact namespacing | Orch |
| 4.4 | Performance: incremental SHACL + retrieval latency at repo scale | Ontomesh |

---

## 4. Rolled-up effort split

| Phase | Orchestrator | Ontomesh | Notes |
|---|---:|---:|---|
| 0 — Contract & spike | 80% | 20% | ontomesh = assessment only |
| B — Bootstrap (green/brown) | 90% | 10% | greenfield drives the existing wizard |
| 1 — Code extractor | 100% | 0% | pure orchestrator |
| 2 — Ingestion & round-trip | 60% | 40% | ontomesh % conditional on Phase 0 |
| 3 — Grounding the agents | 85% | 15% | mostly orchestrator wiring |
| 4 — Hardening / 2nd language | 70% | 30% | later |
| **Weighted total** | **~75%** | **~25%** | matches the Hybrid posture |

**Reading the split:** the orchestrator carries the bulk because the missing
capability — *turning code into facts* and *consuming a grounded graph* — lives
on our side. Ontomesh work is concentrated where the black-box path is genuinely
lossy (a real code-ingestion module, pgvector, a stable retrieval endpoint) and
is **gated behind Phase 0's findings** — if coercion-to-relational works well,
the ontomesh share drops toward 10%.

---

## 5. Critical path & dependencies

```
Phase 0 (contract) ──► Phase 2 (ingest/retrieve) ──► Phase 3 (ground agents) ──► Phase 4
        │                    ▲
        ├─► Phase 1 (extractor) ──┘        (Phase 1 needs no ontomesh)
        └─► Phase B (bootstrap)            (brownfield half uses Phase 1; greenfield half uses the wizard)
```

- **Phase 1 and Phase B run in parallel** — both are unblocked once Phase 0 sets
  the contract; Phase B's brownfield half consumes the Phase 1 extractor, its
  greenfield half consumes the existing ontomesh wizard.
- **Greenfield needs no new ontomesh ingestion** — it reuses the wizard, so the
  greenfield path can light up before the brownfield extractor is fully built.
- **Ontomesh changes are deferred** until Phase 2, and only the subset Phase 0
  proves necessary. This keeps ontomesh pinned and stable for as long as possible.

---

## 6. Decision log

- **Posture:** Hybrid (black-box first, extend only where lossy).
- **Coupling:** sidecar via REST/SPARQL behind `KnowledgeGraphAdapter` (not
  library import) — isolates ontomesh's pre-1.0 churn.
- **Code extraction:** owned by the orchestrator as a "read any existing repo"
  feature.
- **Greenfield vs brownfield:** one PKG + one feature loop, two seeds (model vs
  extract); greenfield reuses the existing ontomesh wizard and matures into
  brownfield as features land. Onboarding (`init`, scaffold, convention model) is
  orchestrator-side (Phase B).
- **Shared Postgres:** ontomesh points at our DB via `ONTOMESH_DB_URL` (separate
  schema); migration ownership TBD in Phase 0.

---

## 7. Open questions to resolve in Phase 0

1. Can code facts be coerced into `ontology_metadata` + relational rows without
   meaningful loss, or is a first-class code-ingestion module required? *(This
   single answer sets the real ontomesh %.)*
2. Is ontomesh's retrieval surface (Ask / SPARQL / `/api/ontologies`) stable and
   scriptable enough to depend on headlessly, or do we need a dedicated endpoint?
3. pgvector: finish it in ontomesh for a shared store, or run ontomesh's vector
   backend (FAISS/Chroma) separately?
4. Postgres topology: shared DB + separate schema vs separate database.

---

*Companion to `STATUS-2026-06-09.md` and `AI-NATIVE-PLATFORM-PLAN.md`. Next
artifact: `docs/specs/PRODUCT-KNOWLEDGE-GRAPH.md` (Phase 0 deliverable 0.4).*
