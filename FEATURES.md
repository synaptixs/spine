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
| **Plan before code** — one reviewable build document per ticket, assembled from the graph, git and the tests; no model call, nothing spent | ✅ | `orchestrator sdlc plan --spec ./TCK-1.json` |
| **Research before design** — every run composes one deterministic **Evidence** artifact: where the ticket lands (symbol, `file:line`, kind, callers, module), a root-cause analysis, and a blast radius keyed off **the landing sites** rather than a design's own proposal. No model, so it costs nothing, and it is written even when the run parks | ✅ | automatic in `sdlc autorun`; `evidence.md` / `evidence.json` |
| **Acceptance criteria bound to your code** — each criterion resolves to a symbol and a `file:line`, or is reported unbound. A criterion naming code the graph does not hold refuses the ticket: a criterion nobody can locate is a test nobody can write. Prose, `ALL_CAPS` env vars, CamelCase and tool names never refuse anything | ✅ | automatic; `criteria.md` |
| **A validator on the design** — a design naming a directory or module the repository does not have parks the run instead of reaching codegen. New files in existing directories are fine; that is a file being created | ✅ | automatic; `design-references.md`. **0 false positives across 100 measured runs** |
| **The run as an inspectable graph** — a row per node with the digest of what it produced, skipped nodes included | ✅ | `orchestrator sdlc explain <run>`; `orchestrator sdlc workflow` prints the validated profile |
| Approve a plan, and refuse to build without one — bound to a digest of what was read, so a plan that changed since reads as stale | ✅ | `orchestrator sdlc approve TCK-1`, `sdlc autorun --no-plan-gate` to skip |
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
| SQL Server database projects — **UTF-16** scripts (SSMS's default) and `GO` batch separators are handled, so a scripted `.Database` project reads instead of being skipped | ✅ | automatic; no flag. On one real project this was the difference between **676 of 709 `.sql` files skipped** and none |
| SQL greenfield codegen — generate a migration, validate on an ephemeral DB | ✅ | `sdlc feature --language sql` (in-memory SQLite; `SDLC_SQL_ENGINE=postgres` for real Postgres) |
| Framework-aware edges — JAX-RS endpoints (Java); ASP.NET Core endpoints and EF Core entities (C#) | ✅ | emitted into the PKG on `pkg extract` / `understand` |
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
| **Measured value of grounding** — the PKG's contribution to codegen is a number, not a claim. 200 runs, 2 frontier models, 5 passes each: **every new module that integrated correctly came from a grounded run (29/50, under `mypy --strict` + placement); the same tickets ungrounded produced none.** On tickets naming the target file both arms tie (98/100) — the graph pays exactly where the model cannot see | ✅ | `scripts/codegen_benchmark.py` (+ `BENCH_NO_GROUNDING=1` for the control); method and bounds in [`codegen-model-comparison-results.md`](docs/specs/codegen-model-comparison-results.md) |
| Committed `episteme/` for humans + any AI tool, with a CI currency gate | ✅ | `orchestrator understand --out episteme`; `orchestrator understand . --check` writes nothing and exits non-zero when the bank no longer matches the code |
| Current State report — overview, infrastructure/runtime, code structure, **documentation coverage + doc drift**, architecture diagrams (no LLM) | ✅ | `orchestrator state . --lens developer\|stakeholder` |
| PKG extraction / export | ✅ | `orchestrator pkg extract`, `orchestrator pkg export` |
| **Measured graph accuracy** — precision and recall per node/edge kind, for **all 8 front-ends**, against a published hand-labelled corpus. Not "grounded" as an adjective; a number you can check | ✅ | `orchestrator pkg accuracy` |
| Runtime oracle — `CALLS` recall from **real execution**, by tracing a repo's own test suite. No labelling needed | 🟡 Python only | `orchestrator pkg accuracy --oracle runtime`. Uses `sys.monitoring` (PEP 669); the other seven front-ends have no equivalent, so "runtime-verified" means "for Python" |
| Per-file route/table parity — where the source declares more than the graph holds, with `file:line` | ✅ | `orchestrator pkg accuracy --oracle parity` |
| Invention detection — `CALLS` edges targeting a name bound in the caller's own scope. Every other check hunts for absence; this hunts for **fiction** | 🟡 Python only | `orchestrator pkg accuracy --oracle invention`. Resolves bindings with Python's `ast`; on other languages every candidate is reported *unexaminable*, which prints as `0` and means "not measured", not "clean" |
| Accuracy regression gate — a committed baseline, and CI failing when a gated number drops | ✅ | `orchestrator pkg accuracy --check` (in the quality gate) |
| Measured caveats in the build document — the blast radius states the recall for its language, so a reader gets a bound rather than a hedge | ✅ | automatic in `sdlc plan` |
| **Intent layer** — `Intent` nodes + `SERVES` edges: which ticket a symbol was last changed for, from git blame and the commit's issue key. Deterministic, no model | 🟡 opt-in, no reader | `orchestrator understand . --intents` / `state --intents`. Costs roughly **3× CPU** (one `git blame` per file, 8 workers). Off by default because **nothing renders or exports these facts** — the only visible effect is a count in the graph-size line, and `pkg export`/`extract` have no `--intents` flag, so the mapping cannot be read back out |
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
