# Orchestrator (Spine) — CLI Reference

> **Spine** is the product; the command is **`orchestrator`** (package `synaptixs-spine`).
> Auto-generated from the CLI — run `orchestrator <command> --help` for the live version.

**41 commands** across 7 areas. Every command supports `--help`; repo-analysis commands accept a local path or a git URL.

## Command map

**Getting started & operations** — Set up your environment and run the platform.  
`init` · `doctor` · `up` · `tui` · `task submit`

**Understand a codebase — the Knowledge Graph** — Extract and read the Product Knowledge Graph (PKG). Deterministic, no LLM. All accept a local path OR a git URL.  
`understand` · `state` · `profile` · `catalog list` · `catalog plan` · `pkg extract` · `pkg export` · `pkg docs`

**Grounded design, debugging & RCA** — The KG-grounded engineering commands: design a change, research a ticket, and trace/analyze bugs — all anchored to real code.  
`design` · `investigate` · `localize` · `rca` · `regression` · `audit`

**Requirements intake — source to backlog** — Turn a requirements source (Confluence, Jira, Notion, files, OpenSpec, MCP) into intents/specs and (optionally) tracker issues.  
`ingest` · `backlog` · `openspec draft`

**The SDLC pipeline — build features** — The autonomous build path: requirements → code → tests → reviewed PR, with human gates.  
`sdlc feature` · `sdlc run` · `sdlc complete` · `sdlc address-review` · `sdlc remediate`

**MCP — external tools** — Consume onboarded Model Context Protocol servers (governed, audited).  
`mcp list` · `mcp contracts` · `mcp call` · `mcp ingest-db`

**Registry — templates & contracts** — Manage reusable capability templates and API contracts in the registry service.  
`template register` · `template list` · `template show` · `template publish` · `template deprecate` · `contract register` · `contract list` · `contract show` · `contract publish` · `contract deprecate`


---

## Getting started & operations

Set up your environment and run the platform.

### `orchestrator init`

Scaffold a new project: create a .env from the template, then guide setup.

Creates a commented .env skeleton (from the same env groups `doctor`
checks), then reports readiness. While required variables are still unset it
exits non-zero with a call to fill them in and re-run — so `init` is the
one-command setup loop: run it, fill the blanks, run it again until green.

Safe to re-run: an existing .env is never overwritten (only missing keys are
appended) unless --force.

```
orchestrator init [OPTIONS]
```

| Option | Description |
|---|---|
| `--path` | Directory to scaffold the .env into. (default: `.`) |
| `--force` | Overwrite an existing .env with a fresh template. |

### `orchestrator doctor`

Check environment readiness and print a diagnostic report.

Bridges `.env` into the process environment first (same as `ingest` /
`sdlc`), so the report reflects exactly what the pipeline will see — a
real exported variable still wins over the file.

```
orchestrator doctor
```

### `orchestrator up`

Bring up the whole local stack in one command, then open the inbox.

Starts Docker infra (Postgres + Temporal), applies migrations, and launches
the web/API server **and** the Temporal worker with sensible defaults — so a
non-technical user reaches the delegation inbox at `/app` without wiring up
three terminals. Streams logs until Ctrl-C, then stops the app processes
(infra containers are left running for fast restarts).

```
orchestrator up [OPTIONS]
```

