# Using Spine from the Codex app

**Spine** (the `synaptixs-spine` / *agent-orchestrator* engine) is an AI‑native SDLC
engineer you delegate tickets to. From inside the **Codex app** you can ask it to read a
requirement, ground new code in your repo's real structure, generate and test that code,
and — when you say so — open a pull request. It works for **greenfield** (fresh) and
**brownfield** (existing) repos across **Python, Java, TypeScript, C#, C, and C++**.

This guide takes you from zero to a delivered feature, entirely through Codex.

> **New to Spine itself?** [USER_GUIDE.md](USER_GUIDE.md) covers the CLI and concepts;
> this guide is specifically about driving Spine from Codex.

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

Codex talks to Spine over **MCP** (Model Context Protocol). Spine runs as a small local
server (`orchestrator-mcp`) that Codex launches as a subprocess; Codex then calls Spine's
capabilities as **tools**. Each tool runs the real engine — PKG grounding, codegen,
test/refine — and Spine clones/branches your target repo into a scratch workspace, so your
working tree is never touched until you choose to push.

There are **two layers** to the integration, and you only need one:

| | What it is | When to use |
|---|---|---|
| **Plugin** | A packaged, branded entry in Codex's plugin list (it *bundles* the MCP server). | The friendly path — install once, toggle in the UI. |
| **MCP server** | A raw `[mcp_servers]` entry in `~/.codex/config.toml`. | Power users / scripted setups / full control over env + paths. |

Both expose the exact same tools.

---

## 2. Prerequisites

- **Codex** installed. On **macOS** the app bundles the CLI at
  `/Applications/Codex.app/Contents/Resources/codex` — add an alias if you like:
  ```bash
  alias codex="/Applications/Codex.app/Contents/Resources/codex"
  ```
  On **Linux / Windows** (or if you prefer the standalone CLI on macOS), install it with
  `npm i -g @openai/codex` (or `brew install codex`) so `codex` is on your PATH. Everything
  below is CLI-driven and works the same across platforms; only this path differs.
