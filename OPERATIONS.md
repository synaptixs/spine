# Operations & Developer Guide — Spine

How to **run, configure, and operate** Spine beyond the everyday build: deployment
modes, the full pipeline, and the steps to turn on each advanced
capability — including the semantic spine (ontomesh × Spine × infodrift).

See [SETUP.md](SETUP.md) for first install + the local stack, [USER_GUIDE.md](USER_GUIDE.md)
for the everyday workflow, and [README capability table](README.md#capabilities) for the capability catalog.

---

## Deployment modes
Start small; add infrastructure only when you need it.

| Mode | What runs | When |
|---|---|---|
| **CLI / local** | Just the `orchestrator` CLI | First builds, `--safe` runs, PKG, `understand`, remediation. No DB. |
| **Service** | REST API + web dashboard | Approvals UI, trace inspection, team use. Needs Postgres. |
| **Full pipeline** | API + Temporal worker + Postgres + MinIO | Orchestrated multi-feature runs, post-merge activities. |

Bring-up for service and full-pipeline modes (docker compose + migrations + worker)
is in [SETUP.md](SETUP.md).

---

## Environment-variable reference
[SETUP → Environment](SETUP.md#7-environment) is the authoritative reference for
credentials, defaults and optional switches. The sections below show how to use them.

---

## Step 7 — The full pipeline + web dashboard

Steps 3–6 build one requirement at a time from the terminal. When you want
**hands-off runs across many requirements**, the **web UI** (a delegation inbox,
console, and trace), and durable execution that survives restarts, switch on the
pipeline. This is the part that
needs Docker and a [source checkout](SETUP.md#2-install-from-source).

**7.1 — The one-command way (recommended):**
```bash
orchestrator up
```
That single command brings up the Docker infra (Postgres + Temporal), applies
migrations, and launches **both** the web/API server and the SDLC worker with
sensible defaults. When it prints **“Spine is up”**, open `http://localhost:8000/app`
and log in with the API key it shows (`dev-key` by default). **Ctrl-C** stops the app
processes (the infra containers stay up for fast restarts). It needs Docker running
and an LLM key in your `.env` (for real codegen). Flags: `--port`, `--no-worker`
(browse-only), `--no-docker` (infra already running), `--compose-file`.

Prefer to run the pieces yourself (or need to customise ports/env)? The manual path
below is exactly what `orchestrator up` automates.

**7.1a — Start the stack and create the database (once):**
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
uv run uvicorn orchestrator.registry.api.app:create_app --factory --port 8000
```

> The worker reads the **process environment**, not `.env` — always
> `set -a; source .env; set +a` in its terminal first.

**7.3 — Sign in, then launch a run.** Open `http://localhost:8000/app` — you're sent
to `/login`; sign in once with your `ORCHESTRATOR_API_KEY` (the session cookie then
authenticates every page). Start a run from the **Inbox** (paste a source, click
**Delegate**), or from the terminal:
```bash
orchestrator sdlc run --source confluence://<page_id> --max-features 1
# prints the sdlc_id and the gate ids: sdlc-<id>-0 (intents), sdlc-<id>-1 (merge),
# plus sdlc-<id>-2 (designs) when the design gate is enabled — see 7.3a
```

Approve a gate by clicking **Approve / Reject** in the Inbox or Console — or via the API:
```bash
curl -X POST -H "x-api-key: dev-key" http://localhost:8000/v1/approvals/sdlc-<id>-0/approve
```

**7.3a — What a run does before it writes code.** The pipeline **comprehends the repo**
first — it builds the same knowledge graph + `episteme/` as `understand`, and folds a
one-line summary into the intents gate, so you approve the extracted intents *and* see that
Spine read the codebase. After you approve intents and it creates issues, a **design wave**
produces a grounded, per-issue **design** (approach, files-to-touch, interfaces, risks, test
strategy — anchored to the repo's real modules) that each feature then builds to. Both the
comprehension and the designs are saved as **run artifacts**: expand the run in the **Console**
to download `knowledge-graph.db`, the episteme docs, and each issue's
`design.md` / `design.json`.

Three pipeline flags control this (comprehension + design are **on** by default; the extra
design *gate* is **off** so runs don't gain a mandatory pause unless you want one):

See [SETUP → Pipeline stages and gates](SETUP.md#pipeline-stages-and-gates) for the defaults and switches.

**7.4 — The web UI (sign in at `/login` first):** one app, one nav, one login. The
left sidebar groups every surface into sections:

**Deliver** — hand work over and watch it ship.
| URL | What you see |
|---|---|
| `/app/inbox` | **Inbox** — delegate a run, watch it progress **live** (server-sent events), approve/reject gates **inline**. The front door. |
| `/app/intake` | **Intake studio** — preview any source (Confluence / Notion / file / OpenSpec) as a backlog, then delegate a gated run (dry-run by default). |
| `/app/backlog` | **Backlog preview** — a source rendered as a derived backlog (read-only). |
| `/console` | **Console** — the approval queue + runs dashboard (state filter, inline trace, export a run's timeline). Expand a run to download its **comprehension + design artifacts** (knowledge graph, episteme, per-issue designs). |

**Understand** — repo intelligence. Point at a **local path _or_ a git URL** (GitHub / Bitbucket / GitLab / enterprise) — **Browse…** picks a local folder, or paste a URL (cloned on demand).
| URL | What you see |
|---|---|
| `/app/understand` | Build the code-true **episteme** for a repo (runs as a job, with live progress). |
| `/app/state` | **Current State** report (developer / stakeholder lens), rendered in-app. |
| `/app/memory-bank` | Browse a repo's committed `episteme/*.md`. |
| `/app/graph` | **Knowledge graph** — a module-level overview (node/edge mix, biggest modules, dependencies, top symbols). |
| `/app/catalog` | What Spine can do in this repo — the capability catalog + a per-intent plan. |

**Govern** — the "governed autonomy" story, made visible.
| URL | What you see |
|---|---|
| `/app/audit` | **Audit log** — the append-only record of every action; filter by run / actor / action. |
| `/app/governance` | **Policy & budget** — per-run spend vs the cap, policy + approval decisions, and a one-click run-bundle **export**. |

**Quality**
| URL | What you see |
|---|---|
| `/app/evals` | **Evals** — skill quality + how the eval harness works. |
| `/app/memory` | **Cross-run memory** — the conventions / pitfalls the engineer learned across runs. |
| `/app/advanced` | **Advanced** — which gated subsystems (agentic loop, semantic spine) are wired. |

**Connect · Registry · System**
| URL | What you see |
|---|---|
| `/app/connections` | **Connections** — MCP servers (list, live-test, **browse to pick an `mcp.json`**, and — when enabled — add/edit/remove) + source/tracker status. |
| `/app/registry` | **Registry** — agent templates, tool contracts, glossary. |
| `/app/personas` | **Personas & skills** — the personas the engineer adopts and the skills they apply. |
| `/app/system` | **System** — readiness (the `doctor` env checks) + a live database probe. |
| `/trace/<sdlc_id>` | **Run timeline** — the ordered stages for one run (intake → codegen → tests → review → merge). |
| `http://localhost:8233` | **Temporal UI** — the raw execution: every activity, retries, per-stage pass/fail (where the actual test output lives). |

**7.5 — Access & safety config (safe by default).** Two surfaces reach beyond the
current repo — analysing a repo by URL, and editing MCP config — so both are gated
by environment variables you opt into:

See [SETUP → Repository and MCP access](SETUP.md#repository-and-mcp-access) for the defaults and switches.

> Non-GitHub private repos authenticate via your **ambient git credentials**
> (SSH agent / credential helper / a token in the URL); GitHub uses `GITHUB_TOKEN`
> or a GitHub App. Public repos need nothing.

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
- **Needs a tool-calling model** (`claude-*`, `gpt-5*`); otherwise it falls back
  to single-shot.
- **Safe by construction** — a hard step cap, a per-run spend budget
  (`SDLC_RUN_BUDGET_USD`), the same write guards as single-shot, and external
  tools allow-listed + write-gated. Destructive tool calls can **pause for your
  approval** mid-run, then resume with your decision.

You extend the catalog with new skills/tools/run-shapes via a selector
(language × task-type × has-DB); the planner picks them up automatically.

---

## Step 9 — Connect external tools (MCP)

`orchestrator mcp contracts` shows the governed ToolContract derived for each
onboarded MCP tool, and labels every argument with its **declared type**, read
from the server's own JSON Schema (nothing is stored — the labels are derived on
every read). A union type is joined with `|`, and an argument the schema doesn't
give a top-level `type` (an `anyOf`, a `$ref`, or a tool with no schema at all)
is labelled `any`:

```json
{
  "contract_id": "mcp.atlassian.jira_search",
  "inputs": ["additional_fields (string)", "limit (integer)", "cursor (string|null)", "payload (any)"],
  "input_types": {"additional_fields": "string", "limit": "integer"}
}
```

Use it to see an argument's expected shape *before* a call fails on a type
mismatch.

**Stringly-typed arguments are encoded for you.** Some servers type a structured
argument as a *string of JSON* rather than an object — Atlassian's
`jira_create_issue.additional_fields` is the one you'll hit first. Pass the
natural object; `mcp call` reads the declared type and encodes it:

```bash
orchestrator mcp call atlassian:jira_create_issue --args '{"project_key":"SSPN","additional_fields":{"labels":["dogfood"]}}'
```

Without this you'd have to embed a JSON document inside a JSON document. If you
already encode it yourself, that still works — both spellings are accepted.

The coercion is deliberately narrow: it applies only where the schema says
`string` (or `string|null`) and you passed an object or array. An argument the
server declares as `object`, one it doesn't declare at all, or a tool whose
schema can't be read is sent exactly as you wrote it — Spine won't guess at a
type the server never stated.

Spine can use external **[MCP](https://modelcontextprotocol.io)
servers** — read Confluence/Jira through Atlassian's MCP server, introspect a
database, and more — reusing the same `mcpServers` config you already use with
Claude or Codex.

**9.1 — Install + point at a config:**
Install `[mcp]` using the [SETUP extras](SETUP.md#optional-extras).
> **Install the extra first, or the failure is silent.** Without it, `mcp list` prints an
> empty tool list rather than an error — a missing dependency currently looks identical to an
> unreachable server. If you see zero tools, check this before debugging the server.

**Two files, one character apart, opposite directions.** `mcp.json` (no leading dot) is what
*Spine reads* to find servers it may call. A tracked `.mcp.json` is the reverse — how Claude
Code launches Spine *as* a server. Editing the wrong one is the most common setup mistake.

Supply the `mcpServers` JSON via `--config`, `$ORCHESTRATOR_MCP_CONFIG`, or `./mcp.json`.
Transport is inferred (`command` → stdio, `url` → HTTP).

**9.1a — Atlassian via Docker (recommended: no tokens in the config file).**
`mcp.json` is *not* gitignored by default and is exactly the file people paste tokens into.
Pass credentials with `--env-file` instead, so the config carries nothing secret and stays
safe to commit or share:
```json
{
  "mcpServers": {
    "atlassian": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--env-file", "/abs/path/to/.env",
               "ghcr.io/sooperset/mcp-atlassian:latest"],
      "allow": ["jira_get_issue", "jira_search", "jira_download_attachments",
                "confluence_get_page", "confluence_get_page_children"]
    }
  }
}
```
`mcp-atlassian` expects **different variable names** than Spine's own `CONFLUENCE_*`/`JIRA_*`.
The token names already match; add four aliases to your `.env` (same values you already have):
```
JIRA_URL=https://your-org.atlassian.net            # Spine calls this JIRA_BASE_URL
JIRA_USERNAME=you@org.com                          # Spine calls this JIRA_EMAIL
CONFLUENCE_URL=https://your-org.atlassian.net/wiki # note the /wiki suffix
CONFLUENCE_USERNAME=you@org.com
```
Use an **absolute** path for `--env-file`: the server is launched as a subprocess whose working
directory you do not control. And note Docker's `--env-file` does **not** strip inline comments —
`FOO=bar  # note` yields the value `bar  # note`. Keep comments on their own lines.

**9.1b — Atlassian via `uvx`** (no Docker; installs into your environment):
```json
{
  "mcpServers": {
    "atlassian": {
      "command": "uvx",
      "args": ["mcp-atlassian"],
      "env": { "JIRA_URL": "https://your-org.atlassian.net", "JIRA_USERNAME": "you@org.com" },
      "allow": ["jira_get_issue", "jira_search", "jira_download_attachments", "confluence_get_page"]
    }
  }
}
```
The inline `env` block is convenient but puts values **in the file** — fine for non-secrets,
wrong for tokens. Prefer `--env-file` (9.1a) whenever credentials are involved.

**9.1c — Several servers at once.** `mcpServers` is a map; add as many as you need. Each is
launched independently, and **one unreachable server does not blank the others** — the rest
still list their tools:
```json
{
  "mcpServers": {
    "atlassian": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--env-file", "/abs/path/to/.env",
               "ghcr.io/sooperset/mcp-atlassian:latest"],
      "allow": ["jira_get_issue", "jira_search", "jira_download_attachments", "confluence_get_page"]
    },
    "postgres": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "DATABASE_URI",
               "mcp/postgres:latest"],
      "allow": ["query", "list_schemas"]
    },
    "internal": {
      "url": "https://mcp.your-org.internal/sse",
      "headers": { "Authorization": "Bearer ${INTERNAL_MCP_TOKEN}" },
      "write_enabled": false
    }
  }
}
```
Tool names are namespaced `server:tool`, so two servers may expose the same tool name without
colliding. Point the Atlassian presets at a specific server with `MCP_JIRA_SERVER` /
`MCP_CONFLUENCE_SERVER` when you run more than one that could serve them.

- **`allow`** is an allow-list — only those tools are callable (omit = all, with a warning).
- **Writes are off by default**: mutating tools are refused unless you set
  `write_enabled: true` on that server.
- **Servers run on your machine.** A stdio server's `command` is executed locally; Docker at
  least confines it. Review what you onboard.

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
orchestrator sdlc feature --source mcp-jira://<issue-key> --safe   # Jira issue + its children
# Feed a database's real schema into codegen's grounding:
orchestrator mcp ingest-db --server <db-server-name>
```
`mcp-confluence` and `mcp-jira` are presets (one `mcp-atlassian` server usually serves both —
point them at it with `MCP_CONFLUENCE_SERVER` / `MCP_JIRA_SERVER`). For **any other** MCP
server, use the generic `mcp://<root>` and name its tools via `MCP_SOURCE_SERVER` /
`MCP_SOURCE_DOC_TOOL` / `MCP_SOURCE_CHILDREN_TOOL` (results are parsed leniently, falling back
to raw text). This routes source access through a governed MCP server instead of spreading
`CONFLUENCE_*` / `JIRA_*` tokens into the env.

A Jira issue read over `mcp-jira` reads **the same as over REST**: comments, issue links and
attachment text, rendered by the same code. Attachment bytes come from mcp-atlassian's
`jira_download_attachments` (override the name with `MCP_JIRA_ATTACHMENTS_TOOL`); leave it off
the `allow` list and each attachment is named with why instead of read. A server that rejects
the request for those fields is read with its defaults, and the ticket says so — the description
only. With `--follow-links`, the pages it links to are read through `confluence_get_page`.
In the pipeline (Step 7), configured MCP tools are auto-onboarded at startup with
the same rate-limit + audit + approval path.

**9.4 — Manage MCP servers from the web UI.** The **Connections** page
(`/app/connections`) lists every configured server and **tests each live**
(reachable? which allow-listed tools?), alongside your source/tracker status. Use
**Browse…** to pick an `mcp.json` anywhere on the machine (a server-side file
picker — the config lives on the server, so a normal upload can't select it). To
add / edit / remove servers from the page (it writes `mcp.json`), start with
`ORCHESTRATOR_MCP_CONFIG_WRITABLE=1` — off by default because a stdio server's
`command` runs on this machine. When it's off, the page shows the config path so
you can edit the file directly.

---

## Step 10 — Call it from Claude, Codex, or your IDE (MCP server)

The reverse of Step 9: Spine can **become** an MCP server, so any host
— Claude Code, the Codex app, Claude Desktop, claude.ai — can call your
"intent → reviewed PR" pipeline as tools, with the **same human gates**.

Install `[all]` using the [SETUP extras](SETUP.md#optional-extras).
> `[mcp]` alone serves the tools but a **Python-only** graph — a Java or Go repo yields zero
> nodes rather than an error. `[all]` is `[languages]` + `[mcp]` + doc ingestion, which is what
> the comprehension tools are worth installing for.

**10.1 — Local (the host launches it over stdio):**

Follow [AGENT_GUIDE → Install](AGENT_GUIDE.md#3-install) for plugin registration,
or [its configuration examples](AGENT_GUIDE.md#4-credentials) for a raw server.
The [generated tool inventory](AGENT_GUIDE.md#6-the-tools-spine-exposes) lists every
tool, scope and output type. Host-specific setup and tool descriptions live there.

**10.2 — Remote (hosted clients connect to a URL):**
```bash
orchestrator-mcp --http --host 0.0.0.0 --port 8080     # behind TLS in production
```
Pick one auth mode (a public bind without either is refused):
- **Shared secret** — set `ORCHESTRATOR_MCP_TOKEN` + `ORCHESTRATOR_MCP_RESOURCE_URL`; the client sends that token as its bearer.
- **OAuth introspection** — set `ORCHESTRATOR_MCP_ISSUER_URL`, `…_INTROSPECTION_URL` and client id/secret to validate every token against your IdP.

Scopes follow the tool tiers and are checked **per call**: `spine:read` (comprehension, observing
a run), `spine:plan` (`sdlc_plan`, `sdlc_approve`), `spine:run` (anything that spends money or
writes where it cannot be taken back). An IdP-issued token needs the scope of the tier it calls;
a shared-secret token carries all three unless `ORCHESTRATOR_MCP_REQUIRED_SCOPES` narrows it —
`spine:read` makes it read-only. (The pre-3.31 `sdlc` scope is retired; grant the three scopes.)
Every run-scope call and every scope denial over HTTP is recorded against the token's principal
in the registry's audit log (`POST /v1/audit`; read it back with `resource_type=mcp_tool`).

Destructive tools stay gated regardless of auth: `sdlc_feature(live=true)` and
`sdlc_start_run(create_jira=true)` both need an explicit `confirm=true`.

---

## Operating advanced capabilities

**Committed `understand` episteme:**
```bash
orchestrator understand --out episteme            # commit-cached PKG
orchestrator understand --out episteme --refresh  # force re-extraction
orchestrator understand . --check                 # CI: non-zero exit if episteme is stale
```
Commit `episteme/` so the team (and any AI tool) shares grounded context.

**Wire `--check` into CI** to make the bank provably current rather than hopefully current. It
writes nothing. Two operational notes: it reads docs **from disk regardless of git**, so an
untracked or gitignored Markdown file makes it report stale over a diff CI cannot reproduce; and
the commit-keyed cache is only trusted on a **clean tree**, so a dirty checkout re-extracts the
whole repo every run.

**Graph accuracy in the gate:**
```bash
orchestrator pkg verify .                  # self-consistency: dangling edges, provenance
orchestrator pkg accuracy --check          # gated regression against the committed baseline
orchestrator pkg accuracy . --oracle parity     # declared routes/tables vs the graph
orchestrator pkg accuracy . --oracle invention  # calls to names that don't exist (Python only)
```
Corpus precision/recall is gated **strict**; parity shortfall is a **ratchet**; invention and
runtime recall are recorded as trends and never gated, because they move with ordinary commits.

**Agentic codegen loop** (off by default):
```bash
export SDLC_AGENTIC_CODEGEN=1
orchestrator catalog plan      # inspect what would be assembled for this repo
```

**Cross-run semantic memory:** `export ORCHESTRATOR_SEMANTIC_MEMORY=1` — lessons
persist and ground later runs.

**Live tracing:** point `OTEL_EXPORTER_OTLP_ENDPOINT` at an OTLP collector (e.g.
Jaeger at `http://localhost:4318`) and run anything.

**Local / offline models:** no API key needed — point codegen at Ollama or any
OpenAI-compatible endpoint (see [USER_GUIDE.md](USER_GUIDE.md)).

---

## The semantic spine
A shared **`EntityKey`** (`Component_vX::Region::Interface`) joins a domain concept →
code symbol → deployment unit → drift signal, so a production drift becomes a
grounded, governed, provenance-carrying code fix. Three seams; each is independently
useful and **inert unless its variables are set**.

Variables and defaults: [SETUP → Semantic-spine configuration](SETUP.md#semantic-spine-configuration).

> **The two systems differ.** **ontomesh is a service** (a Flask app you run, then
> point a URL at). **infodrift (`drift_monitor`) is a library** — no HTTP server. So
> Seam 3 needs no running infodrift service (just a report file it produces), and
> Seam 2's `SPINE_INFODRIFT_URL` has nothing to point at unless you wrap the library
> in a small shim.

**Prerequisite — the code↔ontology mapping.** Seams 2 and 3 scope work to code via a
**human-confirmed** mapping store (`spine-mappings.json`); without it, drift can't be
scoped and provenance is fiction. Build it once per repo+ontology.

### Seam 1 — ontomesh domain grounding (read-only, safe to turn on first)
1. Start ontomesh (external Flask service), enable its search, model an ontology:
   ```bash
   docker run -d --name ontomesh -p 5051:5051 ghcr.io/synaptixs/ontomesh:latest
   ```
2. Configure (URL is the *base* — the client appends `/api/search`):
   ```bash
   export SPINE_ONTOMESH_URL=http://localhost:5051
   export SPINE_ONTOMESH_FLAVOR=fraud           # must match a flavor in your ontomesh
   export SPINE_ONTOMESH_MIN_CONFIDENCE=0.4      # optional; default 0.0
   ```
3. Verify:
   ```bash
   uv run python -c "from orchestrator.spine import ontomesh_grounder_from_env; print(ontomesh_grounder_from_env() is not None)"
   ```
   Once set, grounding composes into codegen automatically. Any outage or
   low-confidence answer degrades to code-only — it never breaks a build.

### Seam 3 — drift → governed remediation (the headline; needs no infodrift service)
1. Produce a drift report by running the `drift_monitor` library offline
   (`register_entity` → score a shifted window → `HealthReporter.full_report(as_json=True)`),
   or hand-write the JSON for a first pass.
2. Run the governed remediation:
   ```bash
   orchestrator sdlc remediate --report drift.json --mappings spine-mappings.json \
     --repo /path/to/repo --min-severity warning --safe   # --live opens PRs
   ```
   It scopes each fix to the code mapped to the drifting `entity_key`, uses ontomesh
   constraints as guardrails (when Seam 1 is on), and carries full provenance. Keep
   `--safe` until mapping precision is measured on your real ontology.

### Seam 2 — register shipped units (needs an HTTP receiver infodrift doesn't ship)
- **Recommended to start: leave it off** — the post-merge `register_units` activity
  no-ops cleanly; Seams 1 and 3 are unaffected.
- **To enable:** stand up a thin shim wrapping `DriftOrchestrator.register_entity`
  behind one HTTP route, then set:
  ```bash
  export SPINE_INFODRIFT_URL=http://localhost:8080
  export SPINE_DEPLOY_TOPOLOGY='{"FraudDetector":[["APAC","CardTransactions"]]}'
  ```
  Match the shim's route/body to what `InfodriftHttpClient` posts
  (`src/orchestrator/spine/shipment.py`).

### Honest operational gaps
- Mapping precision is unproven on a real domain — keep humans in the confirm loop; run Seam 3 `--safe`.
- Deploy topology is env-declared, not sourced from real deploy config (Seam 2).
- infodrift needs a shim for Seam 2; there's no out-of-box server.

---

## Turn-on order
1. Seam 1 (ontomesh) — read-only, immediate value, zero risk.
2. Build + human-confirm `spine-mappings.json`.
3. Seam 3 — `sdlc remediate --safe` on a real drift report; review the diff.
4. Seam 2 — only if you want auto-registration; build the shim first.
5. Graduate Seam 3 to `--live` once precision is measured.

**Before a live (`--live`) run:** GitHub App auth set, budget cap set, local quality
gate green, approvals reachable.