| Option | Description |
|---|---|
| `--port` | Port for the web UI + API. (default: `8000`) |
| `--host` | Bind address for the API. (default: `127.0.0.1`) |
| `--no-docker` | Don't manage Docker; assume Postgres + Temporal are already up. |
| `--no-worker` | Skip the Temporal worker (browse-only; can't delegate runs). |
| `--compose-file` | Override the docker compose file to use. |

### `orchestrator tui`

Launch the terminal UI: watch runs, clear gates, and delegate a run.

A keyboard-driven cousin of the web inbox over the same `/v1` API. Needs the
`tui` extra: `pip install 'synaptixs-spine[tui]'`.

```
orchestrator tui [OPTIONS]
```

| Option | Description |
|---|---|
| `--api-url` | Registry API base URL. (default: `http://localhost:8000`) |
| `--api-key` | API key for the registry. (default: `dev-key`) |

### `orchestrator task submit`

Submit a task to the orchestrator and print the final state.

```
orchestrator task submit [OBJECTIVE] [OPTIONS]
```

**Arguments**

- `OBJECTIVE` — 

| Option | Description |
|---|---|
| `--template` | Pin a specific template id; planner chooses otherwise. |
| `--version` | Pin a specific template version. |

---

## Understand a codebase — the Knowledge Graph

Extract and read the Product Knowledge Graph (PKG). Deterministic, no LLM. All accept a local path OR a git URL.

### `orchestrator understand`

Build a committed `episteme/` — a code-true project knowledge base.

Phase 0: extracts the Product Knowledge Graph + project profile and renders
architecture / domain-model / tech-context / conventions / glossary as
markdown in the target repo. Deterministic (no LLM); re-run to refresh.
`path` may be a local path or a git URL cloned on demand — for a URL the
clone is transient, so the knowledge base defaults to `./episteme`.

A doc-ingestion post-pass also folds the repo's docs into the graph as `Doc` nodes +
`MENTIONS` edges: Markdown, reST, plain text and **HTML** need nothing; **PDF** needs the
`[docs]` extra and **Word/Excel** the `[office]` extra. No-op on a repo with no docs.

```
orchestrator understand [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to comprehend. _(default: `.`)_

| Option | Description |
|---|---|
| `--out` | Knowledge-base dir (default: <repo>/episteme; ./episteme for a URL). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect (postgres\|mysql\|tsql\|oracle\|…); default: auto-detect. |

### `orchestrator state`

Current State — a team-facing snapshot of what a repo is today and how healthy it looks.

Synthesized from the Product Knowledge Graph + project profile (deterministic, no LLM),
layered on top of `understand`. `--lens developer` gives the technical view;
`--lens stakeholder` gives plain language. Includes a **Documentation** section — how much
of the code the ingested docs describe (symbol coverage %) plus top **doc drift**. A report
is a *view* of the code — re-run to refresh; nothing is written unless `--out` is given.

```
orchestrator state [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to summarize. _(default: `.`)_

| Option | Description |
|---|---|
| `--lens` | Audience: developer \| stakeholder. (default: `developer`) |
| `--out` | Write the report to this file (default: print to stdout). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect (postgres\|mysql\|tsql\|oracle\|…); default: auto-detect. |

### `orchestrator profile`

Profile a project (languages, framework, DB, tests, task type) — read-only.

`path` is a local path or a git URL (github/bitbucket/gitlab/enterprise),
cloned on demand.

```
orchestrator profile [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to profile. _(default: `.`)_

| Option | Description |
|---|---|
| `--intent` | Intent title, to classify the task type. |
| `--json` | Emit the profile as JSON. |

### `orchestrator catalog list`

List the capabilities the orchestrator can assemble (read-only).

```
orchestrator catalog list [OPTIONS]
```

| Option | Description |
|---|---|
| `--json` | Emit the catalog as JSON. |

### `orchestrator catalog plan`

Show the capability plan the orchestrator would assemble for a project.

```
orchestrator catalog plan [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to plan for. _(default: `.`)_

| Option | Description |
|---|---|
| `--intent` | Intent title, to classify the task type. |
| `--json` | Emit the plan as JSON. |

### `orchestrator pkg extract`

Extract grounded code facts from a repo and print a summary (read-only).

```
orchestrator pkg extract [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to scan. _(default: `.`)_

| Option | Description |
|---|---|
| `--query`, `-q` | Show callers + blast radius of a symbol name. |
| `--json` | Dump all facts as JSON. |
| `--dialect` | SQL dialect (postgres\|mysql\|tsql\|oracle\|…); default: auto-detect. |

### `orchestrator pkg export`

Extract facts and export the ontomesh-ready kind-per-table SQLite projection.

```
orchestrator pkg export [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to scan. _(default: `.`)_

| Option | Description |
|---|---|
| `--db` | SQLite file to write. (default: `pkg-facts.db`) |

### `orchestrator pkg docs`

Reconcile the **named** documentation file(s) against the code's fact graph (read-only) and
print a binding/drift summary — the targeted counterpart to the automatic whole-repo doc
ingestion that `understand`/`state` perform (which folds *all* docs into the graph as `Doc`
nodes + `MENTIONS` edges).

```
orchestrator pkg docs [REPO] [OPTIONS]
```

**Arguments**

- `REPO` — Repo path or git URL to extract facts from. _(default: `.`)_

| Option | Description |
|---|---|
| `--doc`, `-d` | Markdown/text doc(s) to reconcile. (default: `[]`) |

---

## Grounded design, debugging & RCA

The KG-grounded engineering commands: design a change, research a ticket, and trace/analyze bugs — all anchored to real code.

### `orchestrator design`

Grounded feature design: spec × knowledge graph → a design with blast radius.

Produces the M2 design for one feature anchored to the repo's real structure,
and annotates it with its **blast radius** (which modules it touches, who
depends on them, the call hotspots) and any **unverified references** (named
paths absent from the graph). Deterministic by default; `--llm` writes the
prose. `path` may be a local path or a git URL cloned on demand.

```
orchestrator design [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to design against. _(default: `.`)_

| Option | Description |
|---|---|
| `--title`, `-t` | Feature title (the thing to build). |
| `--summary`, `-s` | One-line feature summary. |
| `--criterion`, `-c` | Acceptance criterion (repeatable). |
| `--spec` | Read the spec from JSON ({title,summary,acceptance_criteria}) or a .md file. |
| `--out` | Write design.md here (default: print to stdout). |
| `--llm` | Let an LLM write the design (needs a provider; else heuristic). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator investigate`

Investigation brief: a ticket × the codebase, before you design.

Researches where a ticket lands in the code (knowledge-graph retrieval, with
`file:line` + caller counts), the relevant committed `episteme/` knowledge,
and — when a registry DB is configured — prior-run notes. Deterministic, no
LLM. Pass the ticket via `--source` (e.g. `jira://PROJ-123`) or inline with
`--title`/`--text`. Feed the result into `orchestrator design`.

```
orchestrator investigate [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to research against. _(default: `.`)_

| Option | Description |
|---|---|
| `--source` | Fetch the ticket from a source, e.g. jira://PROJ-123, confluence://<id>, file://./bug.md. |
| `--title`, `-t` | Inline ticket title (instead of --source). |
| `--text` | Inline ticket body (with --title). |
| `--out` | Write the brief here (default: print to stdout). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator localize`

Fault localization: a stack trace → the repo symbols it names.

Parses a Python traceback / pytest failure, resolves each frame to a
knowledge-graph symbol (`file:line`), and points at the likely fault site
plus who calls it. Reads the trace from `--trace <file>`, `--text`, or stdin.
Deterministic, no LLM — the first step of a root-cause investigation.

```
orchestrator localize [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to resolve the trace against. _(default: `.`)_

| Option | Description |
|---|---|
| `--trace` | File with the stack trace / failing-test output. |
| `--text` | Inline trace text (instead of --trace). |
| `--out` | Write the report here (default: print to stdout). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator rca`

Root-cause analysis: a bug → grounded RCA + fix approach (no code changed).

Localizes the bug (a stack trace, a `jira://` Bug, or inline text) against
the knowledge graph, then reports the fault site, ranked root-cause
*hypotheses* with evidence (callers, recent churn, the exception), the
regression surface a fix must cover, and a scoped fix approach. Deterministic
by default; `--llm` enriches the hypotheses. It stops at the report — a human
decides whether to build the fix.

```
orchestrator rca [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to analyze against. _(default: `.`)_

| Option | Description |
|---|---|
| `--source` | Fetch the bug from a source, e.g. jira://PROJ-42 (a Bug ticket). |
| `--trace` | File with a stack trace / failing-test output. |
| `--text` | Inline bug text / trace (instead of --trace/--source). |
| `--out` | Write rca.md here (default: print to stdout). |
| `--llm` | Let an LLM enrich the hypotheses (needs a provider; else deterministic). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator regression`

Regression coverage: what a change should re-test, from the call graph.

For a symbol you're about to change (`--symbol`) or a fault site (`--trace`),
computes the blast radius and splits it into tests that already exercise it
and production code in the radius with no covering test — the regression
gaps. Deterministic, no LLM. Needs a call graph (Python/C/C++/C#/Java/TS).

```
orchestrator regression [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to analyze. _(default: `.`)_

| Option | Description |
|---|---|
| `--symbol`, `-s` | The symbol you're about to change (by name). |
| `--trace` | A stack trace instead — the fault site becomes the target. |
| `--out` | Write the plan here (default: print to stdout). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator audit`

Codebase-auditor persona: a read-only agentic audit → findings report.

The auditor navigates the repo via the PKG + file reads (no writes) and
reports findings anchored to real file:line. Needs an LLM provider (same
creds the pipeline uses); the model follows `resolve_codegen_model`.

```
orchestrator audit [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo or directory to audit. _(default: `.`)_

| Option | Description |
|---|---|
| `--focus` | What to look for. (default: `general code quality, correctness risks, and security`) |
| `--out` | Write the findings report to this file. |
| `--bundle` | Write the full run bundle (trace + policy blocks) as JSON. |

---

## Requirements intake — source to backlog

Turn a requirements source (Confluence, Jira, Notion, files, OpenSpec, MCP) into intents/specs and (optionally) tracker issues.

### `orchestrator ingest`

Source (Confluence / Notion / local files) → intents → gaps → specs → Jira backlog.

Dry-run by default: fetches the source tree, derives intents, flags
gaps, drafts specs, and prints the would-be Jira issues without writing
anything. Pass --create to write to Jira (refused when gaps gate
approval unless --force).

The lowest-friction source is local files — no SaaS account needed:

    orchestrator ingest --source file://./examples/intake/sample-spec.md

(An LLM key is still required for the intent/spec stages.)

```
orchestrator ingest [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md. |
| `--create` | Create issues for real (default: dry-run preview). |
| `--rules` | Path to a gap-rules YAML (defaults to built-ins). |
| `--force` | Create even when gaps gate the intent-approval bookend. |
| `--refresh` | Re-extract from the source (default: reuse the cached backlog). |

### `orchestrator backlog`

Render the cached backlog + completion progress as markdown (read-only).

Reads the persisted backlog (from a prior ingest / sdlc feature run) and
prints a checkbox ledger: [ ] todo, [~] in progress, [x] done. Pass --out to
write a BACKLOG.md.

```
orchestrator backlog [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Source URI whose cached backlog to render, e.g. confluence://<id>. |
| `--out` | Write the markdown here (default: print to stdout). |

### `orchestrator openspec draft`

Bootstrap OpenSpec change proposals FROM an unstructured source (the write-back).

Runs the LLM intake once (source → intents → specs), then renders each as a
structured `openspec/changes/<id>/` proposal (proposal.md + specs delta + tasks).
A human polishes the draft, then implements deterministically:

    orchestrator openspec draft --source confluence://<id> --out ./openspec
    # …review/edit openspec/changes/<id>/…
    orchestrator sdlc feature --source openspec://<id> --safe

```
orchestrator openspec draft [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Unstructured source to bootstrap FROM, e.g. confluence://<id>. |
| `--out` | OpenSpec root to write into (changes/<id>/ is created under it). (default: `openspec`) |
| `--refresh` | Re-extract from the source (default: reuse the cached backlog). |
| `--overwrite` | Overwrite existing change files (default: never clobber). |

---

## The SDLC pipeline — build features

The autonomous build path: requirements → code → tests → reviewed PR, with human gates.

### `orchestrator sdlc feature`

Linear pipeline for ONE intent, end to end.

source → intent → spec → Jira issue → worktree branch → code generation
→ test + refine → commit → (push + PR) → Jira update → ready for deployment.

Default --safe makes no external write: it creates a local branch, commits
the generated + tested code, and prints the diff. Pass --live to create the
Jira issue, push the branch, open a real PR, and comment the PR link back on
the issue.

```
orchestrator sdlc feature [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md. |
| `--intent` | Intent id to implement (default: first derived intent). |
| `--repo` | Git URL to branch from (default $SDLC_REPO_URL; scratch if unset). |
| `--model` | Codegen model (default: $SDLC_CODEGEN_MODEL or the adapter default). |
| `--max-refine` | Max implement→test→refine iterations. (default: `3`) |
| `--live` | Write for real: create the Jira issue, push the branch + open a PR, comment on Jira. Default --safe stays local (branch + commit + diff, dry-run Jira, no push). |
| `--layout` | Target structure: auto (scaffold only empty repos), new (always scaffold a src/<pkg>/ skeleton), or existing (follow the repo's layout). (default: `auto`) |
| `--package-name` | Override the scaffold package name (default: derived from repo). |
| `--refresh` | Re-extract intents from the source (default: reuse the cached, deterministic backlog). |
| `--language` | Target language: auto (detect), python, java, typescript, csharp, c, cpp, go, or sql. (default: `auto`) |

### `orchestrator sdlc run`

Start the Block-C SDLC workflow on the sdlc-tasks queue.

Generates a fresh sdlc_id and starts `SDLCWorkflow` with workflow id
`task-{sdlc_id}` — the id convention the REST `/v1/approvals/*` API
relies on to route gate decisions back to the workflow. The two human
gates persist real, decidable ApprovalRequest rows
(`sdlc-{sdlc_id}-0` for intents, `sdlc-{sdlc_id}-1` for merge).

A worker must be running on the sdlc-tasks queue
(`python -m orchestrator.sdlc.worker`).

```
orchestrator sdlc run [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md. |
| `--actor` | Who is launching the run (recorded in audit rows). (default: `cli`) |
| `--create-jira` | Write Jira issues for real (default: dry-run synthetic keys). |
| `--wait` | Block until the workflow finishes and print its result (default: return after start). |
| `--max-features` | Cap features per run (0 = unlimited). |
| `--max-parallel` | Feature children per batch (1 = sequential). (default: `2`) |

### `orchestrator sdlc complete`

Close the Jira issue for a merged PR (the merge → Done bookend).

The linear `sdlc feature` path stops at an open PR for a human to review
and merge; this reconciles Jira afterwards. Verifies the PR is merged (via
`gh`), derives the issue key from the PR's head branch
(`feat/<sdlc_id>/<KEY>`) unless `--issue` is given, then transitions the
issue and comments the merge. Needs an authenticated `gh`.

```
orchestrator sdlc complete [OPTIONS]
```

| Option | Description |
|---|---|
| `--pr` | The merged PR URL whose linked issue to close. |
| `--issue` | Issue key (default: derived from the PR branch feat/<id>/<KEY>). |
| `--status` | Target Jira status to move the issue to. (default: `Done`) |
| `--allow-unmerged` | Transition even if the PR is not merged yet. |

### `orchestrator sdlc address-review`

Read a PR's human review comments, revise the change, and push the fix.

Checks out the PR branch into a throwaway clone, feeds the reviewers'
comments to codegen, re-drives to green (tests + preflight), and pushes a
follow-up commit to the PR branch. Out-of-band and human-triggered — the
autonomous run's merge gate stays the bookend. Needs SDLC_CODEGEN=llm and
an authenticated `gh`.

```
orchestrator sdlc address-review [OPTIONS]
```

| Option | Description |
|---|---|
| `--pr` | The PR URL to address review comments on. |
| `--repo` | Repo clone URL (defaults to SDLC_REPO_URL). |
| `--bot-login` | Skip this author's own comments (the agent's account). |
| `--max-refines` | Refine cycles to reach green. (default: `3`) |

### `orchestrator sdlc remediate`

Spine Seam 3: a drift report → governed remediation runs (one per affected entity).

Plans scoped, guardrailed remediation tasks from the infodrift report (Phase 2) and
runs each through the codegen pipeline with the task as the spec (intake skipped),
grounded by ontomesh (Seam 1) when configured. Default --safe is human-gated: it
leaves a branch + diff to review; --live opens PRs.

```
orchestrator sdlc remediate [OPTIONS]
```

| Option | Description |
|---|---|
| `--report` | Path to an infodrift full_report JSON. |
| `--mappings` | Path to the confirmed code↔ontology MappingStore JSON. (default: `spine-mappings.json`) |
| `--repo` | Git URL to branch from (default $SDLC_REPO_URL). |
| `--min-severity` | Only remediate findings at/above: warning \| critical. (default: `warning`) |
| `--live` | --safe (default) leaves a reviewable branch+diff per entity (human-gated); --live opens PRs. |

---

## MCP — external tools

Consume onboarded Model Context Protocol servers (governed, audited).

### `orchestrator mcp list`

Discover the allow-listed tools across all configured MCP servers.

```
orchestrator mcp list [OPTIONS]
```

| Option | Description |
|---|---|
| `--config` | Path to an mcpServers JSON file (default: $ORCHESTRATOR_MCP_CONFIG or ./mcp.json). |

### `orchestrator mcp contracts`

Show the ToolContract derived for each onboarded MCP tool (governance view).

```
orchestrator mcp contracts [OPTIONS]
```

| Option | Description |
|---|---|
| `--config` | mcpServers JSON file path. |

### `orchestrator mcp call`

Invoke one onboarded MCP tool (server:tool) with JSON arguments.

```
orchestrator mcp call [TOOL] [OPTIONS]
```

**Arguments**

- `TOOL` — Qualified tool name: server:tool.

| Option | Description |
|---|---|
| `--args` | JSON object of tool arguments. (default: `{}`) |
| `--config` | mcpServers JSON file path. |

### `orchestrator mcp ingest-db`

Introspect a DB MCP server's schema into PKG data-layer facts (Entity/Field).

```
orchestrator mcp ingest-db [OPTIONS]
```

| Option | Description |
|---|---|
| `--server` | Name of an onboarded DB MCP server. |
| `--query-tool` | The server's SQL query tool name. (default: `query`) |
| `--sql-arg` | The query tool's SQL argument name. (default: `sql`) |
| `--schema` | DB schema to introspect. (default: `public`) |
| `--config` | mcpServers JSON file path. |

---

## Registry — templates & contracts

Manage reusable capability templates and API contracts in the registry service.

### `orchestrator template register`

Register a new agent template from a JSON or YAML file.

```
orchestrator template register [FILE]
```

**Arguments**

- `FILE` — 

### `orchestrator template list`

List agent templates.

```
orchestrator template list [OPTIONS]
```

| Option | Description |
|---|---|
| `--tag` | Filter by tag. |
| `--status` | Filter by lifecycle state. |

### `orchestrator template show`

Show the latest published version (or a specific version).

```
orchestrator template show [ID] [VERSION]
```

**Arguments**

- `ID` — 
- `VERSION` — 

### `orchestrator template publish`

Promote a draft to published.

```
orchestrator template publish [ID] [VERSION]
```

**Arguments**

- `ID` — 
- `VERSION` — 

### `orchestrator template deprecate`

Mark a published version as deprecated.

```
orchestrator template deprecate [ID] [VERSION]
```

**Arguments**

- `ID` — 
- `VERSION` — 

### `orchestrator contract register`

Register a new tool contract from a JSON or YAML file.

```
orchestrator contract register [FILE]
```

**Arguments**

- `FILE` — 

### `orchestrator contract list`

List tool contracts.

```
orchestrator contract list [OPTIONS]
```

| Option | Description |
|---|---|
| `--tag` | Filter by tag. |
| `--status` | Filter by lifecycle state. |

### `orchestrator contract show`

Show the latest published version (or a specific version).

```
orchestrator contract show [ID] [VERSION]
```

**Arguments**

- `ID` — 
- `VERSION` — 

### `orchestrator contract publish`

Promote a draft to published.

```
orchestrator contract publish [ID] [VERSION]
```

**Arguments**

- `ID` — 
- `VERSION` — 

### `orchestrator contract deprecate`

Mark a published version as deprecated.

```
orchestrator contract deprecate [ID] [VERSION]
```

**Arguments**

- `ID` — 
- `VERSION` — 

---
