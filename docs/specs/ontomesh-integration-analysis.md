# Ontomesh × synaptixs-spine — integration analysis

**Status:** Analysis for decision — nothing here is built.
**Date:** 2026-07-21 · spine `v3.6.0` (public) · ontomesh `v3.9.0`
**Decision owner:** you. Each recommendation (O1…O7) has an **ACCEPT / DEFER / REJECT** box.

> **Method.** Both repos were read directly. Spine = the `orchestrator` package
> (`src/orchestrator/spine/*`, `pkg/rdf.py`, `pkg/export.py`, `OPERATIONS.md`,
> `docs/specs/tri-repo-integration.md`). Ontomesh = `synaptixs/ontomesh@v3.9.0`
> (README, `wizard/app.py`, `wizard/importer.py`, `runtime/client.py`). Note: the
> ontomesh repo is **private** (reachable via auth), not public as briefed.

---

## 1. TL;DR

Spine and ontomesh are **complementary, not overlapping**: spine's Program Knowledge
Graph (PKG) is **code-true** (what the code *is*); ontomesh is **domain-true** (what the
*business* means — a mined OWL/SHACL ontology over the org's data). The intended win is
**codegen and analysis grounded in both** — structurally correct *and* semantically correct
for the domain.

Reality today:

- **One seam is already wired but inert.** Spine's `OntomeshHttpClient` calls ontomesh's
  `POST /api/search` and composes the answer into codegen grounding — but only when
  `SPINE_ONTOMESH_URL` + `SPINE_ONTOMESH_FLAVOR` are set, which is **off by default and has
  never been run against a real ontology** (the design spec literally calls the loop
  "slideware until the vignette works").
- **The two graphs never meet.** Spine builds a rich PKG and even *projects it* to RDF
  individuals (`pkg/rdf.py`) and an ontomesh-shaped SQLite schema (`pkg/export.py`) — but
  **nothing ever sends either to ontomesh**, and **ontomesh has no endpoint to receive
  facts/RDF** (its `/api/import` takes SQL DDL / JSON schema only). So the code↔domain join
  that the whole "semantic spine" vision rests on **has no data bridge**.
- **Transport mismatch.** Ontomesh is a Flask HTTP service; spine's agentic codegen loop
  consumes **MCP tools**. There's no MCP wrapper, so the agent can't *query* ontomesh
  mid-task — it only gets one pre-seeded grounding block at plan time.

**The productivity upside is real but gated on setup, not just code.** The cheapest win
(domain-grounded codegen) is *already built* and needs an operational stand-up + one
contract check. The deeper wins (a bidirectional code↔domain graph, domain-aware RCA/impact,
governance/lineage) need new bridging work.

---

## 2. What each side actually is

**Ontomesh** — "the ontology mesh for GraphRAG." A Python/Flask service (port **5051**,
`ghcr.io/synaptixs/ontomesh`) that **mines an OWL/SHACL ontology from your data** (SQL
schemas, JSON, or log templates), then serves **ontology-grounded reasoning search** over a
**relational DB** (SQLite/Postgres). The ontology is an *annotation overlay* on the DB;
RDF triples are **materialized on demand per query**, not persisted. Real, active service
(v3.9.0, ~1000 tests), self-labeled preview/pre-1.0.

Relevant surfaces:
- `POST /api/search` `{question, flavor, db?, max_tier?, k?, materialize?}` → a
  `ReasonedAnswer` (answer, cited SQL, inferred triples, subgraph, trace). **← spine's seam.**
- `POST /api/sparql` `{query, triples?}` — SPARQL over a **transient** subgraph only.
- `POST /api/import` — **SQL DDL / JSON schema only** (bootstraps the ontology *model*);
  **no RDF/triple/facts ingest**.
- In-process SDK `RuntimeClient.ask(question, flavor)`.
- **No awareness of "spine"** anywhere; `SPINE_ONTOMESH_URL` is purely a spine-side name.

**Spine** — the `orchestrator` platform. Builds the **PKG** (code-true facts across 6
languages) and grounds codegen/design/RCA in it. Ships a whole `spine/` package that is the
*designed* semantic backbone (EntityKey join, mapper, lineage, benchmark) — explicitly
decoupled: `"Nothing here depends on ontomesh or infodrift being wired."`

---

## 3. Current integration state (WIRED / STUBBED / DEFERRED)

| Surface | State | Evidence |
|---|---|---|
| `POST /api/search` client + `ReasonedAnswer` parse | **WIRED, inert** | `spine/ontomesh_client.py:86-104` |
| Seam 1 grounding composed into codegen (worker + linear runner) | **WIRED, inert** | `sdlc/worker.py:106-111`, `sdlc/feature_runner.py:342-344` |
| Env gating (`SPINE_ONTOMESH_URL`/`_FLAVOR`/`_MIN_CONFIDENCE`) | **WIRED** | `spine/grounder.py:91-108` |
| PKG → RDF individuals (`facts_to_graph`) | **STUBBED** — only fed to the internal SHACL verifier, never to ontomesh | `pkg/rdf.py`, `pkg/verifier.py` |
| PKG → ontomesh-shaped SQLite (`export_sqlite`) | **STUBBED** — written as a file/artifact, never sent | `pkg/export.py:3-8`; callers `cli.py`, `comprehension.py` |
| Ontomesh **facts/RDF ingest** | **ABSENT on ontomesh's side** | `wizard/importer.py` (SQL/JSON only) |
| MCP wrapper over ontomesh (in-loop querying) | **DEFERRED** | `tri-repo-integration.md:167` |
| EntityKey / lineage persistence + query | **DEFERRED (in-memory only)** | `spine/lineage.py`; spec `:158-159` |
| Real-ontology mapping precision / north-star vignette | **NOT RUN** | spec `:104-118, 164` |

**One-line gap:** spine can *ask* ontomesh a domain question (if you stand it up), but the
two knowledge graphs **never share data**, ontomesh **can't ingest** spine's code facts, and
the agent **can't query** ontomesh as a tool.

---

## 4. The core gap, precisely

1. **Complementarity is real; the join is missing.** ontomesh = domain semantics, PKG =
   code structure. Composing them is the value — but there is **no code↔domain edge**
   (no shared `ontology_iri` on PKG nodes populated from ontomesh, no ingestion of PKG into
   ontomesh). The `EntityKey` machinery to *hold* the join exists; the *data* to fill it
   doesn't.

2. **Ontomesh has no ingress for spine's graph.** Spine's `export_sqlite` output already
   **mirrors ontomesh's `db/schema.sql`** and `facts_to_graph` emits A-box individuals in
   ontomesh's default namespace — but ontomesh only imports *schemas to model an ontology*,
   not *facts to reason over*. Bridging requires either (a) writing spine's projection into a
   relational store ontomesh points at (`ONTOMESH_DB_URL`), or (b) a **new ontomesh
   endpoint** to accept facts/RDF (a cross-repo ask).

3. **Transport mismatch (Flask vs MCP).** Spine's codegen loop and its whole tool-governance
   layer (just generalized in the MCP work) speak MCP. Ontomesh speaks HTTP. Without an MCP
   wrapper the agent can't *interrogate* the domain ontology while it works — it only gets a
   single grounding block at plan time.

4. **Contract drift risk.** Spine's client + `export.py` were built against ontomesh
   **v3.8.0**; ontomesh is now **v3.9.0**. The `/api/search` response shape spine parses
   (`status ∈ {ok,blocked,ungrounded,empty}`, `citations[].iri`, `confidence`) must be
   re-verified against the current service.

5. **Governance boundary.** Ontomesh has **sensitivity tiers / federation**; spine grounding
   must pass/respect `max_tier` so higher-tier domain knowledge can't leak into a lower-tier
   build. Spine currently sends only `{question, flavor}`.

---

## 5. Why it matters — productivity impact

**Today spine is domain-blind.** It generates code that's structurally grounded (the PKG) but
knows nothing of the *business meaning* unless the ticket spells it out. A human has to load
that context into every ticket. Ontomesh is the standing source of that context.

Concrete gains, by capability:

- **Domain-grounded codegen (Seam 1, near-term).** When spine builds "add a fraud-alert
  threshold," it asks ontomesh what a *fraud alert* is, its constraints, and related entities
  → the codegen prompt carries domain citations → the generated code uses the right
  abstractions, names, and business rules on the **first pass**. Fewer wrong-abstraction
  rebuilds; less human context-loading per ticket.
- **Domain-aware design / RCA / investigation (extends C1–C8).** The suite we just shipped
  (`design`, `investigate`, `rca`, `regression`) grounds in *code*. With ontomesh, an RCA
  hypothesis or a design can cite a **business invariant** ("an Order must reference a
  Customer") — catching defects that are structurally valid but semantically wrong.
- **SHACL guardrails at the gate.** Spine already has a `GroundingVerifier` that validates
  the RDF projection against SHACL shapes — designed for ontomesh's generated shapes. Wired
  live, generated schema/code changes get **checked against domain constraints** before merge.
- **End-to-end lineage / auditability.** The `EntityKey` join makes one question answerable:
  *domain concept → code symbol → deployed unit → drift → fix*. That's traceability for
  compliance and a far richer blast radius (the composed impact from C5a extended across the
  code↔domain boundary).
- **The agent looks it up itself (MCP).** With an ontomesh MCP tool, the codegen/RCA loop
  *queries* the ontology on demand instead of relying on one pre-seeded block — the same shift
  that made in-loop PKG tools valuable.

**Net productivity thesis:** ontomesh turns spine from a *code-true* engine into a
*code-true **and** domain-true* one — fewer semantic defects, less human context transfer,
governable/auditable changes. The near-term slice is cheap (Seam 1 is built); the deep slice
(bidirectional graph) is where the compounding value is.

---

## 6. Recommendations (tiered)

Effort: **S** ≈ 1–2 days · **M** ≈ 3–5 days · **L** ≈ 1–2 weeks · **XL** = cross-repo/co-design.

### O1 — Prove Seam 1 end-to-end (contract check + the north-star vignette) · **S–M** · foundational
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Stand up ontomesh (`docker run … ghcr.io/synaptixs/ontomesh`), load/mine a real
ontology for one `flavor`, set `SPINE_ONTOMESH_URL`/`_FLAVOR`, and run one real feature
through domain-grounded codegen. **First verify the v3.9.0 `/api/search` response still
matches spine's `ReasonedAnswer.from_payload`** (status/citations/confidence). **Why:** the
seam is already wired; this is the unlock, and the spec forbids "presenting the loop" before
this vignette runs. **How:** a `doctor`-style ontomesh reachability + contract check, plus a
one-page runbook. **Risk:** ontology quality — if the mined ontology is thin, grounding adds
noise; measure with the existing `evaluate_precision`.

### O2 — Pass the sensitivity tier through Seam 1 · **S** · governance
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Send `max_tier` (and honor `status: blocked`) on `/api/search` so federated,
higher-sensitivity domain knowledge can't leak into a lower-tier build. **Why:** ontomesh
enforces tiers; spine must respect them before this is safe on real data. **How:** add a
`SPINE_ONTOMESH_MAX_TIER` env + wire it into the client payload. Small, closes a real trust gap.

### O3 — Wrap ontomesh as an MCP tool (in-loop domain queries) · **M** · high synergy
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Expose ontomesh's `/api/search` (and optionally `/api/sparql`) as an **MCP server**,
so the agentic codegen/RCA loop can ask the domain ontology mid-task as a governed tool —
not just one plan-time block. **Why:** closes the Flask↔MCP transport gap the spec flags, and
**reuses the generalized MCP-source machinery just built** — very low marginal cost, high
leverage. **How:** a thin MCP shim over the HTTP client; register it like any onboarded server.
**Depends on:** O1.

### O4 — The ingestion bridge: PKG → ontomesh's store · **L** · the headline
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Make ontomesh *aware of the code* so the code↔domain join has data. **How (two
paths):** (a) **DB-share** — write spine's `export_sqlite` projection (already ontomesh-schema
shaped) into a relational store ontomesh points at (`ONTOMESH_DB_URL`), then ontomesh
annotates/reasons over the code-as-data; or (b) **new endpoint** — propose an ontomesh
`POST /api/facts` accepting `facts_to_graph` triples (cross-repo, co-designed). **Why:** this
is what unlocks bidirectional queries ("which code implements this rule?") and lineage.
**Risk:** highest-effort; needs ontomesh-side agreement for (b); (a) is doable now but couples
schemas. **Recommendation:** start with (a) as a spike; pursue (b) as the durable contract.

### O5 — Populate `ontology_iri` on PKG nodes (the join, persisted) · **M** · enables lineage
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Use ontomesh citations (+ the existing `CodeOntologyMapper`/`MappingStore`) to tag
PKG symbols with their domain `ontology_iri`, and **persist the lineage** (today in-memory).
**Why:** turns the "semantic spine" from a diagram into a queryable graph; powers domain-aware
impact/RCA and audit. **Depends on:** O1 (real citations) + O4 (for the reverse direction).

### O6 — Domain-aware design / RCA (extend C1–C8) · **M** · productivity headline
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Feed ontomesh grounding into the new commands — `design` cites domain constraints,
`rca` adds domain-invariant hypotheses, `investigate` surfaces the business entities a ticket
touches. **Why:** compounds the just-shipped suite with semantic correctness. **Depends on:**
O1 (+ O3 for in-loop). **Effort** is mostly prompt/compose wiring; the grounder already exists.

### O7 — SHACL guardrails live at the gate · **S–M** · quality/compliance
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Wire the existing `GroundingVerifier` against ontomesh's generated SHACL shapes so
generated changes are validated against domain constraints before merge. **Why:** catches
semantically-invalid-but-compiling changes; the loop is already built (works standalone today).
**Depends on:** O1 (to fetch/point at the shapes).

---

## 7. Suggested sequence

1. **O1** — prove Seam 1 + contract check (nothing else is real until this runs).
2. **O2** — tier passthrough (cheap, unblocks safe real-data use).
3. **O3** — MCP wrapper (high synergy with the MCP work; in-loop domain queries).
4. **O6** — domain-aware design/RCA (fast follow; compounds C1–C8).
5. **O7** — SHACL guardrails (quality gate).
6. **O4 → O5** — the ingestion bridge + persisted join (the durable, higher-effort headline).

**Quick win vs deep win:** O1–O3 (+O6) deliver *domain-grounded generation* in ~1–2 weeks
using mostly built pieces. O4–O5 are the multi-week investment that yields the bidirectional
graph and lineage.

## 8. What I'd push back on / defer

- **Don't build O4/O5 before O1 proves value.** If the mined ontology isn't good enough to
  move `evaluate_precision`, the whole join is "fiction" (the spec's word) — validate the
  cheap seam first.
- **Prefer O4(a) DB-share as a spike, not a product.** Coupling to ontomesh's internal
  `db/schema.sql` is brittle across its pre-1.0 releases; the durable answer is O4(b), a
  versioned facts endpoint — but only pursue it once O1/O3 show the payoff.
- **Watch the version treadmill.** Ontomesh is pre-1.0 ("expect breaking changes"); pin the
  image and keep the `export.py`/client "verified against ontomesh@vX" contract note current.

## 9. Open questions for you

1. **Is there a real ontology to ground against?** O1's value hinges on a mined domain
   ontology existing for at least one `flavor`. If not, that's step zero.
2. **Cross-repo appetite for O4(b)?** A `POST /api/facts` on ontomesh is the clean bridge but
   needs ontomesh-side work — is that on the table, or do we live with DB-share?
3. **Which vertical first?** The vignette needs a concrete domain (the spec uses "fraud").
   Picking one target domain focuses O1/O6.
