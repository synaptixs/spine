# User Guide — Spine

> **Spine** is the product; it's distributed as the **`synaptixs-spine`** package
> and its command is **`orchestrator`**. Install lines and commands below use those
> names verbatim.

Point it at a **code repo** and a **requirements source** (a Confluence page, a
Notion page, or a Markdown file). It turns requirements into working, tested code
and opens a **reviewed pull request** — pausing for **your approval** at two
points: before it starts building, and before anything merges.

You stay in control the whole way. Nothing is pushed, merged, or written to your
tracker unless you say so.

This guide is a straight line. Do **Step 0 → Step 4** and you'll have your first
change in hand. Everything after that is optional, in roughly the order you'll
want it.

| Steps | You get |
|---|---|
| **0–4** | Installed, configured, and your first feature built locally — then a real PR. |
| **5–6** | Preview before you run; run fully offline on a local model. |
| **7–8** | The hands-off pipeline with a web dashboard, and smarter (agentic) codegen. |
| **9–10** | Connect external tools (MCP), and call Spine from Claude/Codex. |
| **11** | Troubleshooting. |

---

## Step 0 — What you'll need

The essentials (Steps 1–4):

- **Python 3.12+** and **[uv](https://docs.astral.sh/uv/)**
  — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **An LLM** — either an API key (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`)
  **or** [Ollama](#step-6--run-fully-offline-on-a-local-model-no-api-key) for a
  local model with no key (Step 6).

Add these when you want real source/PR work (Step 4 onward):

- **Confluence / Jira** credentials — to read requirements and (optionally) file issues.
- A **GitHub repo** — the repo it builds *into* — plus a token or GitHub App for private repos.

Only for the full pipeline + dashboard (Step 7):

- **Docker** — runs Temporal + Postgres locally.

You do **not** need Docker, a database, or any servers for Steps 1–6.

---

## Step 1 — Install

The published package is `synaptixs-spine`; the command it gives you is
`orchestrator`.

### From PyPI (just the tool)

```bash
pip install synaptixs-spine
orchestrator --help
```

Optional extras, added when you need them:
- `pip install 'synaptixs-spine[sdlc]'` — run the generated tests (the `sdlc feature`/`run` path)
- `pip install 'synaptixs-spine[tui]'` — the `orchestrator tui` terminal UI (Step 7)
- `[java]`, `[typescript]`, `[csharp]`, `[c]`, `[cpp]` — language parsers for comprehension +
  grounding (Python needs no extra). C# codegen also needs the **.NET SDK** (`dotnet`)
  on PATH; C / C++ codegen needs a C / C++ compiler plus **CMake** (greenfield) or
  **Meson + Ninja** (matching the target repo's build system).
- `[mcp]` (MCP client), `[otel]` (live tracing)

### Upgrading

- **PyPI install:** `pip install --upgrade synaptixs-spine` (verify with `pip show synaptixs-spine`).
- **Source checkout:** `git pull && uv sync --extra dev`.

### From source (needed for Step 7's pipeline, or to develop)

```bash
git clone https://github.com/synaptixs/spine
cd spine
uv sync --extra dev            # installs the project + dev tools
uv run orchestrator --help     # in this layout, prefix CLI calls with `uv run`
```

> The rest of the guide writes plain `orchestrator …`. On a source checkout,
> read that as `uv run orchestrator …`.

---

## Step 2 — Configure

```bash
orchestrator init      # scaffolds a commented .env, then checks readiness
# open .env and fill in: your LLM key, your model, and (later) Confluence/Jira + repo
orchestrator doctor    # readiness report — tells you exactly what's set and what's missing
```

`doctor` reads `.env` automatically — run it from the folder that has your
`.env`. Start minimal; you only need the LLM settings for your first run.

| Setting | What it's for | Needed by |
|---|---|---|
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Your LLM provider | Step 3 |
| `ORCHESTRATOR_INTAKE_MODEL` | One model for everything, e.g. `gpt-4o` | Step 3 |
| `CONFLUENCE_*`, `JIRA_*` | Read requirements / file issues | Step 4 |
| `SDLC_REPO_URL` | The repo it builds **into** | Step 4 |
| `GITHUB_TOKEN` *(or `GITHUB_APP_*`)* | Auth for a private target repo | Step 4 |

> **Tip:** set `ORCHESTRATOR_INTAKE_MODEL` explicitly (e.g. `gpt-4o`). One model
> then drives every stage. A heavyweight default can time out on large code
> generations.

---

## Step 2.5 — Understand the project (optional, recommended)

Build a committed, code-true knowledge base before generating anything:

```bash
orchestrator understand .        # writes ./memory-bank/*.md
```

It extracts the Product Knowledge Graph + project profile and renders
`architecture.md`, `domain-model.md`, `tech-context.md`, `conventions.md`, and
`glossary.md` — deterministic, no LLM. Brownfield repos get a real map of what's
there; greenfield repos get a stub that fills in as features land. Re-run anytime
to refresh (`--refresh` re-extracts instead of using the commit cache). Commit
`memory-bank/` so your team — and any AI tool — reads the same project truth.

For a **team-facing snapshot of what the repo is today and how healthy it looks**, use
the Current State report (also no LLM — synthesized from the same graph):

```bash
orchestrator state .                       # developer view, to stdout
orchestrator state . --lens stakeholder    # plain-language view
orchestrator state . --out STATE.md        # write it to a file
```

It renders a plain-language **overview**, an **infrastructure & runtime** breakdown (the
datastores, queues, cloud, container services and external APIs the repo declares it needs —
from its manifests, build files, and `docker-compose`), a **code-structure** map (layout by
component + entry points), a **system-architecture diagram** (components grouped into zones,
with weighted dependency arrows from the import/`#include` graph), a **component-dependency**
table, **call-graph hotspots**, complexity/god-components, test-coverage and recent-activity
signals, a name-based security surface, and prioritized recommendations. A report is a
*view* — re-run to refresh; nothing is written unless `--out` is given.

> **Deep dive:** see **[KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md)** for the full PKG
> guide — the data model, the CLI (`pkg extract` / `export` / `docs`), how grounding
> uses it, and how it works for brownfield and greenfield projects.

> **Multi-language.** Comprehension covers **Python** out of the box and **Java**,
> **TypeScript**, **C#**, **C**, and **C++** when the matching parser extra is installed
> (`pip install 'synaptixs-spine[java]'` / `[typescript]` / `[csharp]` / `[c]` / `[cpp]`).
> `understand`, codegen grounding, and `pkg extract` then process `.java` / `.ts` /
> `.cs` / `.c` / `.h` / `.cpp` / `.hpp` too. For **C#**, the graph additionally captures
> ASP.NET Core endpoints (`EXPOSES`) and EF Core entities (`REFERENCES`); codegen scaffolds a
> solution + xUnit project and runs `dotnet test` (needs the **.NET SDK**). For **C**,
> it builds the `#include` graph and merges header declarations with their source
> definitions; codegen scaffolds a **CMake** project (greenfield) or works in a brownfield
> **Meson** repo, building + testing via `ctest` / `meson test` (needs a C compiler plus
> CMake or Meson+Ninja). **C++** is a superset of the C front-end — it reuses the include
> graph and header/source merge and adds classes, namespaces, inheritance (`IMPLEMENTS`),
> member functions, and templates; codegen scaffolds a CMake **CXX** project and builds +
> tests via `ctest` (needs a C++ compiler plus CMake).

### Working with existing repos (brownfield) — and how knowledge grows

Spine understands a repo from its **own code**: it builds a Product Knowledge Graph
(PKG) and a committed `memory-bank/`, then grounds every action in that. ontomesh
(domain ontology) is an *optional* add-on — not the thing that reads your code.

**Brownfield — comprehend, then deliver:**

1. **Comprehend** the existing codebase (deterministic, no LLM):
   ```bash
   orchestrator understand .       # PKG → memory-bank/*.md
   orchestrator profile .          # quick architecture profile
   orchestrator audit .            # surface findings / issues
   ```
2. **Deliver** new intents or bug fixes, grounded in the repo's real layout and
   conventions — `--layout auto` follows the existing structure and never scaffolds:
   ```bash
   orchestrator sdlc feature --source file://./spec.md --safe
   ```
   The `[grounding] target-KG context: N chars` line is it reusing what's already there.
3. **Findings** come from the same PKG: blast-radius scoping, the reviewer pass, `audit`.

**Greenfield — knowledge grows as you build.** The first `understand` writes a stub and
the first `feature` run scaffolds `src/<package>/` + `tests/`. From there the PKG and
`memory-bank/` fill in with every feature that lands — re-run
`orchestrator understand . --refresh` so the committed knowledge keeps pace. The repo
accumulates its own code-true memory as it grows.

> **Optional domain layer.** For domain-heavy systems (fraud, telecom, healthcare) you
> can compose *business-domain* knowledge on top of the code-true grounding (Spine
> Seam 1, `SPINE_ONTOMESH_URL`) — see [OPERATIONS.md](OPERATIONS.md#the-semantic-spine).
> It augments comprehension; it never replaces the PKG.

---

## Step 3 — Your first build (local and safe)

This is the core loop, with zero infrastructure. It reads one requirement, writes
code grounded in the target repo's own structure, generates tests, and leaves you
a **local branch + diff** to inspect. No pushes, no PRs, nothing external.

```bash
orchestrator sdlc feature --source confluence://<page_id> --safe
```

- `--source` also accepts `notion://<page_id>` or `file://./spec.md`.
- `--safe` is the safe default: dry-run tracker, local commit, **no push**.
- Pin one requirement with `--intent <intent-id>` if a page has several.

> **Stable, tracked backlog.** The extracted intents are cached deterministically
> (first run extracts; later runs reuse — no re-fetch, no LLM — until `--refresh`),
> so a pinned `--intent` is stable. Each run also writes a **`BACKLOG.md`** ledger:
> `[ ]` todo, `[~]` in progress (a `--live` PR is open), `[x]` done (PR merged, via
> `sdlc complete`). View/regenerate it anytime with
> `orchestrator backlog --source confluence://<page_id>`.

> **Isolated test env.** The pipeline runs the generated tests in a **per-project
> venv** it creates in the worktree, installing pytest + the project's deps (and any
> missing import) automatically — so you don't pre-install test deps. Optional knobs:
> `SDLC_TEST_ISOLATION=local` uses the orchestrator's own interpreter instead;
> `ORCHESTRATOR_LLM_TIMEOUT_SECONDS` (default 120) widens the LLM timeout for large pages.

**Code structure (`--layout`).** The runner places generated code by target:
- **Greenfield repo** → it **scaffolds** a `src/<package>/` skeleton (package name
  derived from the repo, e.g. `Example-Service.` → `example_service`),
  with `tests/` and a pytest-ready `pyproject`, then generates into it. The first
  `--live` run lands this structure on the remote as part of the PR; later runs detect
  it and extend it.
- **Existing codebase** → it **follows the repo's layout**, never scaffolding.
- Default is `--layout auto` (the above). Force it with `--layout new|existing`, and
  override the name with `--package-name <name>`.

As it runs it prints each stage, including `[layout] mode=… package=…` and
`[grounding] target-KG context: N chars` — that's it reading the existing codebase so
the new code reuses what's already there. When it finishes, check out the branch it
made and read the diff.

---

## Step 4 — Go live: a real issue + pull request

When the local result looks right, let it do the real thing — file a Jira issue,
push a branch, and open a PR:

```bash
orchestrator sdlc feature --source confluence://<page_id> --live
```

A human reviews and merges the PR (it never merges on its own). After the merge,
close the loop so the tracker issue moves to Done:

```bash
orchestrator sdlc complete --pr https://github.com/<owner>/<repo>/pull/<n>
```

That's the whole everyday workflow: **`feature --safe` → look → `feature --live`
→ merge → `complete`.**

---

## Step 5 — Preview before you run (read-only)

Want to see what it *would* do without spending an LLM call or touching anything?

```bash
orchestrator ingest --source confluence://<page_id>     # the backlog it reads (dry-run)
orchestrator ingest --source confluence://<page_id> --create   # actually file the issues

orchestrator profile .                                   # languages, framework, DB, tests, task type
orchestrator catalog plan . --intent "Add CSV export"    # the capabilities it would assemble (Step 8)
```

All read-only except `ingest --create`. (The same backlog preview is also a
point-and-click page in the web UI — **Backlog**, `/app/backlog` — see Step 7.)

---

## Step 6 — Run fully offline on a local model (no API key)

No key, fully private: point it at [Ollama](https://ollama.com) (local or a
hosted endpoint). `doctor` accepts Ollama as a valid provider.

```bash
# Local: `ollama pull qwen2.5-coder` then `ollama serve`, then in .env:
OLLAMA_API_BASE=http://localhost:11434
ORCHESTRATOR_INTAKE_MODEL=ollama/qwen2.5-coder

# Hosted Ollama / any OpenAI-compatible endpoint:
OLLAMA_API_BASE=https://your-ollama-host
```

**Model choice matters more than the provider.** Reading requirements and review
run fine on modest models, but code generation emits strict JSON and anchored
edits — use a **coder** model there. You can even mix local and cloud per stage:

```bash
ORCHESTRATOR_INTAKE_MODEL=ollama/qwen2.5-coder   # cheap stages, local
SDLC_CODEGEN_MODEL=gpt-4o                         # codegen, cloud quality
SDLC_REVIEW_MODEL=ollama/qwen2.5-coder            # the review judge
```

---

## Step 7 — The full pipeline + web dashboard

Steps 3–6 build one requirement at a time from the terminal. When you want
**hands-off runs across many requirements**, the **web UI** (a delegation inbox,
console, and trace), and durable execution that survives restarts, switch on the
pipeline. This is the part that
needs Docker, and it runs from a **source checkout** (Step 1, option 2).

**7.1 — Start the stack and create the database (once):**
```bash
docker compose -f docker-compose.dev.yml up -d     # Temporal + Postgres + MinIO
set -a; source .env; set +a                        # load .env into this shell
export SDLC_CODEGEN=llm ORCHESTRATOR_API_KEY=dev-key
# The web UI signs cookies with this secret — set any random value:
export ORCHESTRATOR_SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
uv run alembic upgrade head                        # create the schema
```

**7.2 — Start the two processes (separate terminals):**
```bash
# Terminal 1 — the worker that executes the pipeline stages:
uv run python -m orchestrator.sdlc.worker

# Terminal 2 — the REST API + the whole web UI (one process serves every page):
uv run uvicorn orchestrator.registry.api.app:app --factory --port 8000
```

> The worker reads the **process environment**, not `.env` — always
> `set -a; source .env; set +a` in its terminal first.

**7.3 — Sign in, then launch a run.** Open `http://localhost:8000/app` — you're sent
to `/login`; sign in once with your `ORCHESTRATOR_API_KEY` (the session cookie then
authenticates every page). Start a run from the **Inbox** (paste a source, click
**Delegate**), or from the terminal:
```bash
orchestrator sdlc run --source confluence://<page_id> --max-features 1
# prints the sdlc_id and two gate ids: sdlc-<id>-0 (intents), sdlc-<id>-1 (merge)
```

Approve a gate by clicking **Approve / Reject** in the Inbox or Console — or via the API:
```bash
curl -X POST -H "x-api-key: dev-key" http://localhost:8000/v1/approvals/sdlc-<id>-0/approve
```

**7.4 — The web UI (sign in at `/login` first):** one app, one nav, one login.

| URL | What you see |
|---|---|
| `/app/inbox` | **Inbox** — delegate a run, watch it progress **live** (server-sent events), and approve/reject its gates **inline**. The front door. |
| `/console` | **Operator console** — the approval queue + the runs dashboard. |
| `/app/backlog` | **Backlog preview** — a Confluence page rendered as a derived backlog (read-only). |
| `/trace/<sdlc_id>` | **Run timeline** — the ordered stages for one run (intake → codegen → tests → review → merge). |
| `/app/personas` | **Personas & skills** — the personas the engineer adopts and the skills they apply. |
| `http://localhost:8233` | **Temporal UI** — the raw execution: every activity, retries, per-stage pass/fail (where the actual test output lives). |

> Prefer the terminal? `pip install 'synaptixs-spine[tui]'` then `orchestrator tui`
> — the same watch-runs / clear-gates / delegate actions, keyboard-driven, over the
> same API (`ORCHESTRATOR_API_URL` + `ORCHESTRATOR_API_KEY`, or `--api-url`/`--api-key`).

For raw test output and per-activity detail, the **Temporal UI** (or the worker
logs) is the source of truth.

---

## Step 8 — Smarter codegen: it adapts to each repo

Out of the box, Spine **profiles each project and assembles the right
capabilities** instead of treating every repo the same — and can run codegen as
an **agentic loop** that uses tools mid-task.

**8.1 — See the plan (read-only, no LLM):**
```bash
orchestrator profile .                                  # what kind of project this is
orchestrator catalog list                               # every capability it can assemble
orchestrator catalog plan . --intent "Add CSV export"   # what it would use here
```
A plan is three things, each chosen by a rule from the profile: **skills** (match
the repo's conventions), **mcp_servers** (external tools to use — Step 9), and
**workflow_params** (run shape). In the pipeline (Step 7) the plan is shown **at
the intent gate**, so you approve the toolkit alongside the work.

**8.2 — Turn on the agentic loop:**
```bash
export SDLC_CODEGEN=llm            # real LLM codegen (not the built-in stub)
export SDLC_AGENTIC_CODEGEN=1      # run codegen as a think → act → observe loop
```
Instead of one shot, the agent reads files, queries the repo's knowledge graph,
writes, runs the tests, and fixes — and (Step 9) calls approved external tools.

- **Off by default** — single-shot stays default until you're happy with cost
  (the loop makes several model calls per feature).
- **Needs a tool-calling model** (`gpt-4o`, `claude-*`); otherwise it falls back
  to single-shot.
- **Safe by construction** — a hard step cap, a per-run spend budget
  (`SDLC_RUN_BUDGET_USD`), the same write guards as single-shot, and external
  tools allow-listed + write-gated. Destructive tool calls can **pause for your
  approval** mid-run, then resume with your decision.

You extend the catalog with new skills/tools/run-shapes via a selector
(language × task-type × has-DB); the planner picks them up automatically.

---

## Step 9 — Connect external tools (MCP)

Spine can use external **[MCP](https://modelcontextprotocol.io)
servers** — read Confluence/Jira through Atlassian's MCP server, introspect a
database, and more — reusing the same `mcpServers` config you already use with
Claude or Codex.

**9.1 — Install + point at a config:**
```bash
pip install 'synaptixs-spine[mcp]'        # or from source: uv sync --extra mcp
```
Supply an `mcpServers` JSON file via `--config`, `$ORCHESTRATOR_MCP_CONFIG`, or
`./mcp.json`. Transport is inferred (`command` → stdio, `url` → HTTP). Example
`mcp.json` for Atlassian:
```json
{
  "mcpServers": {
    "confluence": {
      "command": "uvx",
      "args": ["mcp-atlassian"],
      "env": {
        "CONFLUENCE_URL": "https://your-org.atlassian.net/wiki",
        "CONFLUENCE_USERNAME": "you@org.com",
        "CONFLUENCE_API_TOKEN": "<token>",
        "JIRA_URL": "https://your-org.atlassian.net",
        "JIRA_USERNAME": "you@org.com",
        "JIRA_API_TOKEN": "<token>"
      },
      "allow": ["confluence_get_page", "confluence_get_page_children", "jira_get_issue", "jira_search"]
    }
  }
}
```
- **`allow`** is an allow-list — only those tools are callable (omit = all, with a warning).
- **Writes are off by default**: mutating tools are refused unless you set
  `write_enabled: true` on that server.

**9.2 — Inspect + call:**
```bash
orchestrator mcp list                       # discovered tools (server:tool)
orchestrator mcp contracts                  # governance view: side-effects + write-gating
orchestrator mcp call confluence:confluence_get_page --args '{"page_id":"123"}'
```

**9.3 — Use them in a run:**
```bash
# Drive intake through Atlassian's MCP server instead of REST creds:
orchestrator sdlc feature --source mcp-confluence://<page_id> --safe
# Feed a database's real schema into codegen's grounding:
orchestrator mcp ingest-db --server <db-server-name>
```
In the pipeline (Step 7), configured MCP tools are auto-onboarded at startup with
the same rate-limit + audit + approval path.

---

## Step 10 — Call it from Claude, Codex, or your IDE (MCP server)

The reverse of Step 9: Spine can **become** an MCP server, so any host
— Claude Code, the Codex app, Claude Desktop, claude.ai — can call your
"intent → reviewed PR" pipeline as tools, with the **same human gates**.

```bash
pip install 'synaptixs-spine[mcp]'        # or from source: uv sync --extra mcp
```

**10.1 — Local (the host launches it over stdio):**
```bash
orchestrator-mcp            # serves over stdio, reads ./.env for creds
```
Register with Claude Code via `.mcp.json`:
```json
{ "mcpServers": { "orchestrator": { "command": "orchestrator-mcp" } } }
```

**10.2 — Remote (hosted clients connect to a URL):**
```bash
orchestrator-mcp --http --host 0.0.0.0 --port 8080     # behind TLS in production
```
Pick one auth mode (a public bind without either is refused):
- **Shared secret** — set `ORCHESTRATOR_MCP_TOKEN` + `ORCHESTRATOR_MCP_RESOURCE_URL`; the client sends that token as its bearer.
- **OAuth introspection** — set `ORCHESTRATOR_MCP_ISSUER_URL`, `…_INTROSPECTION_URL`, client id/secret, and `…_REQUIRED_SCOPES` to validate every token against your IdP.

Destructive tools stay gated regardless of auth: `sdlc_feature(live=true)` and
`sdlc_start_run(create_jira=true)` both need an explicit `confirm=true`.

---

## Step 11 — Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor` shows everything missing | Run it from the folder that has your `.env`. |
| Codegen times out | Set `ORCHESTRATOR_INTAKE_MODEL=gpt-4o` (or another fast model). |
| Private repo clone fails | Set `GITHUB_TOKEN` (PAT) or the GitHub App (`GITHUB_APP_*`). |
| Console asks for an API key | Paste the `ORCHESTRATOR_API_KEY` you started the server with (`dev-key` above). |
| `sdlc run` hangs at a gate | Approve it in the console or via `/v1/approvals/.../approve`. |
| Worker does nothing | It reads the process env, not `.env` — `set -a; source .env; set +a` before starting it. |
| `mcp list` shows no servers | Add an `mcpServers` file (`--config`, `$ORCHESTRATOR_MCP_CONFIG`, or `./mcp.json`). |
| `mcp` commands fail to import | Install the extra: `pip install 'synaptixs-spine[mcp]'` (or `uv sync --extra mcp`). |
| An MCP tool is "not allow-listed" / write-gated | Add it to the server's `allow`; for mutating tools set `write_enabled: true`. |
| Agentic loop falls back to single-shot | Use a tool-calling model (`gpt-4o`, `claude-*`) and set `SDLC_CODEGEN=llm`. |
| `orchestrator-mcp --http` refuses to start | Set `ORCHESTRATOR_MCP_TOKEN` or `…_INTROSPECTION_URL`, bind `127.0.0.1`, or pass `--allow-unauthenticated` on a trusted net. |
| Remote client gets 401 | Send `Authorization: Bearer <token>`; for introspection confirm the token is active and carries the required scope. |
| `Nondeterminism error` on replay | An in-flight workflow predates a code change. Terminate the stale run (Temporal UI); new runs are unaffected. |

---

Built something with it, or hit a snag this guide didn't cover? Open an issue —
see [CONTRIBUTING.md](CONTRIBUTING.md).
