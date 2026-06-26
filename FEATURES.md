# Features & Capabilities — Spine

What Spine can do today, how to invoke each capability, and where to read more.

> **Naming.** *Spine* is the product; it ships as the **`agent-orchestrator`**
> package with the **`orchestrator`** command. Those names stay in install lines and
> commands.

**Status legend:** ✅ shipped & wired · 🟡 mechanism built, partial/operator-gated ·
🔬 experimental (flag-gated, off by default).

Most advanced behavior is **off by default** and turned on with an environment
variable, so the everyday path stays simple. The full variable reference lives in
[OPERATIONS.md](OPERATIONS.md#environment-variable-reference).

---

## 1. Delivery pipeline — requirements → reviewed PR

The core loop: read a requirement, understand the repo, generate grounded code +
tests, get them green, open a PR — with **two human gates** (before building, before
merging).

| Capability | Status | How to use | Deep dive |
|---|---|---|---|
| **Intake** — Confluence / Notion / Markdown → structured intents → specs → backlog | ✅ | `orchestrator ingest --source <uri>`, `orchestrator backlog` | [intake-backlog-progress](docs/specs/intake-backlog-progress.md) |
| **Single feature build** (local & safe by default) | ✅ | `orchestrator sdlc feature --source file://./spec.md --safe` | [USER_GUIDE](USER_GUIDE.md) |
| **Full orchestrated run** (backlog → many features, Temporal) | ✅ | `orchestrator sdlc run --source <uri>` | [SETUP](SETUP.md) |
| **Open / complete a PR** (live) | ✅ | `orchestrator sdlc feature … --live`, `orchestrator sdlc complete` | [USER_GUIDE](USER_GUIDE.md#step-4) |
| **Address review feedback** on an existing PR | ✅ | `orchestrator sdlc address-review --pr <url>` | — |
| **Safe mode** — branch + diff only, no external writes | ✅ | `--safe` flag (default) | [USER_GUIDE](USER_GUIDE.md#step-3) |

---

## 2. Code-grounded understanding (Product Knowledge Graph)

Before generating, Spine builds a **PKG** of the target repo — modules, types,
functions, call sites, blast radius — and grounds new code in what already exists.

| Capability | Status | How to use | Deep dive |
|---|---|---|---|
| **PKG extraction** (read-only facts) | ✅ | `orchestrator pkg extract <repo>` | [pkg-code-grounded-understanding](docs/specs/pkg-code-grounded-understanding.md) |
| **Multi-language comprehension** — Python, Java, TypeScript | ✅ | automatic per repo | [multi-language-java](docs/specs/multi-language-java.md), [typescript-codegen](docs/specs/typescript-codegen.md) |
| **Multi-language codegen** — Python, Java, TypeScript | ✅ | automatic per repo | [java-codegen](docs/specs/java-codegen.md) |
| **`understand`** — committed, code-true `memory-bank/` for humans + any AI tool | ✅ | `orchestrator understand --out memory-bank` | [project-comprehension-memory-bank](docs/specs/project-comprehension-memory-bank.md) |
| **PKG export** (ontomesh-ready SQLite projection) | ✅ | `orchestrator pkg export`, `orchestrator pkg docs` | [PRODUCT-KNOWLEDGE-GRAPH](docs/specs/PRODUCT-KNOWLEDGE-GRAPH.md) |
| **Repo profile / audit** | ✅ | `orchestrator profile <repo>`, `orchestrator audit <repo>` | — |

---

## 3. Governed autonomy

The workflow itself is a typed, validated artifact: a planner decomposes the
objective, a runtime executes it, and **per-edge verifiers** check every step against
schemas, evidence, and policy. Failures trigger replan, human approval, or a clean stop.

| Capability | Status | How to use | Deep dive |
|---|---|---|---|
| **Planner → validated GraphIR** | ✅ | internal to `sdlc run` | — |
| **Human approval gates** (before build, before merge) | ✅ | `--safe`; API `/v1/approvals` | [SETUP](SETUP.md#8-common-workflows) |
| **In-loop human pause** (mid-run approval) | 🟡 | governed-run gating | [bet2c-in-loop-approval](docs/specs/bet2c-in-loop-approval.md) |
| **Policy + budget guardrails** | ✅ | `SDLC_RUN_BUDGET_USD`, `SDLC_AGENTIC_POLICY` | [OPERATIONS](OPERATIONS.md) |
| **Audit trail** — every tool call, approval, decision | ✅ | persisted; `/v1/tasks/<id>/trace` | [SETUP](SETUP.md) |
| **RBAC / multi-tenancy** | 🟡 | `ORCHESTRATOR_PRINCIPALS`, `ORCHESTRATOR_TENANT_ID` | [bet2c-rbac-multitenancy](docs/specs/bet2c-rbac-multitenancy.md) |
| **Export / replay** a run | ✅ | trust-spine export | [bet2-trust-spine](docs/specs/bet2-trust-spine.md) |

---

## 4. Smarter codegen — the agentic loop

Beyond single-shot generation: profile → plan (catalog) → gate → a ReAct tool-use
loop conditioned by skills and governed MCP tools.

| Capability | Status | How to use | Deep dive |
|---|---|---|---|
| **Catalog-then-compose** — assemble the right capabilities per project | ✅ | `orchestrator catalog list`, `orchestrator catalog plan` | [catalog-then-compose-roadmap](docs/specs/catalog-then-compose-roadmap.md) |
| **Agentic (ReAct) codegen loop** | 🔬 | `export SDLC_AGENTIC_CODEGEN=1` | [phase5-agentic-codegen-loop](docs/specs/phase5-agentic-codegen-loop.md) |
| **Convention learning** (output matches the repo's idioms) | ✅ | automatic | [USER_GUIDE](USER_GUIDE.md#step-8) |
| **Clarifying questions** when a spec is ambiguous | ✅ | automatic | — |
| **Sandboxed test execution** | 🟡 | `SDLC_TEST_ISOLATION` | [sandboxed-test-execution](docs/specs/sandboxed-test-execution.md) |
| **Local / offline models** (Ollama, any OpenAI-compatible) | ✅ | `SDLC_CODEGEN`, `ORCHESTRATOR_INTAKE_MODEL` | [USER_GUIDE](USER_GUIDE.md#step-6) |

---

## 5. Personas — non-SWE jobs on the same machinery

The same catalog + agentic-loop substrate, retargeted at other roles.

| Capability | Status | How to use | Deep dive |
|---|---|---|---|
| **PR Reviewer** (standalone, GitHub App) | ✅ | `codereview` module / GitHub App | [persona-skill-system](docs/specs/persona-skill-system.md) |
| **Auditor persona** | ✅ | persona registry | [standout-evals-and-personas](docs/specs/standout-evals-and-personas.md) |
| **Persona-agnostic eval harness** | ✅ | `evals` module | [persona-skill-measurement](docs/specs/persona-skill-measurement.md) |

---

## 6. Memory & observability

| Capability | Status | How to use | Deep dive |
|---|---|---|---|
| **Cross-run semantic memory** (lessons persist across runs) | ✅ | `ORCHESTRATOR_SEMANTIC_MEMORY=1` | [cross-run-semantic-memory](docs/specs/cross-run-semantic-memory.md) |
| **Committed memory bank** (`memory-bank/*.md`) | ✅ | `orchestrator understand` | [knowledge-vision](docs/specs/KNOWLEDGE-VISION.md) |
| **Live OpenTelemetry tracing** | ✅ | `OTEL_EXPORTER_OTLP_ENDPOINT` | [live-observability-otel](docs/specs/live-observability-otel.md) |

---

## 7. Integrations (MCP)

| Capability | Status | How to use | Deep dive |
|---|---|---|---|
| **Consume external MCP servers** (DBs, Atlassian, …) | ✅ | `orchestrator mcp ingest-db`, `mcp list`, `mcp call` | [USER_GUIDE](USER_GUIDE.md#step-9) |
| **Spine as an MCP server** (drive it from Claude / Codex / IDE) | ✅ | `plugin` surface; remote HTTP/OAuth | [USER_GUIDE](USER_GUIDE.md#step-10) |
| **Atlassian intake source** (Confluence/Jira via MCP) | ✅ | `ORCHESTRATOR_MCP_*` | [USER_GUIDE](USER_GUIDE.md#step-9) |

---

## 8. The semantic spine (ontomesh × Spine × infodrift)

A shared **`EntityKey`** (`Component_vX::Region::Interface`) joins a domain concept →
code symbol → deployment unit → drift signal, so a production drift becomes a
grounded, governed, provenance-carrying code fix. **All gated, inert unless configured.**

| Seam | Status | How to use | Deep dive |
|---|---|---|---|
| **Seam 1 — domain-grounded build** (ontomesh → codegen) | ✅ wired | `SPINE_ONTOMESH_URL` + `SPINE_ONTOMESH_FLAVOR` | [OPERATIONS](OPERATIONS.md#seam-1--ontomesh-domain-grounding) |
| **Seam 3 — drift → governed remediation** (infodrift → PR) | 🟡 | `orchestrator sdlc remediate --report drift.json` | [OPERATIONS](OPERATIONS.md#seam-3--drift--governed-remediation) |
| **Seam 2 — register shipped units** (Spine → infodrift) | 🟡 | `SPINE_INFODRIFT_URL` + `SPINE_DEPLOY_TOPOLOGY` (needs a shim) | [OPERATIONS](OPERATIONS.md#seam-2--register-shipped-units) |
| **Unified lineage / provenance** | 🟡 | `LineageIndex` (in-memory) | [tri-repo-integration](docs/specs/tri-repo-integration.md) |

**End-to-end walkthrough:** the [Spine vignette runbook](docs/specs/spine-vignette-runbook.md)
runs the whole loop on one entity — drift signal → scoped, ontology-grounded,
human-gated remediation branch with provenance.

---

## 9. Distribution

| Capability | Status | How to use |
|---|---|---|
| **pip install** (TestPyPI via OIDC) | ✅ | see [SETUP](SETUP.md) for the exact one-liner |
| **`init` / `doctor`** — scaffold `.env`, check readiness | ✅ | `orchestrator init && orchestrator doctor` |
| **Developer TUI** | ✅ | `orchestrator tui` |
| **Web dashboard + REST API** | ✅ | see [USER_GUIDE Step 7](USER_GUIDE.md#step-7) |

---

## See also

- **[OPERATIONS.md](OPERATIONS.md)** — operator & developer guide: deployment modes,
  the full environment-variable reference, and how to stand up / configure each
  advanced capability (including the Spine seams).
- **[SETUP.md](SETUP.md)** — install + local stack.
- **[USER_GUIDE.md](USER_GUIDE.md)** — step-by-step everyday workflow.
- **[docs/specs/](docs/specs/)** — design specs behind each capability.
</content>
</invoke>
