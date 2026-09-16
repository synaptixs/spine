# Setup — install, configure, and troubleshoot Spine

This is the authoritative installation, credentials, environment and troubleshooting
reference for both the published tool and a source checkout. Start with the
[worked example](EXAMPLE.md), follow [USER_GUIDE.md](USER_GUIDE.md) for everyday
builds, or use [OPERATIONS.md](OPERATIONS.md) to run the service for a team.

> **Spine** is the product; it installs as the **`synaptixs-spine`** package and its command
> is **`orchestrator`** — used verbatim below. On a source checkout, prefix CLI calls with
> `uv run`.

---

## 1. Prerequisites

| Tool | Min version | Needed for |
|---|---|---|
| **Python** | 3.12 | everything |
| **uv** | 0.4+ | the venv and the gate (`pip install uv` or `brew install uv`) |
| **Docker** + `docker compose` | recent | only the full stack in §5 — comprehension, the gate and the intake dry run need no Docker |
| an LLM key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`) | — | only the intake pipeline and the real-model tests; `understand` / `state` / `pkg` never call a model |

```bash
python3 --version && uv --version && docker compose version
```

---

## Install the published tool

Use Python 3.12+ and [uv](https://docs.astral.sh/uv/). For comprehension only:

```bash
uv tool install synaptixs-spine
orchestrator --help
```

For building features, add `[sdlc]`; for the agent plugin and every language:

```bash
uv tool install --force 'synaptixs-spine[all]'
```

The `--force` form replaces an existing tool installation with the selected extras.
In an existing virtual environment, `pip install 'synaptixs-spine[all]'` is the
equivalent. Both install the `orchestrator` and `orchestrator-mcp` console scripts.
Host registration and configuration paths are in [AGENT_GUIDE.md](AGENT_GUIDE.md#3-install).

### Optional extras

Optional extras, added when you need them:
- `[sdlc]` — run the generated tests (the `sdlc feature`/`run` path)
- `[all]` — the language, MCP, SDLC and document extras together: every language front-end,
  the MCP server and doc ingestion. This is the right install for the Claude Code / Codex
  plugin; `[languages]` is the front-ends on their own. `[all]` also includes `[clang]`.
- `[clang]` — optional C/C++ member-call resolution using wheel-bundled libclang.
  Adds edges between existing grounded symbols; no system LLVM or Xcode is needed.
  The native library is approximately 72 MB on macOS. Standard-library types are
  not resolved; missing headers reduce coverage. Included in `[all]`, not `[languages]`.
  Install the C/C++ grammars alongside it, for example
  `pip install 'synaptixs-spine[c,cpp,clang]'`. No compilation database or host SDK
  is consulted. Literal includes can supply additional unambiguous repository roots.
  Inspect the `resolved N of M ... in K of T TUs` summary before relying on coverage;
  benefit and cost vary by repository. See the
  [five-repository evaluation](docs/evals/clang-semantic-step3b.md).
- `[java]`, `[typescript]`, `[csharp]`, `[c]`, `[cpp]`, `[go]`, `[php]`, `[perl]`, `[sql]` — language
  parsers for comprehension + grounding (Python needs no extra). C# codegen also needs the **.NET
  SDK** (`dotnet`) on PATH; C / C++ codegen needs a C / C++ compiler plus **CMake** (greenfield) or
  **Meson + Ninja** (matching the target repo's build system); **Go** codegen needs the **`go`
  toolchain** on PATH (`go build`/`go test`). `[sql]` adds `.sql`
  comprehension (schema/queries/procedures + migration folding) — no toolchain needed. `[php]`
  adds `.php` comprehension + a call graph (namespaces, classes/interfaces/traits, `CALLS`,
  typed-receiver resolution) + Laravel/Slim/Symfony routes + Eloquent/Doctrine entities —
  codegen uses Composer or a pinned PHPUnit PHAR. `[perl]` adds `.pl`/`.pm`/`.t` comprehension
  (every package is its own type, inheritance across its five spellings)
  and codegen: install `perl` and `prove` on PATH; `cpanm` is optional for dependencies.
- `[docs]` — **PDF** doc ingestion; `[office]` — **Word/Excel** (`.docx`/`.xlsx`) ingestion.
  Markdown, `.rst`, `.txt` and **HTML** need no extra. Without an extra those files are simply
  skipped, so a base install still ingests everything it can read.
- `[media]` — **image OCR** (needs a system `tesseract` binary); `[asr]` — **local audio/video
  transcription** (Whisper). Only the opt-in `orchestrator media extract` uses these; the
  deterministic build never does. See [media ingestion](USER_GUIDE.md#bringing-diagrams--recordings-into-the-graph).
- `[mcp]` (MCP client), `[otel]` (live tracing)

`[all]` excludes `[security]` (Semgrep), `[media]`, `[asr]`, `[sql-postgres]`,
`[otel]` and `[dev]`; those stay opt-in. `[sql-postgres]` uses Docker for real
Postgres validation instead of the default in-memory SQLite.

### Updating and uninstalling

For a uv tool installation, use `uv tool upgrade synaptixs-spine` or
`uv tool uninstall synaptixs-spine`. For pip, use `pip install --upgrade synaptixs-spine` with your original extras, or `pip uninstall synaptixs-spine`.
In a source checkout, pull the desired branch and repeat the sync command below.
Verify with `orchestrator --version` and `orchestrator doctor`, which identifies
the interpreter answering. Host plugin update/removal commands stay in
[the agent guide](AGENT_GUIDE.md#3-install).

---

## 2. Install from source

```bash
git clone https://github.com/synaptixs/spine
cd spine
uv sync --frozen --extra dev --extra mcp --extra typescript --extra java --extra csharp \
  --extra c --extra cpp --extra go --extra php --extra perl