- **Python 3.12+**.
- **An LLM provider key** — Anthropic, OpenAI, or a local Ollama endpoint.
- **Per‑language build tools** only if you want Spine to *build/test* generated code in
  that language — see [§10](#10-language-support--toolchains). For Python, nothing extra.
- *(Optional, for `live` PRs)* a **GitHub** token and, if you create tickets, **Jira**.

---

## 3. Install (two ways)

### 3a. As a Codex plugin (recommended)

```bash
pip install 'synaptixs-spine[all]'                 # provides the `orchestrator-mcp` command
codex plugin marketplace add synaptixs/spine       # add the Spine marketplace
codex plugin add spine@spine                        # install the plugin
codex plugin list | grep spine                      # → spine@spine  installed, enabled
```

> **`[all]`, not `[mcp]`.** `[mcp]` installs the server alone, so the graph is **Python-only** —
> and silently so: a Java or Go repo yields zero nodes rather than an error, which reads as
> "nothing here" instead of "nothing parsed". `[all]` adds every language front-end, doc
> ingestion and the MCP server; `[languages]` is the front-ends on their own.


Restart the Codex app. **Spine** now appears in your plugin list.

> Prefer a local checkout? `codex plugin marketplace add ./codex-marketplace` from this
> repo instead of `synaptixs/spine`.

### 3b. As a raw MCP server

Add this to `~/.codex/config.toml`:

```toml
[mcp_servers.spine]
command = "orchestrator-mcp"      # or an absolute path to the venv's console script
args = []
startup_timeout_sec = 60
tool_timeout_sec = 600            # sdlc_feature does codegen + a build — give it room

[mcp_servers.spine.env]
ORCHESTRATOR_DOTENV = "/abs/path/to/your/.env"   # see §4
```

Restart Codex. Verify with `codex mcp list` (you should see `spine`).

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

**How Spine finds your `.env`:** because Codex launches the server from its own working
directory, point it at an absolute path with `ORCHESTRATOR_DOTENV` (in the plugin/server
env, as shown in §3b). Read‑only tools (`doctor`, `pkg_grounding`) work without any creds.

> Tip: set a *fast, capable* model. A slow default can time out on large generations.
> `ORCHESTRATOR_INTAKE_MODEL` sets the default; `SDLC_CODEGEN_MODEL` overrides codegen only.

---

## 5. Verify the connection

In a Codex chat, just ask:

> **"Use spine's `doctor` tool and show me what's ready."**

You'll get a readiness report (LLM provider, source, tracker, GitHub). Then confirm the
tools are visible:

```bash
codex mcp list           # spine → enabled
codex plugin list        # spine@spine → installed, enabled   (if you used the plugin)
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
| **Plan it, then decide — before anything is built** | | |
| [`sdlc_plan`](#sdlc_plan) | **The build document.** Twelve grounded sections for one ticket: requirement, intent, root cause, what the graph knows, blast radius, design, files, acceptance criteria, the codegen prompt, cost and confidence — each labelled with where it came from. **No model, no credentials, nothing spent.** | `.spine/` |
| [`sdlc_approve`](#sdlc_approve) | Record that a **human** read that document and decided. Binds to a digest of it, so a plan that changes afterwards reads as *stale* rather than still approved. | `.spine/` |
| [`sdlc_feature`](#sdlc_feature) | **Ship it.** One intent end‑to‑end: spec → grounded codegen → tests → branch → *(optionally)* PR. | gated |
| [`sdlc_start_run` + gate tools](#the-autonomous-run-sdlc_start_run--friends) | Drive the long, autonomous, gated run as a job (needs the Mode‑B backend). | gated |

> **The comprehension tools are read‑only, need no credentials, and are deterministic** (only
> `root_cause`'s opt‑in `use_llm` uses a model) — the differentiator: they don't just map the code,
> they hand Codex **engineering decisions** (what breaks, what's untested, where a change lands).
> `repo_path` is a **local path or a git URL** (shallow‑cloned behind the same host allow‑list as the
> CLI); each returns structured fields **plus** a `markdown` rendering, bounded (top‑N with
> `file:line`). Example — *"Use spine's `blast_radius` on `repo_path=.` for `symbol=create_app` and
> tell me what a change would touch."* To serve these to a **remote** Codex/host over HTTP, run the
> streamable‑HTTP server (`orchestrator-mcp --http`, bearer/OAuth auth from env — see §3b); it
> registers the exact same tools.

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

Each tool below shows the **Codex prompt** (what you type), the **tool call** it maps to
(the literal arguments — handy if you call it programmatically or want to be precise), and
**returns** (the shape of the result). Arguments not shown use their defaults.

---

#### `doctor`

Checks what's wired up. Run this first.

> **Ask Codex:** "Use spine's `doctor` and summarize what's ready."

```jsonc
// tool: doctor   (no arguments)
{}
```
**Returns:** `{ "all_passed": false, "checks": [ { "name": "llm", "passed": true, "detail": "anthropic/claude-opus-5" }, … ] }`

---

#### `pkg_grounding`

Read‑only preview of what Spine would *reuse* in an existing repo for a given idea — the
real symbols, with `file:line`. Great for "what will it build on?" before you commit.

> **Ask Codex:** "Use spine's `pkg_grounding` on `repo_path=/path/to/my/repo` for the spec
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

> **Ask Codex:** "Use spine's `read_memory_bank` on `repo_path=/path/to/my/repo`, section
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

> **Ask Codex:** "Use spine's `ingest_preview` on `file://./roadmap.md` and list the intents."

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
(`.spine/plans/<INTENT>-build.md`). **A spec it cannot validate is refused**, with the
specific problem and the valid field names.

> **This is the tool for a machine with no model API key.** Codex has the model and — through
> its own onboarded `mcp-atlassian` server — the tracker credentials, including enterprise
> Jira behind a personal access token that Spine's own adapter cannot speak to. Read the
> ticket yourself, draft the spec, call this. The document comes back grounded in the repo's
> real graph, and Spine never needed a key of its own.

> **`met_criteria` is the field worth your attention.** It maps a stated criterion to the
> evidence that existing code *already satisfies it*. No deterministic pass can make that
> call — but you can: read the ticket, then check with `explain_symbol` or `blast_radius`
> before filling it in. On the ticket this was built from, two of six criteria described
> behaviour that already existed, and a run would have reported them met having changed
> nothing.

#### `sdlc_approve`

Records that a **human** read the document and decided. Binds to a digest of it, so a plan
that changes afterwards reads as *stale* rather than still approved.

```jsonc
// tool: sdlc_approve
{ "repo_path": ".", "intent_id": "PROJ-14", "decided_by": "alice", "note": "why" }
// add "reject": true to record a rejection instead
```

**Do not call this on the user's behalf without being asked.** It records a human decision,
and the tool refuses rather than inventing an approver when it cannot tell who decided.

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

> **Ask Codex:** "Use spine's `sdlc_feature` with `source=file://~/specs/slugify.md`,
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

> **Ask Codex:** "Use spine's `sdlc_feature` with `source=file://./rate-limit.md`,
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

> **Ask Codex:** "Use spine's `sdlc_feature` with `source=file://./rate-limit.md`,
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

> **Ask Codex:** "Use spine's `sdlc_start_run` on `file://./roadmap.md`, max 3 features."

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

**2. Ask Codex:**

> **"Use spine's `sdlc_feature` with `source=file://~/specs/slugify.md`, `language=python`,
> `layout=new`. Keep it safe (don't open a PR). Then show me the generated files and the
> test result."**

**3. What you get back:** a JSON result with `passed: true`, the `branch`, the generated
`files` (implementation + tests), and `iterations` (how many refine passes it took).
Spine scaffolded a project, wrote `slugify`, wrote tests, and ran them green — all in a
scratch workspace. Nothing was pushed.

**4. Iterate** by editing the acceptance criteria and re‑running, or ask Codex to read the
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

The `confirm=true` is a deliberate second authorization on top of Codex's own tool‑use
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
| Go | the **`go` toolchain** (`go build` / `go test`); multi-module aware |
| SQL | nothing extra — schema, queries, stored procedures, ordered-migration folding |

Comprehension front-ends beyond Python install as extras — one at a time
(`pip install 'synaptixs-spine[go]'`) or all at once with `[languages]`, which `[all]`
already includes.

`language=auto` detects from the repo. For C#, Spine additionally lifts ASP.NET Core
endpoints and EF Core entities into the graph; for C/C++ it builds the `#include` graph and
merges header declarations with their definitions; for Go it computes **interface
satisfaction** (`IMPLEMENTS`) by matching method sets.

> **`--language` is not validated.** An unsupported value silently scaffolds a *Python*
> project rather than erroring, so check your spelling.

---

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| Codex doesn't see Spine's tools | Restart Codex after install. Check `codex mcp list` / `codex plugin list`. |
| `doctor` says the LLM provider is missing | Your `.env` isn't being found — set `ORCHESTRATOR_DOTENV` to its **absolute** path in the server/plugin env. |
| `orchestrator-mcp: command not found` | The server isn't on PATH. `pip install 'synaptixs-spine[all]'`, or point `command` at the absolute path of the console script. |
| Codegen times out | Set a faster model: `ORCHESTRATOR_INTAKE_MODEL=...` (or `SDLC_CODEGEN_MODEL`). Raise `tool_timeout_sec` for the server. |
| "live needs a repo to push to" | Pass `repo=...` or set `SDLC_REPO_URL`; ensure `GITHUB_TOKEN`/`GH_TOKEN` is set. |
| A `live` call refuses to write | That's the gate — pass `confirm=true` together with `live=true`. |
| Build fails for Java/TS/C#/C/C++ | The language toolchain isn't installed — see [§10](#10-language-support--toolchains). |
| Private repo clone fails | Set `GITHUB_TOKEN` (PAT) or configure the GitHub App. |

For deeper diagnostics, ask Codex to run `doctor`, or run `orchestrator doctor` in a shell
from the folder with your `.env`.

---

## 12. Updating & uninstalling

```bash
# update the engine (new languages, fixes)
pip install -U 'synaptixs-spine[all]'
codex plugin marketplace upgrade            # refresh a Git marketplace snapshot

# remove
codex plugin remove spine@spine
codex plugin marketplace remove spine
# (or delete the [mcp_servers.spine] block from ~/.codex/config.toml)
```

---

Questions, issues, or want a language/host we don't cover yet? Open an issue at
<https://github.com/synaptixs/spine>. Happy delegating.
