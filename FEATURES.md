# Features & Capabilities — Spine

What Spine can do today, and how to use it. For setup see [SETUP.md](SETUP.md), for
the everyday workflow see [USER_GUIDE.md](USER_GUIDE.md), and for configuration +
operations see [OPERATIONS.md](OPERATIONS.md).

> **Naming.** *Spine* is the product; it ships as the **`synaptixs-spine`**
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
| Spec-driven intake — [OpenSpec](https://openspec.dev) changes → deterministic intents (no LLM guessing); write-back drafts OpenSpec from a wiki for review | ✅ | `--source openspec://<change-id>`, `orchestrator openspec draft --source <uri>` |
| Single feature build (local & safe by default) | ✅ | `orchestrator sdlc feature --source file://./spec.md --safe` |
| Full orchestrated run (backlog → many features) | ✅ | `orchestrator sdlc run --source <uri>` |
| Open / complete a PR (live) | ✅ | `orchestrator sdlc feature … --live`, `orchestrator sdlc complete` |
| Address review feedback on a PR | ✅ | `orchestrator sdlc address-review --pr <url>` |

## Code-grounded understanding
Builds a **Product Knowledge Graph** of the repo (modules, types, call sites, blast
radius) and grounds new code in what already exists. Full guide:
[KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md).

| Capability | Status | How to use |
|---|---|---|
| Multi-language comprehension + codegen — Python, Java, TypeScript, C#, C, C++, Go | ✅ | automatic per repo |
| SQL data-layer comprehension — schema, queries, stored procedures, migration folding | ✅ | `pip install 'synaptixs-spine[sql]'`; `.sql` per repo |
| SQL greenfield codegen — generate a migration, validate on an ephemeral DB | ✅ | `sdlc feature --language sql` (in-memory SQLite; `SDLC_SQL_ENGINE=postgres` for real Postgres) |
| Framework-aware edges — ASP.NET Core endpoints, EF Core entities (C#) | ✅ | emitted into the PKG on `pkg extract` / `understand` |
| C `#include` graph + header/source merge; codegen on **CMake or Meson** | ✅ | `.c`/`.h` per repo; `sdlc feature --language c` |
| Go — package-per-directory, call graph, **interface satisfaction** (`IMPLEMENTS` by method-set); codegen built + tested with `go build`/`go test`, multi-module aware | ✅ | `pip install 'synaptixs-spine[go]'`; `.go` per repo; `sdlc feature --language go` |
| Doc ingestion — folds Markdown/reST/text docs into the PKG as `Doc` nodes + `MENTIONS` edges (which docs describe a symbol); section-granular, precision-first, no LLM | ✅ | automatic on `orchestrator understand` / `orchestrator state` |
| HTML ingestion — `<h1..h6>` become sections, inline `<code>` binds like a backtick | ✅ | automatic (stdlib, no extra) |
| PDF ingestion — same, for `.pdf` docs (scanned/image-only PDFs skipped, no OCR) | ✅ | `pip install 'synaptixs-spine[docs]'` |
| Office ingestion — `.docx` (Word heading styles → sections, monospace runs → code claims) and `.xlsx` (sheet → section) | ✅ | `pip install 'synaptixs-spine[office]'` |
| Media ingestion (G3) — images/audio/video become `Doc` nodes + `MENTIONS` via a committed `.spine-media/` transcript; model runs only in the opt-in `media extract`, never in the deterministic build | ✅ | `orchestrator media extract` (opt-in); no artifact → skipped, build unchanged |
| Image OCR — architecture diagrams → box/edge labels bound to symbols (local Tesseract, diagram-oriented) | ✅ | `pip install 'synaptixs-spine[media]'` + a `tesseract` binary |
| Audio/video ASR — recorded design reviews → searchable, timestamped transcripts; local Whisper or a remote API (off-machine only with explicit `--allow-remote`) | ✅ | `pip install 'synaptixs-spine[asr]'` (local) or `--asr api` |
| Doc-grounded codegen — a reused symbol's documenting prose rides into the codegen context | ✅ | automatic in `sdlc feature` grounding when the repo has docs |
| Committed `episteme/` for humans + any AI tool | ✅ | `orchestrator understand --out episteme` |
| Current State report — overview, infrastructure/runtime, code structure, **documentation coverage + doc drift**, architecture diagrams (no LLM) | ✅ | `orchestrator state . --lens developer\|stakeholder` |
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
