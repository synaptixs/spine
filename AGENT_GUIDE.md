# Using Spine from an agent

Spine exposes the same MCP tools to Claude Code, Codex, and other MCP hosts: understand
a repository, plan a change, generate and test code, and open a pull request when authorized.
The host-specific installation and configuration are in sections 3 and 4; the workflow is shared.

New to Spine? Start with the [worked example](EXAMPLE.md). The [user guide](USER_GUIDE.md)
covers the everyday CLI workflow.

## Contents

1. [How it fits together](#1-how-it-fits-together)
2. [Prerequisites](#2-prerequisites)
3. [Install](#3-install)
4. [Credentials](#4-credentials)
5. [Verify the connection](#5-verify-the-connection)
6. [The tools Spine exposes](#6-the-tools-spine-exposes)
7. [Walkthrough — greenfield](#7-walkthrough--greenfield)
8. [Walkthrough — brownfield](#8-walkthrough--brownfield)
9. [Safe vs. live (the write gate)](#9-safe-vs-live-the-write-gate)
10. [Language support & toolchains](#10-language-support--toolchains)
11. [Troubleshooting](#11-troubleshooting)
12. [Updating & uninstalling](#12-updating--uninstalling)

## 1. How it fits together

Your host talks to Spine over MCP (Model Context Protocol). It launches the local
`orchestrator-mcp` server as a subprocess and calls its capabilities as tools. Each tool
runs the real engine: PKG grounding, codegen, tests and refinement. Generated changes land
in a scratch workspace before you choose to push.

Use either a packaged plugin, which bundles the MCP server configuration, or a raw MCP
entry for direct control over its command, environment and paths. Both expose the same tools.

## 2. Prerequisites

- An MCP host installed; host-specific notes are in [Install](#3-install).
- Python 3.12+ and the [Spine prerequisites](SETUP.md).
- A model provider key or local Ollama endpoint for generation. Read-only comprehension
  and deterministic planning work without a model key.
- Language build tools only when building/testing generated code: see [toolchains](#10-language-support--toolchains).
- GitHub access for live PRs; tracker credentials when creating real tickets.

## 3. Install

Install the engine once so `orchestrator-mcp` is on PATH:

```bash
uv tool install 'synaptixs-spine[all]'
```

`[all]` includes language front-ends, document ingestion and the MCP server. `[mcp]`
alone does not install the language grammars; `[languages]` contains the front-ends.
`[all]` additionally installs the optional `[clang]` semantic pass; see
[its size and coverage limits](SETUP.md#optional-extras).
See [SETUP.md](SETUP.md) for the complete installation options.

### Claude Code

Use the Claude Code CLI or IDE extension on macOS, Linux or Windows. In a session:

```text
/plugin marketplace add synaptixs/spine
/plugin install spine@spine
```

For a local checkout, use `/plugin marketplace add ./` instead. Restart or run
`/reload-plugins`; check `/plugin` and `/mcp` for the enabled plugin and connected server.
The plugin includes the `understand-codebase` skill.

To refresh the marketplace, run `/plugin marketplace update spine`. To remove it:

```text
/plugin uninstall spine@spine
/plugin marketplace remove spine
```

### Codex

On macOS the app bundles its CLI at `/Applications/Codex.app/Contents/Resources/codex`.
Use that path or an existing `codex` executable on PATH; the standalone CLI is available
through `npm i -g @openai/codex` or `brew install codex`.

```bash
codex plugin marketplace add synaptixs/spine
codex plugin add spine@spine
codex plugin list
```

For a local checkout, use `codex plugin marketplace add ./codex-marketplace` instead.
Restart the app; verify the server with `codex mcp list`.

To refresh the marketplace, run `codex plugin marketplace upgrade`. To remove it:

```bash
codex plugin remove spine@spine
codex plugin marketplace remove spine
```

For a raw MCP setup instead of a plugin, use your host's configuration below.

## 4. Credentials

Follow [SETUP → Credentials and model selection](SETUP.md#credentials-and-model-selection)
for `.env` values. When the host starts from another directory, set
`ORCHESTRATOR_DOTENV` to the **absolute** `.env` path as shown below.
Read-only comprehension and deterministic planning need no provider key.

### Claude Code configuration

Use a project-scoped `.mcp.json` at the repository root:

```json
{
  "mcpServers": {
    "spine": {
      "command": "orchestrator-mcp",
      "args": [],
      "env": {"ORCHESTRATOR_DOTENV": "/abs/path/to/your/.env"}
    }
  }
}
```

Or register it with `claude mcp add spine --env ORCHESTRATOR_DOTENV=/abs/path/to/your/.env -- orchestrator-mcp`.
Launching Claude Code from the project containing `.env` also supplies the default path.
Restart or use `/reload-plugins`, then verify with `/mcp`.
Remove the `spine` entry from `.mcp.json` to uninstall a raw server.

### Codex configuration

Add this to `~/.codex/config.toml`:

```toml
[mcp_servers.spine]
command = "orchestrator-mcp"
args = []
startup_timeout_sec = 60
tool_timeout_sec = 600

[mcp_servers.spine.env]
ORCHESTRATOR_DOTENV = "/abs/path/to/your/.env"
```

An absolute `command` path can select a particular installation. The longer tool timeout
allows codegen and builds to finish. Restart Codex and verify with `codex mcp list`.
Remove the `[mcp_servers.spine]` block to uninstall a raw server.

## 5. Verify the connection

Ask your assistant: **"Use Spine's `doctor` tool and show me what's ready."**

The report identifies the server version, interpreter and MCP SDK, plus provider, source,
tracker and GitHub readiness. Missing provider credentials do not prevent read-only use.
If the server is missing, follow your host's checks in [Install](#3-install).
For other failures, see [Troubleshooting](#11-troubleshooting).

## 6. The tools Spine exposes

The inventory below is generated from the registered tools, their access tiers and
output schemas. Workflow examples follow the table.

<!-- BEGIN GENERATED MCP TOOLS -->
<!-- Generated by scripts/mcp-tools.py; edit registration metadata, not this table. -->

| Tool | What it does | Read-only hint | HTTP scope | Output schema |
|---|---|---|---|---|
| `doctor` | Report environment readiness (LLM provider, Confluence/Jira, MCP, …) and which install is answering: `server` carries the package version, the interpreter, the MCP SDK version and the extras present — so a stale console script on a host's PATH is visible from the host, not just from a shell. | yes | `spine:read` | `DoctorOut` |
| `ingest_preview` | Preview the backlog for a requirements source — dry-run, writes nothing. | yes | `spine:read` | `IngestPreviewOut` |
| `pkg_grounding` | Existing-code context a repo's Product Knowledge Graph surfaces for a spec. | yes | `spine:read` | `PkgGroundingOut` |
| `read_memory_bank` | Read a repo's committed memory bank (`memory-bank/`) — code-true project knowledge built by `orchestrator understand`. | yes | `spine:read` | `ReadMemoryBankOut` |
| `map_repo` | A skim-first map of a repo: languages, components, **call-hotspots**, **test-coverage gaps**, and prioritized **recommendations**. Deterministic (no LLM). `lens` is `developer` (technical) or `stakeholder` (plain language). `repo_path` is a local path or a git URL. | yes | `spine:read` | `MapRepoOut` |
| `blast_radius` | "What breaks if I change X" — a symbol's direct callers plus the cross-layer set a change ripples into (CALLS + IMPORTS + REFERENCES), each with `file:line`. Deterministic. | yes | `spine:read` | `BlastRadiusOut` |
| `explain_symbol` | What a symbol is and how it connects: kind, location, who calls it, what it calls, and what it contains. Deterministic (no LLM). | yes | `spine:read` | `ExplainSymbolOut` |
| `investigate` | Where a ticket lands in the code: the real symbols to start from (`file:line` + caller counts), the owning areas, and any committed `episteme/` knowledge. Deterministic (no LLM). | yes | `spine:read` | `InvestigateOut` |
| `localize` | Resolve a stack trace / traceback to the repo symbols it names, pointing at the likely fault site and its callers. Deterministic (no LLM). | yes | `spine:read` | `LocalizeOut` |
| `regression_gaps` | Blast-radius test-coverage gaps for a change: the production symbols a change to `symbol` (or the fault site in `trace`) reaches that **no test covers**. Deterministic. | yes | `spine:read` | `RegressionGapsOut` |
| `root_cause` | A grounded root-cause report for a bug (a stack trace, an error message, or a description): the fault site, ranked root-cause **hypotheses** with evidence, the regression surface a fix must cover, and a scoped fix approach. **Deterministic by default** (no LLM, no credentials); `use_llm=true` opts into LLM-enriched hypotheses (needs a model). Stops at analysis — it never changes code. | yes | `spine:read` | `RootCauseOut` |
| `pkg_joins` | Propose or check cross-repository joins. Read-only — it never writes a config. | yes | `spine:read` | `PkgJoinsOut` |
| `sdlc_plan` | The twelve-section **build document** for one ticket: requirement, intent, root cause, what the graph knows, blast radius, design, files, acceptance criteria, the codegen prompt, cost and confidence. **Deterministic — no LLM, no credentials, nothing spent.** Every section says where it came from. Hand it a `spec` object (title, summary, acceptance_criteria, and optionally met_criteria mapping a criterion already satisfied by existing code to the evidence). Stops at the document — it never changes code. | no | `spine:plan` | `SdlcPlanOut` |
| `sdlc_approve` | Record that a **human** read a build document and decided. Binds the decision to a digest of the document body, so a plan that changes afterwards reads as *stale* rather than silently still approved — and `sdlc autorun` refuses to build without a current one. Needs `sdlc_plan` to have produced the document first. `decided_by` defaults to the repo's git identity; a decision nobody is named for is not recorded. | no | `spine:plan` | `SdlcApproveOut` |
| `docs_for` | Which docs describe the code — the doc-ingestion surface. With a `symbol`, the doc pages that **MENTION** it (grounded to the repo's docs). Without one, a doc-coverage summary: how many docs are ingested, how many symbols they name, and the top **potential drift** (doc claims the graph can't resolve — renamed/removed code). Deterministic (no LLM). `repo_path` is a local path or a git URL. | yes | `spine:read` | `DocsForOut` |
| `sdlc_feature` | Build ONE intent end to end: spec → grounded codegen → tests → branch. Reports progress per stage (spec, layout, design, implement, tests, refine, judge, PR) when the host asks for it. | no | `spine:run` | `SdlcFeatureOut` |
| `sdlc_start_run` | Start the autonomous, gated SDLC workflow. Returns a run id immediately. | no | `spine:run` | `SdlcStartRunOut` |
| `sdlc_run_status` | Poll a run: Temporal workflow status + the gate (if any) awaiting a decision. | yes | `spine:read` | `SdlcRunStatusOut` |
| `sdlc_decide_gate` | Decide a pending gate so the run can continue (or stop). | no | `spine:run` | `SdlcDecideGateOut` |
| `sdlc_run_result` | Fetch a run's final result once it has COMPLETED (status only otherwise). | yes | `spine:read` | `SdlcRunResultOut` |
| `understand_repo` | Build a repo's `episteme/` knowledge base — or, with `check=true`, verify the committed one still matches the code. **Deterministic, no LLM, no credentials**: the same pages `orchestrator understand` writes, so an assistant can bootstrap a repo that has no bank yet, then `read_memory_bank` it. Writes only under `episteme/` (or `out`). `check` writes nothing and reports the pages that are missing, stale (the code moved on) or orphaned (describing code that is gone). `refresh` re-extracts the graph instead of using the commit cache. A **build on a git URL is refused** unless `out` is an absolute directory — the clone vanishes, and a bank written into it with it; `check` on a URL is fine. Returns the three entry pages and the counts, not every path. | no | `spine:plan` | `UnderstandRepoOut` |
| `profile_repo` | Profile a project: languages, framework, database and migrations, test runner, and — given an `intent` title — the task type Spine would classify it as. Read-only, deterministic; the same profile the catalog uses to pick skills. `repo_path` is a local path or a git URL. | yes | `spine:read` | `ProfileRepoOut` |
| `design_change` | A grounded design for one feature: the spec × the knowledge graph → an approach anchored to the repo's real structure, its **blast radius** (modules touched, who depends on them, call hotspots) and any **unverified references** (paths the spec names that the graph does not have). Takes the same `spec` object as `sdlc_plan` (`intent_id`, `title`, `summary`, `acceptance_criteria`), validated the same way. **Deterministic by default**; `use_llm=true` lets a model write the prose (needs `ORCHESTRATOR_INTAKE_MODEL`). Returns the design and its markdown — it never writes. For the full twelve-section build document, use `sdlc_plan`. | yes | `spine:read` | `DesignChangeOut` |
| `sdlc_baseline` | Score the run agent against a corpus of tickets whose right answer is known, and summarize the durable run records. **Deterministic and free**: the validity gate reads each ticket and this repo's real graph; run metrics are observations of what ran. False refusals and missed refusals are counted separately — one accuracy number would let each hide behind the other. `repo_path` is a local path or a git URL. | yes | `spine:read` | `SdlcBaselineOut` |
| `sdlc_address_review` | Address the human review comments on an open PR and **push a fix to its branch**: clone the repo, check the PR out, feed the comments to codegen, re-drive the change to green (tests + preflight), push. **Gated — needs `confirm=true`**: there is no local mode, a successful call writes to the PR. Spends tokens. Needs `git`, an authenticated `gh`, a model, and the run backend's database. `repo` defaults to `SDLC_REPO_URL`; `bot_login` skips the agent's own comments. | no | `spine:run` | `SdlcAddressReviewOut` |
| `sdlc_complete` | Close the tracker issue for a **merged** PR — the merge → Done bookend. Verifies the PR is merged (`gh`), derives the issue key from its head branch (`feat/<sdlc_id>/<KEY>`) unless `issue` is given, transitions the issue to `status`, comments the merge, and marks the backlog intent done. **Gated — needs `confirm=true`**: it writes to the tracker for real, never dry-run. Needs an authenticated `gh` and Jira credentials. | no | `spine:run` | `SdlcCompleteOut` |
| `sdlc_remediate` | Turn an infodrift drift report into remediation feature runs, one per material finding at or above `min_severity` (`warning` &#124; `critical`). `report_path` is the `full_report` JSON, `mappings_path` the confirmed code↔ontology mapping store — both on this machine. Spends tokens. Safe by default (`live=false`): each task leaves a local branch + diff; `live=true` opens PRs and is **gated on `confirm=true`**, like `sdlc_feature`. `repo` defaults to `SDLC_REPO_URL`. | no | `spine:run` | `SdlcRemediateOut` |
| `audit_repo` | A codebase-auditor persona reads the repo — the graph plus file reads, **no writes** — and reports findings anchored to a real `file:line` (claims that resolve to nothing are listed separately as `unresolved`). **Spends tokens**: needs a tool-calling model (`ORCHESTRATOR_INTAKE_MODEL`). `repo_path` is a local path or a git URL; `focus` says what to look for. Progress is start and done only — the audit loop has no per-step hook. | yes | `spine:run` | `AuditRepoOut` |
| `registry_runs` | Recent SDLC runs at the registry, most recently active first: id, state (`running` / `merged` / `failed` / `denied` / `cancelled`), last action and timestamps. Read-only; scoped to the API key's tenant. Needs the registry up (`orchestrator up`). `limit` is capped at 200 by the server. | yes | `spine:read` | `RegistryRunsOut` |
| `registry_approvals` | Pending approvals at the registry — the gates waiting on a human — latest first, each with its id, title, risk classification and the run it belongs to. Read-only. Decide one with `registry_decide`. Needs the registry up. | yes | `spine:read` | `RegistryApprovalsOut` |
| `registry_decide` | Decide a pending approval at the registry so its run continues (or stops). | no | `spine:run` | `RegistryDecideOut` |
| `registry_trace` | A run's trace at the registry: the newest `tail` audit entries and tool invocations, the verifier outcome, and the replan count against its budget. Bounded — `truncated` says how many older entries were left out. Read-only. Pass the run id as `registry_runs` lists it — the same id the web console links its trace with. | yes | `spine:read` | `RegistryTraceOut` |

<!-- END GENERATED MCP TOOLS -->

> **Graph queries are read-only, credential-free and deterministic** (only
> `root_cause`'s and `design_change`'s opt-in `use_llm` use a model).
> `understand_repo` writes the knowledge base; its tier and scope are shown above. There is no `state` tool
> because `map_repo` *is* `orchestrator state` — same engine, same rendering. They ship with an **`understand-codebase` skill**
> that tells the assistant *when* to reach for each — so you can just ask in plain language and the assistant picks
> the tool. Try: *"Map this repo and tell me what's untested,"* or *"What breaks if I change
> `create_app`?"* `repo_path` is a **local path or a git URL** (shallow‑cloned behind the CLI's host
> allow‑list); each returns structured fields **plus** a `markdown` rendering, bounded (top‑N +
> `file:line`). To serve them to a **remote** host over HTTP, run the streamable‑HTTP server
> (`orchestrator-mcp --http`, bearer/OAuth auth from env) — it registers the same tools, and
> **checks a scope per tier** on each call: `spine:read` (comprehension, observing a run),
> `spine:plan` (`sdlc_plan`, `sdlc_approve`), `spine:run` (`sdlc_feature`, `sdlc_start_run`,
> `sdlc_decide_gate`, `registry_decide`). A token missing the tier's scope gets `error` + `needs`
> + `has` back, not a 403. A static token carries all three unless
> `ORCHESTRATOR_MCP_REQUIRED_SCOPES` narrows it (`spine:read` = a read-only token); the legacy
> `sdlc` scope is retired — grant the three. Over stdio there is no token and no check.

### Prompts and resources — the workflow and the documents, through the protocol

Two things the plugin exposes besides tools, for any MCP host:

**Prompts** carry the *which tool, in which order* workflow the `understand-codebase` skill
describes, so a host without skill support gets the same ordered
guidance. A host lists them and fills the arguments in its own UI.

| Prompt | Arguments | Walks the host through |
|---|---|---|
| `orient` | `repo_path` (optional) | `map_repo` → `read_memory_bank` |
| `investigate-ticket` | `title`, `problem` | `investigate` → `blast_radius` → `regression_gaps` — change nothing |
| `triage-bug` | `bug` | `localize` → `regression_gaps` → `root_cause` — analysis only |
| `plan-then-approve` | `title`, `summary`, `criteria` | `sdlc_plan` → a **human** reads → `sdlc_approve` → only then `sdlc_feature` |
| `whats-waiting-on-me` | — | `registry_approvals` → `registry_trace` → `registry_decide` (a rejection ends the run) |

**Resources** are the documents Spine has already written, readable by URI and attachable as
context — a tool result scrolls away; a resource can be read again.

| URI | Content |
|---|---|
| `spine://bank` | The committed `episteme/` index and section list — or a note that `understand_repo` builds one |
| `spine://bank/{section}` | One page: `architecture`, `domain-model`, `conventions`, … |
| `spine://plans` | The build documents under `.spine/plans`, each with its approval state |
| `spine://plan/{intent_id}` | One build document, approval state on top |
| `spine://state` | The current‑state report (developer lens), from the commit‑keyed cache |

Resources describe the **default repository**: the process working directory, or
`SPINE_REPO_ROOT` when set — a stdio plugin is launched per project, so that is the repo the host
is in. The tools keep taking `repo_path` as before. All read‑only; over HTTP the `spine:read` floor
covers them.

### Progress from the long tools

`sdlc_feature`, `understand_repo`, `sdlc_remediate`, `sdlc_address_review` and `audit_repo` run for
seconds to minutes. When the host asks for progress (an MCP progress token on the call — most
hosts send one), each reports it as it goes: `sdlc_feature` per stage in the runner's own order
(spec, layout, design, implement, tests, refine, judge, PR), `sdlc_remediate` per task,
`sdlc_address_review` at checkout / respond / done, `understand_repo` at extract / write, and
`audit_repo` at start / done (its loop has no per‑step hook). The bar is monotonic — a second test
run after a refine does not move it backwards — and a line the stage table does not know rides
on the current step as its message. Nothing changes in the tools' schemas, and a client that sends
no token sees exactly what it saw before.

### What each tool returns, as a schema

Every tool advertises an **output schema** — a type per tool, with no required key, because
every tool has an error path (`error` + `hint`) and most have several shapes (found / not
found; single‑repo / multi‑repo). A host reads the fields — `found`, `matches[].where`,
`uncovered_elsewhere` — before it calls, and the structured result validates against them. A key
the type happens to miss still reaches the host (the types allow extras); the test suite's drift
guard is what keeps the declarations honest. Open shapes — the twelve‑section build document, a
run's Temporal status, the engine's design dict — stay untyped objects on purpose.

### Who did what, over HTTP

Over stdio a local subprocess acts for one user; nothing to attribute. Over HTTP several people
may share one server, so every **run‑scope** call (`sdlc_feature`, `sdlc_start_run`,
`sdlc_decide_gate`, `registry_decide`, `sdlc_address_review`, `sdlc_complete`, `sdlc_remediate`,
`audit_repo`) and every **scope denial** is recorded against the token's principal in the
registry's audit log (`POST /v1/audit`, with the plugin's own `ORCHESTRATOR_API_KEY` as the
writer): the principal, the tool, its scope, the argument *names* and a digest of the values —
never the values — and the outcome. `registry_trace` and `GET /v1/audit?resource_type=mcp_tool`
read it back. A registry that is down degrades to a log line; the audit never fails the call.

> **The tiers are metadata your host can act on.** Every tool is registered with MCP tool
> annotations derived from its tier — *read‑only*, *destructive*, *idempotent*, *open‑world* — so a
> host that asks before destructive calls asks before `sdlc_feature`, `sdlc_start_run` and
> `sdlc_decide_gate`, and not before `map_repo`. The comprehension tools are read‑only and
> idempotent; `sdlc_plan`/`sdlc_approve` write (under `.spine/`) but destroy nothing; only
> `doctor`, `pkg_joins`, `sdlc_plan` and `sdlc_approve` never leave the machine — everything else
> may clone a URL or read a remote source. See [`docs/specs/mcp-plugin-surface.md`](docs/specs/mcp-plugin-surface.md).

### Asking across several repositories

The comprehension tools take **one** repository by default, and for a service that is called
over HTTP that is a trap rather than a limitation. `blast_radius` on a route handler reports
**`0 caller(s)`** — which is *true*, nothing in its own source calls it — and reads as safe to
change.

Declare your services in a **`.spine/repos.yaml`** and pass it as `repos=` — to `blast_radius`,
`investigate`, `explain_symbol`, `regression_gaps`, `localize` or `docs_for` — instead of `repo_path`:

```yaml
repos:
  billing: ../billing
  web: ../web
joins:
  - kind: http
    consumer: web
    provider: billing
```

**An Android app can be the consumer.** Retrofit interfaces are read as HTTP *calls*, not
routes — an app serves nothing, it calls something — so a Kotlin mobile repo joins to a
provider in any front-end that emits endpoints — Java, Kotlin, C#, Go, PHP, Perl, Python,
TypeScript or JavaScript (Express) — by verb and path like any other consumer. That makes
"which screens break if this service drops `GET /topics`" answerable, which no front-end
could do before. The path comes from the annotation; a `baseUrl(...)` that is not a string
literal (the usual case, since it comes from build config) leaves the call path-only.

**And a Kotlin service can be the provider.** Ktor `routing { get("/topics") { … } }` and
Spring MVC controllers become `Endpoint`s in the same namespace, so both ends of that join
can now be Kotlin — the shape a team shipping an app and its backend in one language
actually has. The Spring half is shared with the Java front-end, which read JAX-RS only.

The same question then crosses the boundary:

```
- **billing** · `create_order` (Function, 0 caller(s), **1 dependent(s) in other repos**) — billing:app/routes.py:7
```

Three things worth knowing:

- **The topology is declared, not guessed.** A `joins:` entry *narrows* the search; it does not
  create the edge — matching `POST /v1/orders/42` against `POST /v1/orders/{id}` is still
  resolution, done segment by segment, and refused outright when two endpoints match.
- **A forgotten join is quiet.** A repository nobody listed is loud (no nodes, a visibly
  narrower graph); a missing `joins:` entry looks exactly like two services that are not
  coupled — which reads as health. Run `pkg_joins` with `mode="check"` before trusting a clean
  result, and `mode="propose"` to see what the evidence supports. Neither writes anything.
- **A single‑repo answer will tell you when it is one.** Point a tool at a repo that declares
  siblings in its own `.spine/repos.yaml` and the result carries a `multi_repo_available` note
  naming the config and the repos it declares. Nothing errors — extracting one directory always
  succeeds — so without the note a partial answer would be indistinguishable from a complete
  one. It is a note, never a switch: which repositories an answer covers stays your decision.
- **Every multi‑repo answer carries a `standing` block** — the repos it covers, and whether the
  result is `reproducible`. A merged graph built over a repo with uncommitted work looks
  identical to one built over clean trees, so the answer says which it was.

`map_repo` stays single‑repo: there is no merged profile behind it. Call it per repository.

### Using the `understand-codebase` skill

Where the host supports the bundled skill, ask naturally: "Map this repo and tell me
what's untested," "What breaks if I change `create_app`?", or paste a traceback.
The skill sequences the comprehension tools; MCP prompts provide the same workflow to
other hosts. It reads code; generation uses the gated `sdlc_feature` tool.

Each tool below shows the **assistant prompt** (what you type), the **tool call** it maps to
(the literal arguments — handy if you call it programmatically or want to be precise), and
**returns** (the shape of the result). Arguments not shown use their defaults.

---

#### `doctor`

Checks what's wired up. Run this first.

> **Ask your assistant:** "Use spine's `doctor` and summarize what's ready."

```jsonc
// tool: doctor   (no arguments)
{}
```
**Returns:** `{ "all_passed": false, "checks": [ { "name": "llm", "passed": true, "detail": "anthropic/claude-opus-5" }, … ] }`

---

#### `pkg_grounding`

Read‑only preview of what Spine would *reuse* in an existing repo for a given idea — the
real symbols, with `file:line`. Great for "what will it build on?" before you commit.

> **Ask your assistant:** "Use spine's `pkg_grounding` on `repo_path=/path/to/my/repo` for the spec
> 'add rate limiting to the public API', and summarize what it found."

```jsonc
// tool: pkg_grounding
{
  "repo_path": "/path/to/my/repo",
  "spec_text": "add rate limiting to the public API"
}
```
**Returns:** `{ "chars": 6099, "context": "…ranked APIs/types with file:line provenance…" }`
(empty `context` ⇒ greenfield / nothing relevant.)

---

#### `read_memory_bank`

Reads a repo's committed `episteme/` (the code‑true knowledge `orchestrator understand`
writes). Omit `section` for the index; pass one to read it.

> **Ask your assistant:** "Use spine's `read_memory_bank` on `repo_path=/path/to/my/repo`, section
> `architecture`."

```jsonc
// tool: read_memory_bank
{
  "repo_path": "/path/to/my/repo",
  "section": "architecture"          // optional; omit to list sections + index
}
```
**Returns:** the section list + index (no `section`), or that section's markdown.

---

#### `ingest_preview`

Turns a requirements source into a backlog **without writing anything** — see the intents
Spine derives and any gaps, before running a feature.

> **Ask your assistant:** "Use spine's `ingest_preview` on `file://./roadmap.md` and list the intents."

```jsonc
// tool: ingest_preview
{
  "source": "file://./roadmap.md"    // or confluence://<id>, notion://<id>
}
```
**Returns:** `{ "documents": 1, "intent_count": 3, "intents": [ { "id": "intent-1", "title": "…" } ], "gap_count": 0, "blocked": false }`

---

#### `sdlc_plan`

**The build document for one ticket, before anything is built.** Twelve sections in fixed
order, each labelled with where it came from — quoted from the ticket, computed from the
graph, inferred by a model, or decided by a person. **No model call, no credentials,
nothing spent.**

```jsonc
// tool: sdlc_plan
{
  "repo_path": ".",                  // local path or git URL
  "spec": {
    "intent_id": "PROJ-14",
    "title": "CLI crashes when the registry API is down",
    "summary": "Quote the actual error and name the real files; known paths identify the fault module.",
    "acceptance_criteria": ["…one independently checkable statement per entry…"],
    "met_criteria": {
      "Any other non-success HTTP status surfaces the status code…":
        "src/orchestrator/cli.py:134 — _check() already does this"
    }
  }
}
```

**Returns** the rendered `document` **and** the `path` it was persisted to
(`.spine/plans/<INTENT>-build.md`), plus `superseded` when it replaced an older version.

**A spec it cannot validate is refused**, with the specific problem and the valid field
names — so if you drafted the spec, read the error and fix it rather than working around it.

> **`met_criteria` is the field worth your attention.** It maps a stated criterion to the
> evidence that existing code *already satisfies it*. No deterministic pass can make that
> call — but you can: read the ticket, then check with `explain_symbol` or `blast_radius`
> before filling it in. On the ticket this was built from, two of six criteria described
> behaviour that already existed, and a run would have reported them met having changed
> nothing. That is the single most valuable thing you can add to a plan.

**Where this matters most:** a machine with no model API key. You have the model and the
tracker credentials; Spine has the graph. Read the ticket yourself, draft the spec, and call
this — the document comes back grounded, and Spine never needed a key.

#### `sdlc_approve`

Records that a **human** read the document and decided. Binds to a digest of it, so a plan
that changes afterwards reads as *stale* rather than still approved, and `sdlc autorun`
refuses to build without a current one.

```jsonc
// tool: sdlc_approve
{ "repo_path": ".", "intent_id": "PROJ-14", "decided_by": "alice", "note": "why" }
// add "reject": true to record a rejection instead
```

**Do not call this on the user's behalf without being asked.** It records a human decision;
`decided_by` defaults to the repo's git identity, and the tool refuses rather than inventing
an approver when it cannot tell who decided.

---

#### `sdlc_feature`

**The main tool** — builds one intent end to end. Safe by default (local branch + diff, no
external writes). **Prefer `sdlc_plan` first**: it costs nothing, and it is the only way the
user sees what would be built before the money is spent. Parameters:

| Param | Meaning |
|---|---|
| `source` | Where the requirement lives: `file://./spec.md`, `confluence://<id>`, `notion://<id>`. **(required)** |
| `intent_id` | Which derived intent to build (default: the first one). |
| `repo` | Git URL or `owner/repo` to branch from. Omit for a throwaway scratch repo (pure demo). |
| `layout` | `new` = **greenfield** (scaffold a fresh structure), `existing` = **brownfield** (follow the repo), `auto` = scaffold only if the repo is empty. |
| `language` | `auto` (detect) or `python` / `java` / `typescript` / `csharp` / `c` / `cpp` / `go` / `php` / `perl` / `sql`. |
| `package_name` | Override the scaffold package name (greenfield). |
| `live` | `false` (default) = local branch + diff, no external writes. `true` = real Jira + push + PR. |
| `confirm` | Must be `true` alongside `live=true` — the explicit authorization for writes. |
| `max_refine` | How many implement→test→refine iterations to allow (default 3). |

**Example A — greenfield (safe):**

> **Ask your assistant:** "Use spine's `sdlc_feature` with `source=file://~/specs/slugify.md`,
> `language=python`, `layout=new`. Keep it safe. Show me the files and test result."

```jsonc
// tool: sdlc_feature
{
  "source": "file://~/specs/slugify.md",
  "language": "python",
  "layout": "new"
  // live defaults to false → nothing is pushed
}
```

**Example B — brownfield (safe):**

> **Ask your assistant:** "Use spine's `sdlc_feature` with `source=file://./rate-limit.md`,
> `repo=my-org/my-service`, `layout=existing`, `language=auto`. Keep it safe; show the diff."

```jsonc
// tool: sdlc_feature
{
  "source": "file://./rate-limit.md",
  "repo": "my-org/my-service",
  "layout": "existing",
  "language": "auto"
}
```

**Example C — brownfield, open a real PR (gated):**

> **Ask your assistant:** "Use spine's `sdlc_feature` with `source=file://./rate-limit.md`,
> `repo=my-org/my-service`, `layout=existing`, **`live=true`, `confirm=true`**. Open the PR."

```jsonc
// tool: sdlc_feature
{
  "source": "file://./rate-limit.md",
  "repo": "my-org/my-service",
  "layout": "existing",
  "live": true,
  "confirm": true                    // required with live=true, or Spine refuses
}
```

**Returns:**
```jsonc
{
  "passed": true,
  "intent_id": "intent-1",
  "issue_key": "DRY-1",              // a dry-run key when live=false; a real Jira key when live=true
  "branch": "feat/<id>/DRY-1",
  "files": ["src/<pkg>/utils.py", "tests/test_utils.py"],
  "iterations": 1,                   // implement→test→refine passes it took
  "grounding_chars": 0,             // size of the PKG context used (0 ⇒ greenfield)
  "live": false,
  "pr_url": null                     // the PR URL when live=true
}
```

---

#### The autonomous run (`sdlc_start_run` + friends)

For a **whole backlog**, not one intent: a long, gated run that pauses for human decisions.
This needs the **Mode‑B backend** (a running Temporal worker + Postgres) — see
[OPERATIONS.md](OPERATIONS.md). You start it, poll status, decide each gate, then fetch the
result.

**1. Start (safe — dry‑run Jira):**

> **Ask your assistant:** "Use spine's `sdlc_start_run` on `file://./roadmap.md`, max 3 features."

```jsonc
// tool: sdlc_start_run
{
  "source": "file://./roadmap.md",
  "create_jira": false,             // true writes real issues → needs confirm: true
  "max_features": 3,
  "max_parallel": 2
}
// → { "sdlc_id": "…", "status": "RUNNING", … }
```

**2. Poll status** (returns the gate awaiting you, if any):

```jsonc
// tool: sdlc_run_status
{ "sdlc_id": "<id from step 1>" }
```

**3. Decide a gate** (the run pauses at `intents`, then `merge`):

```jsonc
// tool: sdlc_decide_gate
{
  "sdlc_id": "<id>",
  "gate": "intents",                // "intents" | "merge" | a raw approval id
  "action": "approve",              // "approve" | "reject" | "modify_input"
  "rationale": "looks good"          // optional
}
```

**4. Fetch the result** once it has COMPLETED:

```jsonc
// tool: sdlc_run_result
{ "sdlc_id": "<id>" }
```

#### Operating runs (`registry_runs` + friends)

"What is running, and what is waiting on me?" — the operator questions the web inbox answers,
for an assistant. These go **over HTTP to the registry** (`orchestrator up`, or
`ORCHESTRATOR_API_URL` pointing at a running one, with `ORCHESTRATOR_API_KEY`), so the plugin
needs no database or Temporal access of its own; the registry scopes what you see to your key's
tenant and records your key as the actor, exactly as it does for the inbox. If the registry is
down, each returns `error` + a `hint` instead of failing.

> **Ask your assistant:** "What's waiting on me?" · "Show me the trace for run `<id>`." · "Approve
> `sdlc-<id>-0`, rationale: reviewed the intents."

```jsonc
// tool: registry_runs        → { count, items: [{ sdlc_id, state, last_action, updated_at, … }], markdown }
{ "limit": 20 }

// tool: registry_approvals   → { count, items: [{ id, title, risk_classification, task_id, … }], markdown }
{ "limit": 50 }

// tool: registry_trace       → newest `tail` audit entries + tool invocations; `truncated` says what was left out
{ "sdlc_id": "<id>", "tail": 50 }

// tool: registry_decide      → destructive: a rejection ends the run
{
  "approval_id": "sdlc-<id>-0",
  "action": "approve",              // "approve" | "reject" | "modify_input" (needs modified_input)
  "rationale": "reviewed the intents"
}
```

`sdlc_decide_gate` decides the same gates **in‑process** (no registry, but Temporal + Postgres
access from the plugin); use it for a run this plugin started when there is no registry, and
`registry_decide` when there is.

---

## 7. Walkthrough — greenfield

Goal: generate a brand‑new, tested utility from a one‑line spec — no existing repo.

**1. Write a spec file** (anywhere on disk), e.g. `~/specs/slugify.md`:

```markdown
# String utilities

## Feature: slugify
Provide a `slugify(text)` helper that lowercases, trims, and replaces runs of
non-alphanumeric characters with single hyphens.

### Acceptance criteria
- slugify('Hello, World!') == 'hello-world'
- slugify('  A__B  ') == 'a-b'
```

**2. Ask your assistant:**

> **"Use spine's `sdlc_feature` with `source=file://~/specs/slugify.md`, `language=python`,
> `layout=new`. Keep it safe (don't open a PR). Then show me the generated files and the
> test result."**

**3. What you get back:** a JSON result with `passed: true`, the `branch`, the generated
`files` (implementation + tests), and `iterations` (how many refine passes it took).
Spine scaffolded a project, wrote `slugify`, wrote tests, and ran them green — all in a
scratch workspace. Nothing was pushed.

**4. Iterate** by editing the acceptance criteria and re‑running, or ask your assistant to read the
generated files and explain them.

> Swap `language=cpp` (and add a spec for, say, a small math utility) to watch Spine
> scaffold a CMake project and drive it to a green `ctest` — same flow, different toolchain.

---

## 8. Walkthrough — brownfield

Goal: deliver a change into an **existing** repo, grounded in its real conventions.

**1. Preview the grounding first** (read‑only — see what Spine will reuse):

> **"Use spine's `pkg_grounding` with `repo_path=/path/to/my/repo` and
> `spec_text='add rate limiting to the public API'`. Summarize what it found."**

You'll see the real types/functions/endpoints Spine would build on, with `file:line`.

**2. Deliver the feature, safely:**

> **"Use spine's `sdlc_feature` with `source=file://./rate-limit.md`,
> `repo=my-org/my-service`, `layout=existing`, `language=auto`. Keep it safe. Show me the
> branch and the diff."**

`layout=existing` tells Spine to **follow the repo's own structure** (its package layout,
build system, test framework) instead of scaffolding. It clones the repo, branches,
generates code that fits, runs the repo's tests, and commits locally — no push.

**3. Review**, then promote to a real PR when you're satisfied — see [§9](#9-safe-vs-live-the-write-gate).

> **Heads‑up on big native repos.** For very large C/C++ projects whose *full* build is
> heavy (system deps, hundreds of targets), Spine generates and grounds correctly, but the
> in‑pipeline build/test may be too heavy to finish in one call. Prefer a self‑contained
> slice, or verify the build of just the touched component.

---

## 9. Safe vs. live (the write gate)

**Three tiers, separated by what a tool costs you if it is wrong** — work down them, never up.

| Tier | Tools | Costs | Writes |
|---|---|---|---|
| **Comprehend** | `map_repo`, `blast_radius`, `investigate`, `localize`, `root_cause`, … | nothing | nothing |
| **Plan and decide** | `sdlc_plan`, `sdlc_approve` | nothing | `.spine/` only |
| **Build** — *gated* | `sdlc_feature`, `sdlc_start_run` + gate tools | **real money, every call** | local, or a PR with `live=true` |

**"Gated" means two separate things.** It spends: every call drives a model through codegen,
tests and review, and a failed run costs what a successful one costs. And with `live=true` it
writes where you cannot take it back — a tracker issue, a pushed branch, an open PR.

**Safe mode still costs tokens.** `live=false` keeps every write local; it does not make the
run free. The tier above it — `sdlc_plan` — is the one that costs nothing at all, which is
why it belongs before anything is built rather than after.

Spine is **safe by default**. `sdlc_feature` with `live` unset only ever creates a *local*
branch, commits, and shows a diff — **no external writes**, Jira runs dry.

To actually open a PR (and create the Jira issue), you must pass **both** `live=true` **and**
`confirm=true`:

> **"Use spine's `sdlc_feature` with `source=file://./rate-limit.md`, `repo=my-org/my-service`,
> `layout=existing`, **`live=true`, `confirm=true`**. Open the PR."**

The `confirm=true` is a deliberate second authorization on top of the host's own tool‑use
approval — Spine refuses a live write without it. `live=true` needs a reachable repo
(`repo` or `SDLC_REPO_URL`) and a GitHub token. The same gate guards `sdlc_start_run`'s
`create_jira=true`.

---

## 10. Language support & toolchains

Comprehension covers **thirteen front-ends** — twelve languages, plus a Gradle reader
that turns `.kts` build scripts into a module dependency graph (it is not a language
and has no toolchain row). Kotlin reads structure, calls, Room entities, Retrofit
calls, Compose routes and Hilt wiring, and is a **codegen target** for both plain
Kotlin/JVM and Android projects.
Spine only needs a language's toolchain when it **builds/tests** generated code in that
language:

| Language | Build/test needs on PATH |
|---|---|
| Python | nothing extra (pytest ships with the engine's `sdlc` extra) |
| Java | a JDK + **Maven**, or **Gradle** (a committed `./gradlew` counts) |
| TypeScript | **Node.js** + a package manager (npm/pnpm/yarn) |
| C# | the **.NET SDK** (`dotnet`) |
| C | **CMake** (or **Meson + Ninja**) + a C compiler |
| C++ | **CMake** (or **Meson + Ninja**) + a C++ compiler |
| Go | the **`go`** toolchain (`go build` / `go test`); multi-module aware |
| PHP | **PHP** (8.3 recommended); **Composer** when `composer.json` exists, otherwise a verified PHPUnit PHAR is downloaded outside the worktree |
| Kotlin | a JDK + **Gradle** — a committed `./gradlew` is enough, since it downloads the version the project pins; no Kotlin install is needed, as `kotlin("jvm")` brings the compiler |
| Kotlin (Android) | the above **plus an Android SDK** (`ANDROID_HOME`). Unit tests only — no emulator and no device, ever |
| Perl | **perl** + **prove**; **cpanm** optional for `cpanfile` dependencies |
| SQL | nothing extra — schema, queries, stored procedures, ordered-migration folding |

Comprehension front-ends beyond Python install as extras — one at a time
([SETUP extras](SETUP.md#optional-extras)) or all at once with `[languages]`, which `[all]`
already includes.

`language=auto` detects from the repo. For C#, Spine additionally lifts ASP.NET Core
endpoints and EF Core entities into the graph; for C/C++ it builds the `#include` graph and
merges header declarations with their definitions; for Go it computes **interface
satisfaction** (`IMPLEMENTS`) by matching method sets.

C++ translation units route the `.h` headers they reach through literal includes
to the C++ parser. With `[clang]` installed alongside the language extras, the
optional semantic pass adds member-call edges only between grounded symbols.
Overloads retain one name-based ID; virtual calls use the static target. Read the
`resolved N of M ... in K of T TUs` extraction summary as a coverage bound.
Missing headers, unsupported declarations and ungrounded targets remain misses;
[real-repository recovery is limited](docs/evals/clang-semantic-validation.md).

Perl codegen uses `perl -c` followed by owning tests and the whole `prove` suite.
Nested suites run recursively; `cpanm` is optional and its absence is logged.
A repository with `.perlcriticrc` additionally requires `Perl::Critic` for preflight.
See [Perl code generation](USER_GUIDE.md#perl-code-generation).

For measured graph precision and recall, see [BENCHMARK.md](BENCHMARK.md) or run
`orchestrator pkg accuracy` for the current numbers.

> Use `--language php` for PHP delivery. Existing PHPUnit layout and bootstrap settings
> are preserved; only changed PHP files are linted and changed tests executed. See the
> [PHP workflow](USER_GUIDE.md#php-code-generation) for Composer and legacy setup.

---

## 11. Troubleshooting

See [SETUP → Troubleshooting](SETUP.md#10-troubleshooting), then run
`orchestrator doctor` from the folder containing `.env`. For host discovery and
configuration paths, use [Install](#3-install) and [Credentials](#4-credentials).

---

## 12. Updating & uninstalling

Use [SETUP](SETUP.md#updating-and-uninstalling) to update or uninstall the engine. Refresh or remove the
plugin using your host's commands in [Install](#3-install); remove a raw server using
[its configuration entry](#4-credentials). Restart the host after either change.

Report issues at <https://github.com/synaptixs/spine>.
