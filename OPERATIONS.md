# Operations & Developer Guide — Spine

How to **run, configure, and operate** Spine beyond the everyday build: deployment
modes, the environment-variable reference, and the steps to turn on each advanced
capability — including the semantic spine (ontomesh × Spine × infodrift).

See [SETUP.md](SETUP.md) for first install + the local stack, [USER_GUIDE.md](USER_GUIDE.md)
for the everyday workflow, and [FEATURES.md](FEATURES.md) for the capability catalog.

---

## Deployment modes
Start small; add infrastructure only when you need it.

| Mode | What runs | When |
|---|---|---|
| **CLI / local** | Just the `orchestrator` CLI | First builds, `--safe` runs, PKG, `understand`, remediation. No DB. |
| **Service** | REST API + web dashboard | Approvals UI, trace inspection, team use. Needs Postgres. |
| **Full pipeline** | API + Temporal worker + Postgres + MinIO | Orchestrated multi-feature runs, post-merge activities. |

Bring-up for service and full-pipeline modes (docker compose + migrations + worker)
is in [SETUP.md](SETUP.md).

---

## Environment-variable reference
All behavior is configured by environment variables (`orchestrator init` scaffolds a
`.env`). Advanced features are **off until their variable is set**.

**Core / LLM** — `ORCHESTRATOR_INTAKE_MODEL`, `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`,
`SDLC_CODEGEN`, `SDLC_CODEGEN_MODEL` / `SDLC_REVIEW_MODEL`.

**Pipeline & governance** — `SDLC_REPO_URL`, `SDLC_RUN_BUDGET_USD` (hard spend cap),
`SDLC_AGENTIC_CODEGEN` (ReAct loop, default off), `SDLC_AGENTIC_POLICY`,
`SDLC_TEST_ISOLATION`, `SDLC_GITHUB_INSTALLATION_ID` (live PR auth).

**Service / storage / identity** — `ORCHESTRATOR_DATABASE_URL`,
`ORCHESTRATOR_ARTIFACT_STORE`, `ORCHESTRATOR_SESSION_SECRET`, `ORCHESTRATOR_API_URL`
/ `ORCHESTRATOR_API_KEY`, `ORCHESTRATOR_PRINCIPALS` / `ORCHESTRATOR_TENANT_ID` (RBAC, partial).

**Memory & observability** — `ORCHESTRATOR_SEMANTIC_MEMORY`, `ORCHESTRATOR_MEMORY_BANK_DIR`,
`OTEL_EXPORTER_OTLP_ENDPOINT`.

**MCP** — `ORCHESTRATOR_MCP_CONFIG` (servers Spine consumes), `ORCHESTRATOR_MCP_HOST`
/ `_PORT` / `_PATH` (Spine-as-server), `ORCHESTRATOR_MCP_ISSUER_URL` / `_INTROSPECTION_*`
(remote OAuth).

**Semantic spine** — see the table in the next section.

---

## Operating advanced capabilities

**Committed `understand` episteme:**
```bash
orchestrator understand --out episteme            # commit-cached PKG
orchestrator understand --out episteme --refresh  # force re-extraction
```
Commit `episteme/` so the team (and any AI tool) shares grounded context.

**Agentic codegen loop** (off by default):
```bash
export SDLC_AGENTIC_CODEGEN=1
orchestrator catalog plan      # inspect what would be assembled for this repo
```

**Cross-run semantic memory:** `export ORCHESTRATOR_SEMANTIC_MEMORY=1` — lessons
persist and ground later runs.

**Live tracing:** point `OTEL_EXPORTER_OTLP_ENDPOINT` at an OTLP collector (e.g.
Jaeger at `http://localhost:4318`) and run anything.

**Local / offline models:** no API key needed — point codegen at Ollama or any
OpenAI-compatible endpoint (see [USER_GUIDE.md](USER_GUIDE.md)).

---

## The semantic spine
A shared **`EntityKey`** (`Component_vX::Region::Interface`) joins a domain concept →
code symbol → deployment unit → drift signal, so a production drift becomes a
grounded, governed, provenance-carrying code fix. Three seams; each is independently
useful and **inert unless its variables are set**.