uv run orchestrator --help
```

This is the extras set CI syncs. `[dev]` supplies testing/type tools plus SQL and
document parsers; the explicit language extras and `[mcp]` exercise the remaining
front-ends and plugin. Fewer extras mean fewer languages, not zero findings.
On a checkout, prefix commands in other guides with `uv run --frozen`.

---

## 3. The gate

Run it before every push. The commands are the single source in
[CONTRIBUTING.md → Opening a pull request](CONTRIBUTING.md#opening-a-pull-request) — note
`mypy src tests`, **not** just `src`, and the five generated-artifact `--check` scripts CI also runs. In short:

```bash
uv run pytest                    # unit tests only; no Docker, no key
uv run mypy src tests
uv run ruff format --check .
```

Two opt-in markers need more than the checkout: `-m integration` needs Postgres up (§5), and
`-m real_llm` needs a provider key. The default run excludes both.

---

## 4. See it work — no account, no key

The deterministic comprehension surface runs on this repository itself:

```bash
uv run orchestrator state .            # a current-state report, developer lens
uv run orchestrator understand .       # writes episteme/ — the committed knowledge base
uv run orchestrator understand . --check   # …and verifies it still matches the code
```

`episteme/` is regenerated by CI after every merge and is never committed from a branch (see
[CLAUDE.md](CLAUDE.md)); generate it locally to read it. [USER_GUIDE.md → Step 1.5](USER_GUIDE.md#step-15--see-what-it-knows-about-your-repo-no-configuration-yet)
walks through what you are looking at.

The intake pipeline is the first thing that needs a model. The `file://` source reads
requirements off disk — no Confluence, Notion or Jira account:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run orchestrator ingest --source file://./examples/intake/sample-spec.md   # dry run; writes nothing
```

`orchestrator doctor` says which of the optional credentials are set.

---

## 5. The full stack

Everything under the SDLC pipeline and the web dashboard — Temporal, Postgres, the worker,
the API — comes up with one command from a checkout with Docker running:

```bash
uv run orchestrator up            # infra + migrations + API + worker, then prints the URL and key
```

It opens `http://localhost:8000/app`; Ctrl-C stops the app processes. What it started:

