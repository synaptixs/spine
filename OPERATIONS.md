# Operations & Developer Guide — Spine

How to **run, configure, and operate** Spine beyond the everyday build — deployment
modes, the full environment-variable reference, and step-by-step setup for each
advanced capability, including the semantic spine (ontomesh × Spine × infodrift).

> **Naming.** *Spine* is the product; it ships as the **`agent-orchestrator`**
> package with the **`orchestrator`** command.

**Where this sits in the docs:**

| Read | For |
|---|---|
| [SETUP.md](SETUP.md) | First install + local stack (Postgres, MinIO, Temporal). |
| [USER_GUIDE.md](USER_GUIDE.md) | The everyday workflow, step by step. |
| [FEATURES.md](FEATURES.md) | The capability catalog — *what* exists. |
| **This guide** | *How to operate* each capability + the full config surface. |
| [docs/specs/](docs/specs/) | Design rationale behind each capability. |

---

## 1. Deployment modes

Spine runs at three levels of infrastructure. Start small; add pieces only when you
need them.

| Mode | What runs | When to use |
|---|---|---|
| **CLI / local** | Just the `orchestrator` CLI | First builds, `--safe` runs, PKG, `understand`, remediation. No DB. |
| **Service** | REST API + web dashboard | Approvals UI, trace inspection, team use. Needs Postgres. |
| **Full pipeline** | API + **Temporal worker** + Postgres + MinIO | Orchestrated multi-feature runs, post-merge activities (Seam 2). |

Bring-up for the service and full-pipeline modes is in [SETUP.md](SETUP.md) (docker
compose + migrations + the worker). This guide assumes that baseline and focuses on
configuration and the advanced capabilities.

---

## 2. Environment-variable reference

All behavior is configured by environment variables (Spine scaffolds a `.env` via
`orchestrator init`). Advanced features are **off until their variable is set**.

### Core / LLM

| Variable | Purpose | Default |
|---|---|---|
| `ORCHESTRATOR_INTAKE_MODEL` | Model for intake/spec extraction | — |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | LLM credentials | — |
| `SDLC_CODEGEN` | Codegen backend (`llm`, …) | — |
| `SDLC_CODEGEN_MODEL` / `SDLC_REVIEW_MODEL` | Per-stage model overrides | intake model |
| `ORCHESTRATOR_LLM_TIMEOUT_SECONDS` | LLM call timeout | — |

### Pipeline & governance

| Variable | Purpose | Default |
|---|---|---|
| `SDLC_REPO_URL` | Repo the run branches from (path or GitHub URL) | — |
| `SDLC_RUN_BUDGET_USD` | Hard per-run spend cap | — |
| `SDLC_AGENTIC_CODEGEN` | Enable the ReAct tool-use loop | `0` (off) |
| `SDLC_AGENTIC_POLICY` | Policy profile for the agentic loop | — |
| `SDLC_REVIEW` | Enable in-pipeline review | — |
| `SDLC_TEST_ISOLATION` | Sandbox test execution | — |
| `SDLC_GITHUB_INSTALLATION_ID` | GitHub App auth for live PRs | — |
| `SDLC_TASK_QUEUE` / `SDLC_WORKSPACE_ROOT` | Temporal queue / scratch dir | — |

### Service / storage / identity

| Variable | Purpose |
|---|---|
| `ORCHESTRATOR_DATABASE_URL` | Postgres connection (service + full pipeline) |
| `ORCHESTRATOR_ARTIFACT_STORE` | Object store (MinIO/S3/GCS) |
| `ORCHESTRATOR_SESSION_SECRET` | Signs web-UI cookies |
| `ORCHESTRATOR_API_URL` / `ORCHESTRATOR_API_KEY` | REST API endpoint + key |
| `ORCHESTRATOR_PRINCIPALS` / `ORCHESTRATOR_TENANT_ID` | RBAC / multi-tenancy (🟡 partial) |

### Memory & observability

| Variable | Purpose |
|---|---|
| `ORCHESTRATOR_SEMANTIC_MEMORY` | Enable cross-run semantic memory |
| `ORCHESTRATOR_MEMORY_BANK_DIR` | Where `understand` writes the memory bank |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Ship live traces (Jaeger/OTLP) |

### MCP

| Variable | Purpose |
|---|---|
| `ORCHESTRATOR_MCP_CONFIG` | MCP client config (servers Spine consumes) |
| `ORCHESTRATOR_MCP_HOST` / `_PORT` / `_PATH` | Spine-as-MCP-server bind |
| `ORCHESTRATOR_MCP_ISSUER_URL` / `_INTROSPECTION_*` / `_REQUIRED_SCOPES` | Remote MCP OAuth |

