# Using Spine from Claude Code

**Spine** (the `synaptixs-spine` / *agent-orchestrator* engine) is an AI‑native SDLC
engineer you delegate tickets to. From inside **Claude Code** you can ask it to read a
requirement, ground new code in your repo's real structure, generate and test that code,
and — when you say so — open a pull request. It works for **greenfield** (fresh) and
**brownfield** (existing) repos across **Python, Java, TypeScript, C#, C, C++, and Go**.

This guide takes you from zero to a delivered feature, entirely through Claude Code.

> **New to Spine itself?** [USER_GUIDE.md](USER_GUIDE.md) covers the CLI and concepts;
> this guide is specifically about driving Spine from Claude Code. Driving it from the
> **Codex app** instead? See [CODEX_GUIDE.md](CODEX_GUIDE.md) — same tools, same flow.

---

## Contents

1. [How it fits together](#1-how-it-fits-together)
2. [Prerequisites](#2-prerequisites)
3. [Install (two ways)](#3-install-two-ways)
4. [Credentials](#4-credentials)
5. [Verify the connection](#5-verify-the-connection)
6. [The tools Spine exposes](#6-the-tools-spine-exposes)
7. [Walkthrough — greenfield](#7-walkthrough--greenfield)
8. [Walkthrough — brownfield](#8-walkthrough--brownfield)
9. [Safe vs. live (the write gate)](#9-safe-vs-live-the-write-gate)
10. [Language support & toolchains](#10-language-support--toolchains)
11. [Troubleshooting](#11-troubleshooting)
12. [Updating & uninstalling](#12-updating--uninstalling)

---

## 1. How it fits together

Claude Code talks to Spine over **MCP** (Model Context Protocol). Spine runs as a small
local server (`orchestrator-mcp`) that Claude Code launches as a subprocess; Claude then
calls Spine's capabilities as **tools**. Each tool runs the real engine — PKG grounding,
codegen, test/refine — and Spine clones/branches your target repo into a scratch
workspace, so your working tree is never touched until you choose to push.

There are **two layers** to the integration, and you only need one:

| | What it is | When to use |
|---|---|---|
| **Plugin** | A packaged, branded entry installed from the Spine marketplace (it *bundles* the MCP server). | The friendly path — install once, manage from `/plugin`. |
| **MCP server** | A raw entry in a project's `.mcp.json` (or `claude mcp add`). | Power users / scripted setups / full control over env + paths. |

Both expose the exact same tools.

---

## 2. Prerequisites

- **Claude Code** installed — the CLI (`claude`) or an IDE extension. It runs on macOS,
  Linux, and Windows; everything below is issued from a Claude Code session and works the
  same across platforms.
- **Python 3.12+**.
- **An LLM provider key** — Anthropic, OpenAI, or a local Ollama endpoint.
- **Per‑language build tools** only if you want Spine to *build/test* generated code in
  that language — see [§10](#10-language-support--toolchains). For Python, nothing extra.
- *(Optional, for `live` PRs)* a **GitHub** token and, if you create tickets, **Jira**.

---

## 3. Install (two ways)

### 3a. As a Claude Code plugin (recommended)

From inside a Claude Code session:

```
/plugin marketplace add synaptixs/spine    # add the Spine marketplace
/plugin install spine@spine                 # install the plugin
```

Then make the `orchestrator-mcp` server available on PATH (the plugin declares it, pip
provides it):

```bash
pip install 'synaptixs-spine[all]'          # provides the `orchestrator-mcp` command
```

> **`[all]`, not `[mcp]`.** `[mcp]` installs the server alone, so the graph is **Python-only** —
> and silently so: a Java or Go repo yields zero nodes rather than an error, which reads as
> "nothing here" instead of "nothing parsed". `[all]` adds every language front-end, doc
> ingestion and the MCP server; `[languages]` is the front-ends on their own.


Restart Claude Code (or run `/reload-plugins`). Confirm with `/plugin` (Spine shows as
installed + enabled) and `/mcp` (the `spine` server shows as connected).

> Prefer a local checkout? `/plugin marketplace add ./` from a clone of this repo instead
> of `synaptixs/spine`.

### 3b. As a raw MCP server

Add a project‑scoped `.mcp.json` at your repo root:

```json
{
  "mcpServers": {
    "spine": {
      "command": "orchestrator-mcp",
      "args": [],
      "env": {
        "ORCHESTRATOR_DOTENV": "/abs/path/to/your/.env"
      }
    }
  }
}
```

…or add it from the CLI:

```bash
claude mcp add spine --env ORCHESTRATOR_DOTENV=/abs/path/to/your/.env -- orchestrator-mcp
```

Restart Claude Code (or `/reload-plugins`). Verify with `/mcp` (you should see `spine`).

---

## 4. Credentials

Spine reads provider/source/tracker creds from a **`.env`** file (same format the CLI
uses — copy [`.env.example`](.env.example) and fill in what you need). The *minimum* for
generating + testing code is **one LLM key**:

```bash
# .env  (the bare minimum)
OPENAI_API_KEY=sk-...                  # or ANTHROPIC_API_KEY=sk-ant-... (or an Ollama endpoint)
ORCHESTRATOR_MODEL=claude-opus-5       # one model for every stage (this is the default)
```

Use any LiteLLM‑supported model string here (run `orchestrator models` to list
them with prices and tool-calling support; e.g. an Anthropic
`claude-*` id, or `ollama/<model>` with `OLLAMA_API_BASE`) — match it to the key you set.

Add more only for what you do:

| You want to… | Add to `.env` |
|---|---|
| Read a spec from a file | *(nothing — `file://` needs no creds)* |
| Read from Confluence / Jira / Notion | `CONFLUENCE_*` / `JIRA_*` / `NOTION_API_TOKEN` |
| Open a **live** PR | `GITHUB_TOKEN` (or `GH_TOKEN`), and `SDLC_REPO_URL` for the default repo |
| Create a **live** Jira issue | `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` / `JIRA_PROJECT_KEY` |

**How Spine finds your `.env`:** the bundled plugin server runs from your session's
working directory, so the simplest path is to **launch Claude Code from a project that has
a `.env`**. To point it anywhere else, use the raw‑MCP form (§3b) and set
`ORCHESTRATOR_DOTENV` to the file's **absolute** path in the server's `env`. Read‑only
tools (`doctor`, `pkg_grounding`) work without any creds.

> Tip: set a *fast, capable* model. A slow default can time out on large generations.
> `ORCHESTRATOR_INTAKE_MODEL` sets the default; `SDLC_CODEGEN_MODEL` overrides codegen only.

---

## 5. Verify the connection

In a Claude Code chat, just ask:

> **"Use spine's `doctor` tool and show me what's ready."**

You'll get a readiness report (LLM provider, source, tracker, GitHub). Then confirm the
server and tools are visible:

```
/mcp        # spine → connected, with its tools listed
/plugin     # spine@spine → installed, enabled   (if you used the plugin)
```

If `doctor` reports the LLM provider missing, your `.env`/`ORCHESTRATOR_DOTENV` isn't being
found — see [§11](#11-troubleshooting).

---

## 6. The tools Spine exposes

At a glance:

| Tool | What it does | Writes? |
|---|---|---|
| [`doctor`](#doctor) | Environment readiness (LLM, source, tracker, GitHub). | no |
| **Understand a codebase — the comprehension tools** | | |
| `map_repo` | A skim‑first map of a repo: languages, components, **call‑hotspots**, **test‑coverage gaps**, prioritized **recommendations**. Structured + markdown. | no |
| `blast_radius` | **"What breaks if I change X"** — a symbol's direct callers plus the cross‑layer set a change ripples into, each with `file:line`. | no |
| `explain_symbol` | What a symbol is and how it connects — kind, location, who calls it, what it calls, what it contains. | no |
| `investigate` | Where a ticket lands in the code: the real symbols to start from (`file:line` + caller counts) + owning areas. | no |
| `localize` | Resolve a stack trace / traceback to the repo symbols it names → the likely fault site + its callers. | no |
| `regression_gaps` | The production symbols a change (by symbol or trace) reaches that **no test covers** — what could break silently. | no |
| `root_cause` | A grounded root‑cause report for a bug: fault site, ranked hypotheses + evidence, regression surface, fix approach. Deterministic by default; `use_llm=true` enriches (needs a model). | no |
| `docs_for` | **Which docs describe the code.** With a `symbol`, the doc pages that mention it; without one, a doc‑coverage summary (% of symbols documented + top drift). Ingests `.md`/`.rst`/`.txt`/**HTML**, plus **PDF** (`[docs]`) and **Word/Excel** (`[office]`). | no |
| `pkg_joins` | **The cross‑repo topology, proposed or checked.** `mode="propose"` derives a `joins:` block from the evidence (each candidate carrying the number of edges it would create); `mode="check"` reports the calls no declared join could place. Read‑only — it never writes a config. | no |
| [`read_memory_bank`](#read_memory_bank) | Read a repo's committed `episteme/` (code‑true project knowledge). | no |
| [`pkg_grounding`](#pkg_grounding) | The existing‑code context a repo's Product Knowledge Graph surfaces for a spec — real APIs/types Spine would reuse, with `file:line`. | no |
| [`ingest_preview`](#ingest_preview) | Preview the backlog (derived intents + gaps) for a requirements source — dry‑run. | no |
| `understand_repo` | **Build the `episteme/` knowledge base** a repo has none of yet (then `read_memory_bank` it), or `check=true` to verify the committed one still matches the code — missing / stale / orphaned pages named. Deterministic, no model. Refuses a build on a git URL unless `out` is an absolute directory. | `episteme/` |
| `profile_repo` | Languages, framework, database + migrations, test runner, and the task type for an `intent` — the profile the catalog picks skills from. | no |
| `design_change` | A grounded design for one spec (the same `spec` object as `sdlc_plan`): approach, **blast radius**, **unverified references**. Deterministic; `use_llm=true` writes the prose. Never writes. | no |
| `sdlc_baseline` | Score the run agent against a corpus of tickets with known answers, plus the durable run records — false and missed refusals counted separately. Free. | no |
| **Plan it, then decide — before anything is built** | | |
| [`sdlc_plan`](#sdlc_plan) | **The build document.** Twelve grounded sections for one ticket: requirement, intent, root cause, what the graph knows, blast radius, design, files, acceptance criteria, the codegen prompt, cost and confidence — each labelled with where it came from. **No model, no credentials, nothing spent.** | `.spine/` |
| [`sdlc_approve`](#sdlc_approve) | Record that a **human** read that document and decided. Binds to a digest of it, so a plan that changes afterwards reads as *stale* rather than still approved. | `.spine/` |
| [`sdlc_feature`](#sdlc_feature) | **Ship it.** One intent end‑to‑end: spec → grounded codegen → tests → branch → *(optionally)* PR. | gated |
| [`sdlc_start_run` + gate tools](#the-autonomous-run-sdlc_start_run--friends) | Drive the long, autonomous, gated run as a job (needs the Mode‑B backend). | gated |
| **Operate — what is running, what is waiting on me** | | |
| [`registry_runs`](#operating-runs-registry_runs--friends) | Recent runs at the registry: id, state, last action, timestamps. | no |
| [`registry_approvals`](#operating-runs-registry_runs--friends) | The gates waiting on a human, latest first, with risk and the run they belong to. | no |
| [`registry_trace`](#operating-runs-registry_runs--friends) | A run's audit trail and tool invocations, newest `tail` entries, with what was left out. | no |
| [`registry_decide`](#operating-runs-registry_runs--friends) | Approve / reject / modify a pending approval so its run continues (or stops). | gated |
| **Close the loop — after the PR** | | |
| `sdlc_address_review` | Address the human review comments on an open PR and **push a fix to its branch** (clone, `gh pr checkout`, codegen with the comments as feedback, tests + preflight, push). No local mode — **`confirm=true` on every call**. Needs `git`, `gh`, a model, the run backend. | gated |
| `sdlc_complete` | Close the tracker issue for a **merged** PR: verify the merge via `gh`, derive the key from `feat/<id>/<KEY>` (or pass `issue`), transition, comment, mark the backlog intent done. Real Jira, never dry-run — **`confirm=true`**. | gated |
| `sdlc_remediate` | Turn an infodrift drift report (+ the confirmed mapping store, both files on this machine) into remediation runs at or above `min_severity`. Safe by default (branch + diff); `live=true` opens PRs and needs `confirm=true`, like `sdlc_feature`. | gated |
| `audit_repo` | A codebase-auditor persona reads the repo on a model and reports findings anchored to real `file:line` (claims that don't resolve are listed as `unresolved`). Writes nothing; spends tokens; needs `ORCHESTRATOR_INTAKE_MODEL`. | no |

> **The comprehension tools are read‑only, need no credentials, and are deterministic** (only
> `root_cause`'s and `design_change`'s opt‑in `use_llm` use a model). There is no `state` tool
> because `map_repo` *is* `orchestrator state` — same engine, same rendering. They ship with an **`understand-codebase` skill**
> that tells Claude *when* to reach for each — so you can just ask in plain language and Claude picks
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
> `sdlc` scope still reads as all three for one release. Over stdio there is no token and no check.

### Prompts and resources — the workflow and the documents, through the protocol

Two things the plugin exposes besides tools, for any MCP host:

**Prompts** carry the *which tool, in which order* workflow the `understand-codebase` skill
describes, so a host that has no skills (Codex, Claude Desktop, claude.ai) gets the same ordered
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

Declare your services in a **`.spine/repos.yaml`** and pass it as `repos=` to `blast_radius`
or `investigate` instead of `repo_path`:

```yaml
repos:
  billing: ../billing
  web: ../web
joins:
  - kind: http
    consumer: web
    provider: billing
```

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

Spine's Claude Code plugin bundles an **Agent Skill** called `understand-codebase`. A skill is a
short instruction sheet Claude reads automatically — it tells Claude *which comprehension tool to
reach for* on which kind of question, so **you don't have to name tools**: just ask in plain
language and Claude picks `map_repo` / `blast_radius` / `localize` / … for you.

- **Install:** nothing extra — it ships **with the plugin** (§3a). Once `spine` is installed and
  enabled, the skill and its MCP tools are both available. (Confirm with `/plugin list`; the skill
  activates on its own when your question matches.)
- **Use it:** ask naturally about a repository. For example —
  - *"Map this repo and tell me what's untested."* → `map_repo`
  - *"What breaks if I change `create_app`?"* → `blast_radius`
  - *"Here's a traceback — where's the bug?"* (paste it) → `localize` → `regression_gaps`
  - *"Where would a rate-limiting feature land in this codebase?"* → `investigate`
- **Scope:** the skill only *reads* — understanding, not editing. To actually change code, use the
  gated [`sdlc_feature`](#sdlc_feature) (which requires an explicit `confirm` for any external write).
- **In Codex:** there's no separate skill file — Codex calls the same MCP tools directly (the tool
  descriptions carry the same guidance). See [CODEX_GUIDE §6](CODEX_GUIDE.md#6-the-tools-spine-exposes).

Each tool below shows the **Claude prompt** (what you type), the **tool call** it maps to
(the literal arguments — handy if you call it programmatically or want to be precise), and
**returns** (the shape of the result). Arguments not shown use their defaults.

---

#### `doctor`

Checks what's wired up. Run this first.

> **Ask Claude:** "Use spine's `doctor` and summarize what's ready."

```jsonc
// tool: doctor   (no arguments)
{}
```
**Returns:** `{ "all_passed": false, "checks": [ { "name": "llm", "passed": true, "detail": "anthropic/claude-opus-5" }, … ] }`

---

#### `pkg_grounding`

Read‑only preview of what Spine would *reuse* in an existing repo for a given idea — the
real symbols, with `file:line`. Great for "what will it build on?" before you commit.

> **Ask Claude:** "Use spine's `pkg_grounding` on `repo_path=/path/to/my/repo` for the spec
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

> **Ask Claude:** "Use spine's `read_memory_bank` on `repo_path=/path/to/my/repo`, section
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

> **Ask Claude:** "Use spine's `ingest_preview` on `file://./roadmap.md` and list the intents."

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
    "summary": "Quote the actual error and name the real files — the root-cause section
                reads an exception named anywhere here, and a path the graph knows becomes
                the fault module.",
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
| `language` | `auto` (detect) or `python` / `java` / `typescript` / `csharp` / `c` / `cpp` / `go` / `sql`. |
| `package_name` | Override the scaffold package name (greenfield). |
| `live` | `false` (default) = local branch + diff, no external writes. `true` = real Jira + push + PR. |
| `confirm` | Must be `true` alongside `live=true` — the explicit authorization for writes. |
| `max_refine` | How many implement→test→refine iterations to allow (default 3). |

**Example A — greenfield (safe):**

> **Ask Claude:** "Use spine's `sdlc_feature` with `source=file://~/specs/slugify.md`,
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

> **Ask Claude:** "Use spine's `sdlc_feature` with `source=file://./rate-limit.md`,
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

> **Ask Claude:** "Use spine's `sdlc_feature` with `source=file://./rate-limit.md`,
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

> **Ask Claude:** "Use spine's `sdlc_start_run` on `file://./roadmap.md`, max 3 features."

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

> **Ask Claude:** "What's waiting on me?" · "Show me the trace for run `<id>`." · "Approve
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

**2. Ask Claude:**

> **"Use spine's `sdlc_feature` with `source=file://~/specs/slugify.md`, `language=python`,
> `layout=new`. Keep it safe (don't open a PR). Then show me the generated files and the
> test result."**

**3. What you get back:** a JSON result with `passed: true`, the `branch`, the generated
`files` (implementation + tests), and `iterations` (how many refine passes it took).
Spine scaffolded a project, wrote `slugify`, wrote tests, and ran them green — all in a
scratch workspace. Nothing was pushed.

**4. Iterate** by editing the acceptance criteria and re‑running, or ask Claude to read the
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

The `confirm=true` is a deliberate second authorization on top of Claude Code's own tool‑use
approval — Spine refuses a live write without it. `live=true` needs a reachable repo
(`repo` or `SDLC_REPO_URL`) and a GitHub token. The same gate guards `sdlc_start_run`'s
`create_jira=true`.

---

## 10. Language support & toolchains

Comprehension covers **eight front-ends**. Spine only needs a language's toolchain when it
**builds/tests** generated code in that language:

| Language | Build/test needs on PATH |
|---|---|
| Python | nothing extra (pytest ships with the engine's `sdlc` extra) |
| Java | a JDK + **Maven** |
| TypeScript | **Node.js** + a package manager (npm/pnpm/yarn) |
| C# | the **.NET SDK** (`dotnet`) |
| C | **CMake** (or **Meson + Ninja**) + a C compiler |
| C++ | **CMake** (or **Meson + Ninja**) + a C++ compiler |
| Go | the **`go`** toolchain (`go build` / `go test`); multi-module aware |
| SQL | nothing extra — schema, queries, stored procedures, ordered-migration folding |

Comprehension front-ends beyond Python install as extras — one at a time
(`pip install 'synaptixs-spine[go]'`) or all at once with `[languages]`, which `[all]`
already includes.

`language=auto` detects from the repo. For C#, Spine additionally lifts ASP.NET Core
endpoints and EF Core entities into the graph; for C/C++ it builds the `#include` graph and
merges header declarations with their definitions; for Go it computes **interface
satisfaction** (`IMPLEMENTS`) by matching method sets.

**How accurate is the graph these tools read?** Measured against a committed corpus covering
all eight front-ends: **precision 1.00 on every node and edge kind, in every language** —
nothing is invented. Recall is 1.00 on everything except `CALLS`, which ranges from 1.00
(C, SQL) to 0.50 (TypeScript); the gap is calls whose receiver is a variable rather than a
name. Run `orchestrator pkg accuracy` to see the current numbers yourself.

> **`--language` is not validated.** An unsupported value silently scaffolds a *Python*
> project rather than erroring, so check your spelling.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| Claude doesn't see Spine's tools | Restart Claude Code or run `/reload-plugins`. Check `/mcp` and `/plugin`. |
| `doctor` says the LLM provider is missing | Your `.env` isn't being found — launch Claude Code from a project with a `.env`, or use the raw‑MCP form (§3b) and set `ORCHESTRATOR_DOTENV` to its **absolute** path. |
| `orchestrator-mcp: command not found` | The server isn't on PATH. `pip install 'synaptixs-spine[all]'`, or point `command` at the absolute path of the console script. |
| The server connects and dies ("Connection closed"), or tools are missing | A **stale** `orchestrator-mcp` on PATH — a console script left by an older checkout's venv. Ask Claude to run `doctor` (or run `orchestrator doctor`): its `server` block names the **version, interpreter and MCP SDK** answering. If they aren't the install you expect: `uv tool install --force 'synaptixs-spine[all]'` (or reinstall into the venv you meant), then restart the host. |
| Codegen times out | Set a faster model: `ORCHESTRATOR_INTAKE_MODEL=...` (or `SDLC_CODEGEN_MODEL`). |
| "live needs a repo to push to" | Pass `repo=...` or set `SDLC_REPO_URL`; ensure `GITHUB_TOKEN`/`GH_TOKEN` is set. |
| A `live` call refuses to write | That's the gate — pass `confirm=true` together with `live=true`. |
| Build fails for Java/TS/C#/C/C++/Go | The language toolchain isn't installed — see [§10](#10-language-support--toolchains). |
| Private repo clone fails | Set `GITHUB_TOKEN` (PAT) or configure the GitHub App. |

For deeper diagnostics, ask Claude to run `doctor`, or run `orchestrator doctor` in a shell
from the folder with your `.env`.

---

## 12. Updating & uninstalling

```
# update the engine (new languages, fixes)
pip install -U 'synaptixs-spine[all]'
/plugin marketplace update spine            # refresh the marketplace snapshot

# remove
/plugin uninstall spine@spine
/plugin marketplace remove spine
# (or delete the spine block from your project's .mcp.json)
```

---

Questions, issues, or want a language/host we don't cover yet? Open an issue at
<https://github.com/synaptixs/spine>. Happy delegating.
