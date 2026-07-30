# Product Knowledge Graph — Phase-0 Spike Report & Ingestion Contract

**Date:** 2026-06-10 · **Status:** Phase-0 spike complete (deliverable 0.4)
**Question answered:** *can code facts be coerced into ontomesh's relational
ingestion, or does it need a first-class code-fact module?*
**Answer: coercion is viable — via a kind-per-table projection. No ontomesh
code changes needed for the first integration.**

---

## 1. What the spike did

- Cloned `synaptixs/ontomesh` (depth 1) to `/tmp/ontomesh-spike`; installed
  **requirements-core only** (~50 MB, no compile) into a fresh venv.
- Ran the full pipeline headlessly: `toolkit.py --db db/enterprise.db --out …`
  → **complete in 4.8 s**.
- Inspected the outputs and the annotation control plane (`db/schema.sql`).

## 2. What ontomesh produced (evidence)

| Artifact | Observed |
|---|---|
| `ontology/*.ttl` | **59 OWL classes** inferred from the enterprise DB (+ industry packs, TMF hierarchy) |
| `shapes/*.ttl` | SHACL NodeShapes incl. **agent acceptance gates** (e.g. "Observation must carry `prov:wasGeneratedBy`") — directly reusable as our GroundingVerifier input |
| **A-box** | Named individuals present (`enterprise.ttl`, `drift.ttl`) — rows do materialise as individuals, not just schema→classes |
| `mapping/*.csv` | logical↔physical map, semantic-loss report, orphan candidates |
| Reports | HTML toolkit/compliance/retrieval summaries |

Headless operation confirmed: the core pipeline is a CLI over a DB file —
no Flask/wizard/LLM tiers needed for ingestion.

## 3. The ingestion contract (decided)

Ontomesh ingests **a relational schema + the `ontology_metadata` annotation
table** (per TABLE/COLUMN rows: `semantic_type`, labels, sensitivity tier,
and OWL axiom signals — transitive/symmetric/functional, `inverse_of`,
disjoint groups, `owl:hasKey`). Its schema inference turns **tables into
classes and FK columns into object properties**.

### The coercion: kind-per-table projection

Project the PKG `FactBatch` into a small SQLite/Postgres schema where **each
NodeKind is a table and each EdgeKind is an FK relation**:

```
modules(id, name, language, file, line, end_line)
types(id, name, module_id → modules, file, line, end_line)
functions(id, name, parent_type_id → types, module_id → modules, file, line, end_line)
calls(caller_id → functions, callee_id → functions, file, line)
imports(module_id → modules, target_id → modules, file, line)
```

plus `ontology_metadata` rows annotating each table/column
(`semantic_type='Function'`, `is_transitive=1` on contains-style relations,
sensitivity tiers, SKOS labels).

**Why this shape:** ontomesh then infers exactly the ontology we want —
`:Module`/`:Type`/`:Function` as OWL classes, `:calls`/`:imports`/`:contains`
as object properties, **each code symbol as an individual** with provenance
columns carried as datatype properties. The fact model's semantics survive
intact; nothing is flattened into a generic "node" class.

### What we deliberately do NOT do
- No generic `pkg_nodes`/`pkg_edges` dump (would model our *storage* schema,
  not the code domain — classes would be "PkgNode", semantically lossy).
- No ontomesh source changes (pre-1.0; stay behind the pinned black box).

## 3b. Round-trip VERIFIED (2026-06-10)

The exporter (`orchestrator pkg export`, `pkg/export.py`) was built and run
end-to-end on this repo: **305 modules, 287 types, 771 functions, 862 calls,
811 imports** → kind-per-table SQLite → ontomesh pipeline **4.3 s** →

- ✅ `:Module`, `:Type`, `:Function` emitted as **OWL classes**; FK columns
  became functional object properties; `:Calls`/`:Imports` reified as classes
  (composite-key tables) — useful, since call sites carry `file:line`.
- ✅ **SHACL shapes generated over our classes** (`sh:targetClass :Calls`, …) —
  the GroundingVerifier input exists.
- ⚠️ **No per-symbol individuals** (A-box) materialised from arbitrary rows —
  risk #1 below confirmed. Resolution per the fallback: **T-box + SHACL from
  ontomesh; instance-level queries stay on our own FactStore** (which is
  already the agents' retrieval surface).

One contract detail learned: ontomesh's `setup_db` re-runs its full
`schema.sql`, so the exporter must emit `ontology_metadata` with ontomesh's
**exact column set** (it does now).

## 4. Risks / open items for Phase 2

1. **A-box scale** — individuals materialise, but the demo is hundreds of
   rows; this repo's PKG is ~2.5k nodes / 3.6k edges and real products are
   10–100×. Verify materialisation + SHACL runtime at that scale before
   relying on it per-merge. (Fallback: T-box from ontomesh, A-box queried
   from our own store.)
2. **Retrieval surface** — the headless core emits **files** (Turtle/JSON-LD);
   the REST/Ask/SPARQL console lives in the wizard/runtime tiers (or the
   Docker image). For Phase 2 either (a) load the emitted TTL into our own
   rdflib/SPARQL store, or (b) run the pinned container for its API. Decide
   by latency needs.
3. **Refresh cadence** — pipeline is 5 s on a small DB; per-merge re-runs look
   feasible, keyed by the same commit-SHA cache as `pkg.persistence`.

## 5. Updated Phase-2 plan impact

- The conditional ontomesh work-items (first-class code-ingestion module)
  are **not needed** for the first integration → ontomesh share of Phase 2
  drops toward the ~10% floor projected in the plan.
- New orchestrator-side work item: **`FactBatch → kind-per-table SQLite`
  exporter** (small; the JSON persistence layer already serialises
  everything required) + `ontology_metadata` seeding.

---

*Spike artifacts: `/tmp/ontomesh-spike/out/` (reports, ontology, shapes).
Companion to `README.md` §4. Supersedes Phase-0 open questions 1–2; question
3 (pgvector vs FAISS) deferred to embedding work; question 4 (Postgres
topology) deferred to the exporter implementation.*
