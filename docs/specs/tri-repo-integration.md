# Design: tri-repo integration — the semantic spine (ontomesh × agent-orchestrator × infodrift)

**Status:** **Design only — not implemented.** Spans `synaptixs/ontomesh`,
`agent-orachestrator`, `synaptixs/infodrift`. Cites real surfaces in each; builds
no seams yet.
**Goal (usability-first):** make the three systems *usable together* so that a
real operator gets a concrete outcome they can't get from any one of them — **a
drift in production becomes a grounded, governed code fix, with a provenance trail
from the domain concept that defined it to the deployment that broke.** We design
for the outcome and the mechanism, not the pitch: if the mechanism is genuinely
useful, the market follows.

## The mechanism that matters: one semantic spine
The integration is not "a loop." It's a **shared identity** — the
**ontology-entity key** `Component_vX::Region::Interface` — that the same artifact
carries through every stage, so a single lineage is *queryable end to end*:

```
domain concept        code symbol            deployment unit        drift signal
(ontomesh IRI)  ──►   (PKG node +      ──►   (entity_key +    ──►   (infodrift
                       ontology_iri)          version)              RunReport)
        ▲                                                                │
        └──────────── governed fix, scoped to the same key ◄────────────┘
```

Everything below exists to make that spine real and *usable*. Nothing is
trustworthy until the spine holds — which is why de-risking the join is Phase 0,
not an afterthought.

## What makes it usable (locked principles)
1. **Each phase delivers standalone value.** No phase requires the whole loop to be
   useful. Phase A (domain-grounded codegen) is worth shipping even if drift
   feedback never lands. Adoption is incremental, not all-or-nothing.
2. **The join is earned, not assumed.** Code-symbol ↔ ontology-class mapping is
   the linchpin; it ships **heuristic + human-confirmed**, with a visible
   confidence and an audit of every mapping. A wrong mapping must be obvious and
   correctable, never silently trusted. If the spine is fiction, everything above
   it is fiction.
3. **The feedback has teeth.** Drift does not just "write a memory the agent might
   recall." A drift finding **opens a governed, ontology-constrained remediation
   run** scoped to the exact entity — the drift report is the spec, SHACL/ontology
   constraints are guardrails, a human gates the merge. Memory is a *byproduct* of
   that run, not the mechanism.
4. **Human-gated, not "self-healing."** In the high-consequence systems this
   targets (the infodrift examples are 5G core network functions, fraud models),
   the value is *auditable, gated* remediation. The governance is the feature.
5. **Model-agnostic.** The orchestrator wraps *any* coding agent (its own loop,
   Claude Code, Cursor, Devin) behind the grounding + governance + provenance
   substrate, surfaced over MCP. Don't compete on raw agent quality; make any
   agent grounded and accountable.
6. **Degrade gracefully.** Any repo absent → the others still work (the
   orchestrator already treats grounding + MCP as best-effort). No hard coupling;
   stable HTTP/MCP contracts only.

## The three seams (each independently useful)

### Seam 1 — domain-grounded build (ontomesh → orchestrator) · *use: better code, day one*
**Outcome for the user:** generated/edited code respects the domain's real entities,
relationships, and business rules — not just the code's structure — with citations.