### Semantic spine

| Variable | Purpose | Default |
|---|---|---|
| `SPINE_ONTOMESH_URL` | ontomesh base URL (Seam 1) | unset → off |
| `SPINE_ONTOMESH_FLAVOR` | ontology/sensitivity flavor (Seam 1) | unset → off |
| `SPINE_ONTOMESH_MIN_CONFIDENCE` | Drop answers below this confidence | `0.0` |
| `SPINE_INFODRIFT_URL` | infodrift register endpoint (Seam 2) | unset → off |
| `SPINE_DEPLOY_TOPOLOGY` | `{component: [[region, interface], …]}` (Seam 2) | unset → off |
| `SPINE_SHIP_VERSION` | Version stamped on shipped units (Seam 2) | `1` |

---

## 3. Operating the advanced capabilities

### Code-grounded `understand`
Write a committed, code-true memory bank any human or AI tool can read:
```bash
orchestrator understand --out memory-bank          # uses the commit-cached PKG
orchestrator understand --out memory-bank --refresh # force re-extraction
```
Commit `memory-bank/` so the whole team (and any AI assistant) shares grounded context.

### The agentic (ReAct) codegen loop
Off by default. Turn on the tool-use loop and (optionally) a policy profile:
```bash
export SDLC_AGENTIC_CODEGEN=1
export SDLC_AGENTIC_POLICY=<profile>
orchestrator catalog plan        # inspect what would be assembled for this repo
```

### Cross-run semantic memory
```bash
export ORCHESTRATOR_SEMANTIC_MEMORY=1
```
Lessons from one run (including remediation runs) persist and ground later runs,
scoped by repo (and, for spine remediation, by `entity_key`).

### Live tracing
```bash
docker compose -f docker-compose.dev.yml up -d jaeger
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
# run anything; open http://localhost:16686 for llm.complete / agent.step spans
```

