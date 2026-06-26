# Features & Capabilities — Spine

What Spine can do today, and how to use it. For setup see [SETUP.md](SETUP.md), for
the everyday workflow see [USER_GUIDE.md](USER_GUIDE.md), and for configuration +
operations see [OPERATIONS.md](OPERATIONS.md).

> **Naming.** *Spine* is the product; it ships as the **`agent-orchestrator`**
> package with the **`orchestrator`** command.

**Status:** ✅ shipped · 🟡 partial / operator-gated · 🔬 experimental (off by default).
Most advanced behavior is off until you set its environment variable.

---

## Delivery pipeline — requirements → reviewed PR
The core loop: read a requirement, understand the repo, generate grounded code +
tests, get them green, open a PR — with **two human gates** (before building, before merging).

| Capability | Status | How to use |
|---|---|---|
| Intake — Confluence / Notion / Markdown → specs → backlog | ✅ | `orchestrator ingest --source <uri>`, `orchestrator backlog` |
| Single feature build (local & safe by default) | ✅ | `orchestrator sdlc feature --source file://./spec.md --safe` |
| Full orchestrated run (backlog → many features) | ✅ | `orchestrator sdlc run --source <uri>` |
| Open / complete a PR (live) | ✅ | `orchestrator sdlc feature … --live`, `orchestrator sdlc complete` |
| Address review feedback on a PR | ✅ | `orchestrator sdlc address-review --pr <url>` |

## Code-grounded understanding
Builds a **Product Knowledge Graph** of the repo (modules, types, call sites, blast
radius) and grounds new code in what already exists.

| Capability | Status | How to use |
|---|---|---|
| Multi-language comprehension + codegen — Python, Java, TypeScript | ✅ | automatic per repo |
| Committed `memory-bank/` for humans + any AI tool | ✅ | `orchestrator understand --out memory-bank` |
| PKG extraction / export | ✅ | `orchestrator pkg extract`, `orchestrator pkg export` |
| Repo profile / audit | ✅ | `orchestrator profile <repo>`, `orchestrator audit <repo>` |

## Governed autonomy
The workflow is a typed, validated artifact: a planner decomposes the objective, a
runtime executes it, and per-edge verifiers check every step against schemas,
evidence, and policy.

| Capability | Status | How to use |
|---|---|---|
| Human approval gates (before build, before merge) | ✅ | `--safe`; approvals API/UI |
| Policy + budget guardrails | ✅ | `SDLC_RUN_BUDGET_USD`, `SDLC_AGENTIC_POLICY` |
| Audit trail — every tool call, approval, decision | ✅ | persisted; `/v1/tasks/<id>/trace` |
| Export / replay a run | ✅ | trust-spine export |
| RBAC / multi-tenancy | 🟡 | `ORCHESTRATOR_PRINCIPALS`, `ORCHESTRATOR_TENANT_ID` |

## Smarter codegen
| Capability | Status | How to use |
|---|---|---|
| Catalog-then-compose (right capabilities per project) | ✅ | `orchestrator catalog plan` |
| Agentic (ReAct) tool-use codegen loop | 🔬 | `SDLC_AGENTIC_CODEGEN=1` |
| Convention learning + clarifying questions | ✅ | automatic |
| Local / offline models (Ollama, any OpenAI-compatible) | ✅ | `SDLC_CODEGEN`, `ORCHESTRATOR_INTAKE_MODEL` |

## Personas, memory & observability
| Capability | Status | How to use |
|---|---|---|
| PR Reviewer / Auditor personas | ✅ | persona registry / GitHub App |
| Eval harness | ✅ | `evals` module |
| Cross-run semantic memory | ✅ | `ORCHESTRATOR_SEMANTIC_MEMORY=1` |
| Live OpenTelemetry tracing | ✅ | `OTEL_EXPORTER_OTLP_ENDPOINT` |

## Integrations (MCP)
| Capability | Status | How to use |
|---|---|---|
| Consume external MCP servers (DBs, Atlassian, …) | ✅ | `orchestrator mcp ingest-db`, `mcp list`, `mcp call` |
| Spine as an MCP server (drive from Claude / Codex / IDE) | ✅ | `plugin` surface; remote HTTP/OAuth |

## The semantic spine (ontomesh × Spine × infodrift)
A shared **`EntityKey`** (`Component_vX::Region::Interface`) joins a domain concept →
code symbol → deployment unit → drift signal, so a production drift becomes a
grounded, governed, provenance-carrying code fix. **All gated, inert unless configured** —
configure and operate it from [OPERATIONS.md](OPERATIONS.md#the-semantic-spine).

| Seam | Status | How to use |
|---|---|---|
| Seam 1 — domain-grounded build (ontomesh → codegen) | ✅ | `SPINE_ONTOMESH_URL` + `SPINE_ONTOMESH_FLAVOR` |
| Seam 3 — drift → governed remediation (infodrift → PR) | 🟡 | `orchestrator sdlc remediate --report drift.json` |
| Seam 2 — register shipped units (Spine → infodrift) | 🟡 | `SPINE_INFODRIFT_URL` + `SPINE_DEPLOY_TOPOLOGY` |
</content>