| Variable | Purpose | Default |
|---|---|---|
| `SPINE_ONTOMESH_URL` | ontomesh base URL (Seam 1) | unset → off |
| `SPINE_ONTOMESH_FLAVOR` | ontology/sensitivity flavor (Seam 1) | unset → off |
| `SPINE_ONTOMESH_MIN_CONFIDENCE` | drop answers below this confidence | `0.0` |
| `SPINE_INFODRIFT_URL` | infodrift register endpoint (Seam 2) | unset → off |
| `SPINE_DEPLOY_TOPOLOGY` | `{component: [[region, interface], …]}` (Seam 2) | unset → off |
| `SPINE_SHIP_VERSION` | version stamped on shipped units | `1` |

> **The two systems differ.** **ontomesh is a service** (a Flask app you run, then
> point a URL at). **infodrift (`drift_monitor`) is a library** — no HTTP server. So
> Seam 3 needs no running infodrift service (just a report file it produces), and
> Seam 2's `SPINE_INFODRIFT_URL` has nothing to point at unless you wrap the library
> in a small shim.

**Prerequisite — the code↔ontology mapping.** Seams 2 and 3 scope work to code via a
**human-confirmed** mapping store (`spine-mappings.json`); without it, drift can't be
scoped and provenance is fiction. Build it once per repo+ontology.

### Seam 1 — ontomesh domain grounding (read-only, safe to turn on first)
1. Start ontomesh (external Flask service), enable its search, model an ontology:
   ```bash
   docker run -d --name ontomesh -p 5051:5051 ghcr.io/synaptixs/ontomesh:latest
   ```
2. Configure (URL is the *base* — the client appends `/api/search`):
   ```bash
   export SPINE_ONTOMESH_URL=http://localhost:5051
   export SPINE_ONTOMESH_FLAVOR=fraud           # must match a flavor in your ontomesh
   export SPINE_ONTOMESH_MIN_CONFIDENCE=0.4      # optional; default 0.0
   ```
3. Verify:
   ```bash
   uv run python -c "from orchestrator.spine import ontomesh_grounder_from_env; print(ontomesh_grounder_from_env() is not None)"
   ```
   Once set, grounding composes into codegen automatically. Any outage or
   low-confidence answer degrades to code-only — it never breaks a build.

### Seam 3 — drift → governed remediation (the headline; needs no infodrift service)
1. Produce a drift report by running the `drift_monitor` library offline
   (`register_entity` → score a shifted window → `HealthReporter.full_report(as_json=True)`),
   or hand-write the JSON for a first pass.
2. Run the governed remediation:
   ```bash
   orchestrator sdlc remediate --report drift.json --mappings spine-mappings.json \
     --repo /path/to/repo --min-severity warning --safe   # --live opens PRs
   ```
   It scopes each fix to the code mapped to the drifting `entity_key`, uses ontomesh
   constraints as guardrails (when Seam 1 is on), and carries full provenance. Keep
   `--safe` until mapping precision is measured on your real ontology.

### Seam 2 — register shipped units (needs an HTTP receiver infodrift doesn't ship)
- **Recommended to start: leave it off** — the post-merge `register_units` activity
  no-ops cleanly; Seams 1 and 3 are unaffected.
- **To enable:** stand up a thin shim wrapping `DriftOrchestrator.register_entity`
  behind one HTTP route, then set:
  ```bash
  export SPINE_INFODRIFT_URL=http://localhost:8080
  export SPINE_DEPLOY_TOPOLOGY='{"FraudDetector":[["APAC","CardTransactions"]]}'
  ```
  Match the shim's route/body to what `InfodriftHttpClient` posts
  (`src/orchestrator/spine/shipment.py`).

### Honest operational gaps
- Mapping precision is unproven on a real domain — keep humans in the confirm loop; run Seam 3 `--safe`.
- Deploy topology is env-declared, not sourced from real deploy config (Seam 2).
- infodrift needs a shim for Seam 2; there's no out-of-box server.

---

## Turn-on order
1. Seam 1 (ontomesh) — read-only, immediate value, zero risk.
2. Build + human-confirm `spine-mappings.json`.
3. Seam 3 — `sdlc remediate --safe` on a real drift report; review the diff.
4. Seam 2 — only if you want auto-registration; build the shim first.
5. Graduate Seam 3 to `--live` once precision is measured.

**Before a live (`--live`) run:** GitHub App auth set, budget cap set, local quality
gate green, approvals reachable.
</content>