### Local / offline models
No API key required — point codegen at Ollama or any OpenAI-compatible endpoint. See
[USER_GUIDE Step 6](USER_GUIDE.md#step-6).

---

## 4. The semantic spine — configure & operate

A shared **`EntityKey`** (`Component_vX::Region::Interface`) joins a domain concept →
code symbol → deployment unit → drift signal. There are three seams; each is
independently useful and **inert unless its variables are set**. Full design:
[tri-repo-integration.md](docs/specs/tri-repo-integration.md). End-to-end walkthrough:
[the vignette runbook](docs/specs/spine-vignette-runbook.md).

> **The two systems are different kinds of thing.**
> **ontomesh is a service** (a Flask app you run, then point a URL at).
> **infodrift (`drift_monitor`) is a *library*** — it has no HTTP server. So Seam 3
> needs no running infodrift service (just a report file it produces), and Seam 2's
> `SPINE_INFODRIFT_URL` has nothing to point at unless you wrap the library in a
> small shim.

### Prerequisite — the code↔ontology mapping (Phase 0)
Seams 2 and 3 scope work to code via a **confirmed** mapping store
(`spine-mappings.json`). Mappings are proposed heuristically, then **human-confirmed**
before they're authoritative. Build it once per repo+ontology — see
[runbook step 3](docs/specs/spine-vignette-runbook.md). Without it, drift can't be
scoped to code and provenance claims are fiction.

### Seam 1 — ontomesh domain grounding
*Read-only, safe to turn on first. Adds cited domain knowledge to codegen.*

1. **Start ontomesh** (external Flask service):
   ```bash
   docker run -d --name ontomesh -p 5051:5051 ghcr.io/synaptixs/ontomesh:latest
   curl -s http://localhost:5051/healthz
   ```
   On the ontomesh side, two things must be true: search is enabled (its
   `ONTOFORGE_SEARCH` flag), and an ontology is modeled (the wizard at
   `http://localhost:5051`, or seed a FIBO subset).
2. **Configure** (URL is the *base* — the client appends `/api/search`):
   ```bash
   export SPINE_ONTOMESH_URL=http://localhost:5051
   export SPINE_ONTOMESH_FLAVOR=fraud          # must match a flavor in your ontomesh
   export SPINE_ONTOMESH_MIN_CONFIDENCE=0.4     # optional; default 0.0
   ```
3. **Verify** the exact env-driven path codegen uses:
   ```bash
   uv run python -c "
   from orchestrator.spine import ontomesh_grounder_from_env
   g = ontomesh_grounder_from_env()
   print('grounder active:', g is not None)
   if g: print(repr(g.context_for_spec({'title':'recalibrate fraud scores','summary':'calibration drift'})))
   "
   ```
   `False` ⇒ URL or flavor missing (seam inert). A non-empty string ⇒ grounding will
   be injected. **Fail-safe:** any outage or low-confidence answer degrades to
   code-only grounding — it never breaks a build.

Once set, grounding composes automatically into both codegen paths (the Temporal
worker and the linear runner). No extra flag.

### Seam 3 — drift → governed remediation
*The headline. Needs **no** infodrift service — just a `full_report` JSON.*

1. **Produce a drift report** by running the `drift_monitor` library offline
   (`register_entity` → score a shifted window → `HealthReporter.full_report(as_json=True)`).
   See [runbook step 4](docs/specs/spine-vignette-runbook.md) for the script, or
   hand-write the JSON for a first pass.
2. **Run the governed remediation** (one run per affected entity):
   ```bash
   orchestrator sdlc remediate \
     --report drift.json \
     --mappings spine-mappings.json \
     --repo /path/to/repo \
     --min-severity warning \          # or: critical
     --safe                            # default: branch+diff to review; --live opens PRs
   ```
   It scopes each fix to the code mapped to the drifting `entity_key`, uses
   ontomesh/SHACL constraints as guardrails (when Seam 1 is configured), and carries
   full provenance (drift window → entity_key → ontology IRI → changed symbols). Keep
   `--safe` until you've measured mapping precision on your real ontology.

### Seam 2 — register shipped units
*Closes the loop: every unit the agent ships is monitored from birth. Needs an HTTP
receiver — which infodrift does **not** ship.*

- **Option A (recommended to start): leave it off.** The post-merge `register_units`
  activity no-ops cleanly; Seams 1 and 3 are unaffected.
- **Option B: stand up a thin shim** wrapping `DriftOrchestrator.register_entity`
  behind one HTTP route, then point Spine at it:
  ```bash
  export SPINE_INFODRIFT_URL=http://localhost:8080
  export SPINE_DEPLOY_TOPOLOGY='{"FraudDetector":[["APAC","CardTransactions"],["EU","CardTransactions"]]}'
  export SPINE_SHIP_VERSION=6
  ```
  Match the shim's route and JSON body to what `InfodriftHttpClient` posts
  (`src/orchestrator/spine/shipment.py`). Registration then fires automatically on the
  post-merge step of a Temporal SDLC run, once per region/interface placement.

### Provenance / lineage
`LineageIndex` reconstructs the full chain (domain → code → deployment → drift →
remediation → memory), queryable from a code node, an entity, an IRI, or a build
trace — see [runbook step 6](docs/specs/spine-vignette-runbook.md). It's in-memory
today; persistence + a query surface are the remaining work.

### Operational gaps (be honest about these)
- **Mapping precision is unproven on a real domain** — there's no real-ontology
  precision number yet. Keep humans in the confirm loop; run Seam 3 `--safe`.
- **Deploy topology is env-declared**, not sourced from real deploy config (Seam 2).
- **infodrift needs a shim** for Seam 2; there's no out-of-box server.

---

## 5. Operational checklists

**Turn on the spine, in order:**
1. ✅ Seam 1 (ontomesh) — read-only, immediate codegen value, zero risk.
2. ✅ Build + human-confirm `spine-mappings.json` for your repo + ontology.
3. ✅ Seam 3 — run `sdlc remediate --safe` on a real drift report; review the diff.
4. 🟡 Seam 2 — only if you want auto-registration; build the shim first.
5. 🟡 Graduate Seam 3 to `--live` once mapping precision is measured.

**Before a live (`--live`) run:**
- GitHub App auth set (`SDLC_GITHUB_INSTALLATION_ID`).
- Budget cap set (`SDLC_RUN_BUDGET_USD`).
- Quality gate green locally (`mypy src tests`, `ruff format --check .`).
- Approvals reachable (service mode up) so the merge gate can be actioned.

---

## See also

- [FEATURES.md](FEATURES.md) — the capability catalog.
- [docs/specs/spine-vignette-runbook.md](docs/specs/spine-vignette-runbook.md) — the
  full end-to-end spine walkthrough on one entity.
- [SETUP.md](SETUP.md) · [USER_GUIDE.md](USER_GUIDE.md) — install + everyday workflow.
</content>