- **Contract:** the orchestrator queries ontomesh `/api/search` (or an MCP tool)
  with the feature's domain terms; ontomesh returns a `ReasonedAnswer{answer,
  citations:[Citation{iri}], inferred, confidence, executed_query}`.
- **Wiring:** an `OntomeshGrounder` implementing the orchestrator's `CodegenGrounder`
  protocol (beside `PKGCodegenGrounder`), or an MCP tool next to `recall_memory`.
  It prepends a **cited domain-knowledge block** to codegen grounding — the same
  slot `memory_bank_grounding` fills.
- **Why usable alone:** even with zero drift wiring, codegen that knows the domain
  ontology (with citations into the audit trail) is immediately better and more
  defensible than code-only grounding. This is the low-friction entry point.

### Seam 3 — drift → governed remediation (infodrift → orchestrator) · *use: prod degradation becomes a reviewed fix*
**Outcome for the user:** when a deployed unit drifts, they get a scoped, grounded
remediation PR to review — instead of an alert they have to triage by hand.

- **Contract:** an orchestrator ingestion path (infodrift webhook or scheduled pull
  of `HealthReporter.full_report(as_json=True)` — per-`entity_key` L1/L2/L3
  findings) turns a material finding into a **remediation task**: the drift report
  becomes the spec, the entity's ontomesh constraints become guardrails, the run is
  scoped to the code mapped to that `entity_key`.
- **Teeth:** it spawns a *governed* orchestrator run (policy + human gate +
  budget), not a silent change. The resulting PR carries full provenance: drift
  window → entity_key → ontology IRI → changed code symbols. The cross-run
  `MemoryRow` it leaves behind (via the shipped Phase-2/3 consolidation) is the
  durable lesson, scoped by `repo_key` **and** `entity_key`.
- **Why usable alone:** valuable the moment infodrift exists and the orchestrator
  can build — even before auto-registration (Seam 2). Start with a human pasting an
  entity_key; automate the trigger later.

### Seam 2 — register what shipped (orchestrator → infodrift) · *use: monitored from birth*
**Outcome for the user:** every unit the agent ships is monitored against the exact
version it produced, with no manual onboarding.

- **Contract:** post-merge, the orchestrator derives `entity_key` (Component ↔
  ontology entity; Version from the release/PR; Region/Interface from deploy
  config) and calls `DriftOrchestrator.register_entity(entity_key,
  train_features_df=…, model_version=…, baseline_id=…)`, or emits
  `unit_shipped{entity_key, version, trace_id}`.
- **Hook:** beside the existing post-merge `sdlc_consolidate_memory` activity in
  `SDLCWorkflow` (Phase 2b) — same completion point, same gating.
- **Why last:** it closes the automation but isn't needed for either Seam to be
  useful; it removes the one manual step in Seam 3.

## North-star vignette (the acceptance test — build this, not a platform)
The smallest thing that proves the spine is real *and* useful. One real entity,
end to end:

> A predictive component (`FraudDetector_v5::APAC::CardTransactions`, or the 5G
> `AMF_v2::RegionA::N11`) drifts → **infodrift localizes it** to that `entity_key`
> (L2 calibration erosion, ECE 3×) → the orchestrator **opens a governed
> remediation run** scoped to the code mapped to that key, **grounded by ontomesh's
> ontology + SHACL constraints** for that entity → it produces a **PR with end-to-end
> provenance** (drift window → entity_key → ontology IRI → changed symbols) → a
> human approves the merge → the lesson lands in cross-run memory.

When that vignette runs on one real entity, the integration exists. Everything else
is widening coverage. **Do not present the loop before this vignette works** — until
then it's slideware.

## Phasing (ordered by usable value, gated on the spine)
- **Phase 0 — prove the spine (the join).** ✅ **built** (`orchestrator/spine/`):
  `EntityKey` contract; `CodeOntologyMapper` + `MappingLedger` (human confirm/reject +
  audit); `evaluate_precision` exit-gate; **`MappingStore` persists confirmed mappings**
  (durable JSON, `code_for_iri` view). *Operational remainder:* a real-domain precision
  number (needs a real ontology). *No other phase is trustworthy until this clears a bar.*
- **Phase A — Seam 1 (domain-grounded build).** ✅ **built + wired** (`spine/grounder.py`):
  `OntomeshGrounder` (CodegenGrounder over ontomesh `/api/search`, cited `GroundingBlock`,
  degrades to "") + `CompositeGrounder`. **Live-wired into both codegen paths** — the
  Temporal worker (`compose_factory_with_ontomesh`) and the linear `feature_runner`
  (`compose_with_ontomesh`) — gated by `SPINE_ONTOMESH_URL` + `SPINE_ONTOMESH_FLAVOR`
  (inert otherwise). Immediate value, read-only.
- **Phase B — Seam 3 with teeth (drift → governed remediation).** 🟡 **mechanism built**
  (`spine/drift.py` + `spine/remediation.py`): `DriftReport.from_infodrift` normalizes
  infodrift's `full_report` into severity-gated findings; `plan_remediations` turns
  material drift into scoped, guardrailed, provenance-carrying `RemediationTask`s
  (drift→entity_key→ontology IRI→confirmed code nodes; spec + ontology/SHACL guardrails +
  human-gate). ✅ **Execution wired**: `run_feature` accepts an injected `spec` (skips
  intake); `spine/execute.py: execute_remediations` plans tasks and runs each via an
  injected runner (best-effort); the `orchestrator sdlc remediate --report <json>` CLI
  is the inbound trigger (drift report → governed runs, `--safe` human-gated by default,
  `--live` opens PRs), with `infer_entity_iris` deriving scope from persisted mappings.
  The headline outcome, end to end.
- **Phase C — Seam 2 (auto-register on ship).** 🟡 **mechanism built** (`spine/shipment.py`):
  `ShipmentRegistrar` derives entity keys from a `ShippedUnit` × `DeployTopology`
  (one per region/interface placement) and emits `unit_shipped` events
  (`RegistrationRequest`) to an `InfodriftRegistry` (HTTP client + Protocol); best-effort
  per placement, never fails the merged run. `component_for_nodes` derives the entity from
  the changed code's confirmed mappings. **Live-wired** as the post-merge
  `sdlc_register_units` activity in `SDLCWorkflow`, gated by `SPINE_INFODRIFT_URL` +
  `SPINE_DEPLOY_TOPOLOGY` (inert otherwise). *Operational remainder:* a real deploy-config
  topology source (vs. the env-declared one). Removes the last manual step.
- **Phase D — unified provenance + telemetry.** 🟡 **built** (`spine/lineage.py`):
  `LineageIndex` ingests the typed artifacts from Phases 0–3 and self-links them on
  `ontology_iri` / `entity_key` / `trace_id`; query from a code node, an entity, an IRI,
  or a build trace → the same end-to-end `LineageRecord` (domain → code → deployment →
  drift → remediation → memory). `correlation_handles` yields the cross-plane keys
  (orchestrator OTel by `trace_id`, infodrift/ontomesh by `entity_key`/IRI), anchored on
  `docs/specs/live-observability-otel.md`. *Remaining:* persist lineage + a query
  surface (CLI/API) over real runs.

## Risks (stated plainly, because they decide whether this works)
- **Entity-mapping accuracy is existential.** If code↔ontology mapping is flaky, the
  spine — and every provenance claim — is fiction. Phase 0 must measure precision on
  a real domain and keep humans in the correction loop; do not auto-trust.
- **`entity_key` derivation needs deploy topology** (Region/Interface) the
  orchestrator doesn't own today — Phase C needs a deploy-config source.
- **Transport gaps:** ontomesh is Flask (needs an MCP wrapper); infodrift is a
  library (needs a thin webhook/service for Seam 3). Both small, both real work.
- **Trust boundary:** ontomesh federation enforces sensitivity tiers — domain facts
  entering codegen grounding must respect that gate (no cross-tenant leakage).
- **Overreach on autonomy:** auto-remediation without a human gate, in these
  verticals, destroys trust. Keep the gate; sell the gate.

## Why this is impactful (kept short, on purpose)
The point tools each solve one stage and share no semantics, so a human is the only
thing connecting "this drifted" to "this code, defining this concept, must change."
This integration makes that connection a *queryable, governed mechanism* on a shared
ontology key. The impact is concrete: **less manual triage, grounded fixes, and a
provenance trail you can audit** — in domains where that trail is non-optional. Build
the north-star vignette first; usefulness is the proof, and the rest follows.