| Service | Port | Purpose |
|---|---|---|
| `orchestrator-postgres` | 5433 | application DB |
| `orchestrator-minio` | 9000 / 9001 | S3-compatible artifact store (console on :9001, `minio_admin` / `minio_admin_password`) |
| `orchestrator-temporal` | 7233 | workflow engine |
| `orchestrator-temporal-ui` | 8233 | workflow inspection |
| `orchestrator-jaeger` | 16686 / 4317 / 4318 | live tracing — UI on :16686, OTLP receivers on :4317 (gRPC) / :4318 (HTTP) |

The manual equivalents, for a `--reload` loop or a single process:

```bash
docker compose -f docker-compose.dev.yml up -d && uv run alembic upgrade head
uv run uvicorn orchestrator.registry.api.app:create_app --factory --reload --port 8000
uv run python -m orchestrator.sdlc.worker
```

What to do with a running stack — delegate a run, approve a gate, watch it — is
[OPERATIONS.md → Step 7](OPERATIONS.md#step-7--the-full-pipeline--web-dashboard); every command
is in [CLI_REFERENCE.md](CLI_REFERENCE.md).

---

## 6. Live tracing (optional)

Off by default — nothing is emitted until you point the app at a collector. Jaeger (above)
bundles its own OTLP receiver, so it doubles as one:

```bash
uv sync --extra otel
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
uv run orchestrator up
```

Open **http://localhost:16686**, pick the `synaptixs-spine` service: one trace per run,
`execute_graph_pass → agent.step → llm.complete / tool.<name>`, with the app `trace_id` on every
span so it joins the audit log. Design record: `docs/specs/live-observability-otel.md`.

---

## Credentials and model selection

```bash
orchestrator init      # scaffolds a commented .env, then checks readiness
# open .env and fill in: your LLM key, your model, and (later) Confluence/Jira + repo
orchestrator doctor    # readiness report — tells you exactly what's set and what's missing
```

`doctor` reads `.env` automatically — run it from the folder that has your
`.env`. Start minimal; you only need the LLM settings for your first run.

| Setting | What it's for | Needed by |
|---|---|---|
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Your LLM provider | Local build |
| `ORCHESTRATOR_MODEL` | One model for every stage (optional — defaults to `claude-opus-5`) | Local build |
| `CONFLUENCE_*`, `JIRA_*` | Read requirements / file issues | Live source/PR work |
| `SDLC_REPO_URL` | The repo it builds **into** | Live source/PR work |
| `SDLC_PR_BASE` | The branch a run builds **on** and opens its PR into — set this to `develop` if that is where you merge, or runs are written against `main` | Live source/PR work |
| `GITHUB_TOKEN` *(or `GITHUB_APP_*`)* | Auth for a private target repo | Live source/PR work |

### Choosing a model

Every stage runs on `claude-opus-5` unless you say otherwise. To see what you can
point it at — with context windows, prices, and which models support the tool
calling the pipeline requires:

```bash
orchestrator models                    # everything, plus what each stage uses now
orchestrator models --provider openai  # just one vendor
```

Set one knob for everything, or a model per stage:

| Variable | Drives |
|---|---|
| `ORCHESTRATOR_MODEL` | every stage |
| `SDLC_CODEGEN_MODEL` | codegen — implement, refine, revise, author_tests |
| `SDLC_JUDGE_MODEL` | the acceptance judge |
| `ORCHESTRATOR_INTAKE_MODEL` | intent extraction and spec writing |
| `ORCHESTRATOR_REASONING_EFFORT` | reasoning level on tool-calling models (default `high`) |

A stage's own variable wins over `ORCHESTRATOR_MODEL`, which wins over the default.
Pointing a stage at another vendor needs that vendor's key in the environment.

> **Tool calling is required, not preferred.** Codegen and the judge both force a
> tool call. On a model without it they fall back to reading prose out of a text
> reply, which is far less reliable — `orchestrator models` marks those.

For sources, add `CONFLUENCE_*`, `JIRA_*` or `NOTION_API_TOKEN` as needed;
`file://` needs no source credentials. Live Jira issue creation needs
`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` and `JIRA_PROJECT_KEY`.
Live PRs accept `GITHUB_TOKEN` / `GH_TOKEN`, or the GitHub App configuration.
Match the provider key to the selected model. For a local endpoint, see
[offline models](USER_GUIDE.md#step-6--run-fully-offline-on-a-local-model-no-api-key).

The MCP server reads `.env` too. If the host starts it from another directory,
set `ORCHESTRATOR_DOTENV` to the file's **absolute** path in the server environment.
Read-only comprehension and deterministic planning need no provider credentials.
See the [host configuration examples](AGENT_GUIDE.md#4-credentials).

---

### Local and mixed model configuration

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
SDLC_CODEGEN_MODEL=claude-opus-5                  # codegen, cloud quality
SDLC_REVIEW_MODEL=ollama/qwen2.5-coder            # the review judge
```


---

## 7. Environment

`orchestrator init` scaffolds a `.env` from the same groups `doctor` checks. The three a
developer sets on day one:

| Variable | Default | What it is |
|---|---|---|
| `ORCHESTRATOR_API_KEY` | `dev-key` | auth for `/v1/*` (the `X-API-Key` header) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | the model provider; LiteLLM routes on whichever is set |
| `ORCHESTRATOR_DATABASE_URL` | the compose Postgres on 5433 | the application DB |

The full variable names and defaults are in [`.env.example`](.env.example).
Only set the groups your workflow uses; optional capabilities remain off until enabled.

**Core / LLM** — `ORCHESTRATOR_INTAKE_MODEL`, `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`,
`SDLC_CODEGEN`, `SDLC_CODEGEN_MODEL` / `SDLC_REVIEW_MODEL`.

**Pipeline & governance** — `SDLC_REPO_URL`, `SDLC_RUN_BUDGET_USD` (hard spend cap),
`SPINE_SDLC_IMPERATIVE` (fall back to the pre-3.20 path),
`SDLC_AGENTIC_CODEGEN` (ReAct loop, default off), `SDLC_AGENTIC_POLICY`,
`SDLC_TEST_ISOLATION`, `SDLC_GITHUB_INSTALLATION_ID` (live PR auth).

**Service / storage / identity** — `ORCHESTRATOR_DATABASE_URL`,
`ORCHESTRATOR_ARTIFACT_STORE`, `ORCHESTRATOR_SESSION_SECRET`, `ORCHESTRATOR_API_URL`
/ `ORCHESTRATOR_API_KEY`, `ORCHESTRATOR_PRINCIPALS` / `ORCHESTRATOR_TENANT_ID` (RBAC, partial).

**Memory & observability** — `ORCHESTRATOR_SEMANTIC_MEMORY`, `ORCHESTRATOR_MEMORY_BANK_DIR`,
`OTEL_EXPORTER_OTLP_ENDPOINT`.

**MCP** — `ORCHESTRATOR_MCP_CONFIG` (servers Spine consumes), `ORCHESTRATOR_MCP_HOST`
/ `_PORT` / `_PATH` (Spine-as-server), `ORCHESTRATOR_MCP_ISSUER_URL` / `_INTROSPECTION_*`
(remote OAuth).

**Semantic spine** — see [semantic-spine configuration](#semantic-spine-configuration).

### Pipeline stages and gates

| Env var | Default | Effect |
|---|---|---|
| `SDLC_COMPREHEND` | on | Comprehend the repo before the intents gate. |
| `SDLC_DESIGN` | on | Produce a grounded design per issue before codegen. |
| `SDLC_DESIGN_GATE` | **off** | Add a human **“approve designs”** gate (Gate 1.5, id `sdlc-<id>-2`) after the design wave, before any code is written. |

### Repository and MCP access

| Env var | Default | Effect |
|---|---|---|
| `ORCHESTRATOR_WORKSPACE_ROOT` | the cwd `up` ran in | Local repo paths must resolve under this root. |
| `ORCHESTRATOR_REPO_ALLOWED_HOSTS` | `github.com,bitbucket.org,gitlab.com` | Hosts a repo URL may be cloned from. Add an enterprise/custom host, or `*` for any. `file://` / `http://` / localhost / private IPs are always blocked. |
| `ORCHESTRATOR_REPO_ALLOW_ANY_LOCAL` | off | Allow any absolute **local** repo path (trusted single-user). |
| `ORCHESTRATOR_MCP_CONFIG_WRITABLE` | off | Allow adding/editing MCP servers from the Connections page (writes `mcp.json`; a stdio server's `command` is executed on this machine — off by default). |

### Semantic-spine configuration

| Variable | Purpose | Default |
|---|---|---|
| `SPINE_ONTOMESH_URL` | ontomesh base URL (Seam 1) | unset → off |
| `SPINE_ONTOMESH_FLAVOR` | ontology/sensitivity flavor (Seam 1) | unset → off |
| `SPINE_ONTOMESH_MIN_CONFIDENCE` | drop answers below this confidence | `0.0` |
| `SPINE_INFODRIFT_URL` | infodrift register endpoint (Seam 2) | unset → off |
| `SPINE_DEPLOY_TOPOLOGY` | `{component: [[region, interface], …]}` (Seam 2) | unset → off |
| `SPINE_SHIP_VERSION` | version stamped on shipped units | `1` |

The deployment sequence and the library-versus-service distinction are in
[Operations](OPERATIONS.md#the-semantic-spine).

---

## 8. Migrations

Every schema change ships as an Alembic revision under `migrations/versions/`:

```bash
uv run alembic upgrade head                            # apply
uv run alembic downgrade -1                            # roll back one
uv run alembic revision --autogenerate -m "your change"   # after editing models — then edit the file; autogenerate is a draft
```

---

## 9. Where things live

[ARCHITECTURE.md](ARCHITECTURE.md) has the package table and the diagram; [CLAUDE.md](CLAUDE.md)
has the layout by responsibility plus the invariants that are easy to break; the design
records — the *why* — are indexed at [docs/specs/README.md](docs/specs/README.md).

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor` shows everything missing | Run it from the folder that has your `.env`. |
| Codegen times out | Set `ORCHESTRATOR_MODEL` to a faster model (see `orchestrator models`). |
| Private repo clone fails | Set `GITHUB_TOKEN` (PAT) or the GitHub App (`GITHUB_APP_*`). |
| Console asks for an API key | Paste the `ORCHESTRATOR_API_KEY` you started the server with (`dev-key` by default). |
| `sdlc run` hangs at a gate | Approve it in the console or via `/v1/approvals/.../approve`. |
| Worker does nothing | It reads the process env, not `.env` — `set -a; source .env; set +a` before starting it. |
| `mcp list` shows no servers | Add an `mcpServers` file (`--config`, `$ORCHESTRATOR_MCP_CONFIG`, or `./mcp.json`). |
| `mcp` commands fail to import | Install the extra: `pip install 'synaptixs-spine[mcp]'` (or `uv sync --extra mcp`). |
| An MCP tool is "not allow-listed" / write-gated | Add it to the server's `allow`; for mutating tools set `write_enabled: true`. |
| Agentic loop falls back to single-shot | Use a tool-calling model (see `orchestrator models`) and set `SDLC_CODEGEN=llm`. |
| `orchestrator-mcp --http` refuses to start | Set `ORCHESTRATOR_MCP_TOKEN` or `…_INTROSPECTION_URL`, bind `127.0.0.1`, or pass `--allow-unauthenticated` on a trusted net. |
| Remote client gets 401 | Send `Authorization: Bearer <token>`; for introspection confirm the token is active and carries the required scope. |
| `Nondeterminism error` on replay | An in-flight workflow predates a code change. Terminate the stale run (Temporal UI); new runs are unaffected. |
| The host does not see Spine's tools | Restart it and follow the checks in [Install](AGENT_GUIDE.md#3-install). |
| `doctor` says the LLM provider is missing | Your `.env` isn't being found — use the [server configuration](AGENT_GUIDE.md#4-credentials) and set `ORCHESTRATOR_DOTENV` to its **absolute** path. |
| `orchestrator-mcp: command not found` | The server isn't on PATH. `pip install 'synaptixs-spine[all]'`, or point `command` at the absolute path of the console script. |
| The server connects and dies ("Connection closed"), or tools are missing | A **stale** `orchestrator-mcp` on PATH — a console script left by an older checkout's venv. Ask your assistant to run `doctor` (or run `orchestrator doctor`): its `server` block names the **version, interpreter and MCP SDK** answering. If they aren't the install you expect: `uv tool install --force 'synaptixs-spine[all]'` (or reinstall into the venv you meant), then restart the host. |
| "live needs a repo to push to" | Pass `repo=...` or set `SDLC_REPO_URL`; ensure `GITHUB_TOKEN`/`GH_TOKEN` is set. |
| A `live` call refuses to write | That's the gate — pass `confirm=true` together with `live=true`. |
| Build fails for Java/TS/C#/C/C++/Go/PHP/Perl | The language toolchain isn't installed — see [§10](AGENT_GUIDE.md#10-language-support--toolchains). |

**`temporal-test-server` orphaned after a killed pytest** — `pkill -f temporal-test-server`.
The time-skipping test server does not clean up after `SIGKILL`.

**`EndpointConnectionError` in an integration test** — MinIO is not reachable. Start it
(`docker compose -f docker-compose.dev.yml up -d minio`) or set
`ORCHESTRATOR_ARTIFACT_STORE=memory` for tests that need no real artifact store.

**Migrations fail with "relation already exists"** — a prior run left tables behind. Reset
the dev DB: `docker compose -f docker-compose.dev.yml down -v` (deletes the volume), then
`up -d` and `uv run alembic upgrade head`.

**`mypy` errors after adding a dependency** — `uv sync --extra dev` to pick up the stubs, then
re-run. A failure in a file you did not touch usually means a missing extra; see
[CONTRIBUTING.md → When a check fails on something you didn't change](CONTRIBUTING.md#when-a-check-fails-on-something-you-didnt-change).

**`orchestrator` resolves to an older install** — `uv run orchestrator --version` and
`orchestrator doctor` both name the interpreter answering; a stale console script on PATH is
the usual cause.

---

## 11. Where to learn more

| Topic | File |
|---|---|
| What Spine is, and what is new | [README.md](README.md) |
| Using it, step by step | [USER_GUIDE.md](USER_GUIDE.md) |
| Every command and flag | [CLI_REFERENCE.md](CLI_REFERENCE.md) |
| How the pieces fit | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Running it for others | [OPERATIONS.md](OPERATIONS.md) |
| Contributing, review, the gate | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Using it from Claude Code or Codex | [AGENT_GUIDE.md](AGENT_GUIDE.md) |
| Design records | [docs/specs/README.md](docs/specs/README.md) |
| Security policy · license | [SECURITY.md](SECURITY.md) · `LICENSE` (MIT) |

Perl codegen requires `perl` and `prove` on PATH. `cpanm` is optional for installing
`cpanfile` dependencies; missing or failed installation is logged and tests still run.
A repository with `.perlcriticrc` also needs `Perl::Critic`; configured critic failures
stop preflight. XS builds are unsupported. See [Perl code generation](USER_GUIDE.md#perl-code-generation).

PHP codegen requires PHP on PATH (including XML and mbstring extensions), plus Composer
for repositories with `composer.json`. See [PHP code generation](USER_GUIDE.md#php-code-generation)
for version selection, PHPUnit installation, and legacy repository behavior.
