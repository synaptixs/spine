# SETUP — Spine

Zero-to-running guide. For what Spine is and its capabilities, see `README.md`;
for the day-to-day workflow, see `USER_GUIDE.md`.

> **Spine** is the product; it installs as the **`synaptixs-spine`** package and
> its command is **`orchestrator`** — used verbatim in the commands below.

---

## 1. Prerequisites

| Tool | Min version | Why |
|---|---|---|
| **Python** | 3.12 | Runtime |
| **uv** | 0.4+ | Package + venv manager (`pip install uv` or `brew install uv`) |
| **Docker** + `docker compose` | recent | Postgres, MinIO, and Temporal services for local dev |
| **(optional)** Anthropic / OpenAI API key | — | Real-LLM smoke test + integration tests; not needed for unit tests |

Verify:

```bash
python3 --version       # → 3.12.x
uv --version            # → 0.4+
docker --version        # → 24+ recommended
docker compose version
```

---

## 2. First-time install

```bash
# 1. Install Python dependencies (creates .venv, installs project + dev extras)
uv sync --extra dev

# 2. Bring up local infrastructure (Postgres, MinIO, Temporal)
docker compose -f docker-compose.dev.yml up -d

# Wait ~30 seconds on first run while Temporal's Postgres initialises.
# Check readiness:
docker compose -f docker-compose.dev.yml ps

# 3. Apply database migrations
uv run alembic upgrade head
```

> `--extra dev` already pulls in the parsers CI exercises (including `pypdf` for PDF doc
> ingestion), so `understand`/`state` handle PDFs out of the box here. End users add feature
> extras à la carte — e.g. `pip install 'synaptixs-spine[docs]'` for PDF ingestion, `[sql]`
> for SQL comprehension (see [USER_GUIDE.md](USER_GUIDE.md#step-1--install)).

What just came up:

| Service | Port | Purpose |
|---|---|---|
| `orchestrator-postgres` | 5433 | Main application DB |
| `orchestrator-minio` | 9000 / 9001 | S3-compatible artifact store (console on :9001) |
| `orchestrator-temporal` | 7233 | Workflow engine (Sprint 13+) |
| `orchestrator-temporal-ui` | 8233 | Web UI for workflow inspection |
| `orchestrator-temporal-postgres` | — | Dedicated DB for Temporal |
| `orchestrator-jaeger` | 16686 / 4317 / 4318 | Live OTel tracing — UI on :16686, OTLP receivers on :4317 (gRPC) / :4318 (HTTP) |

MinIO console login: `minio_admin` / `minio_admin_password`.

### Live tracing (optional)

Tracing is **off by default** — nothing is emitted unless you point the app at a
collector. Jaeger (above) bundles its own OTLP receiver, so it doubles as the collector.
Install the extra and export the endpoint, then run the API/worker as usual:

```bash
uv sync --extra otel              # OTLP/HTTP exporter
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
uv run python -m orchestrator.temporal.worker   # (and/or the API)
```

Open **http://localhost:16686**, pick the `synaptixs-spine` service, and you'll see one
trace per run: `execute_graph_pass → agent.step → llm.complete / tool.<name>`, with the app
`trace_id` on every span so it joins the audit log. See `docs/specs/live-observability-otel.md`.

---

## 3. Running tests

The default `uv run pytest` runs unit tests only (no docker required):

```bash
uv run pytest               # ~296 unit tests, <5 seconds
uv run ruff check .         # lint
uv run ruff format --check . # format
uv run mypy                 # type check (--strict)
```

Integration tests need Postgres up (step 2):

```bash
uv run pytest -m integration             # ~33 tests
```

Two integration tests are intentionally skipped until CI provisions
Temporal — manual run commands documented in each file's `skipif` reason:

- `tests/temporal/test_worker_restart.py` (Sprint 13.6)
- `tests/integration/test_approvals_e2e.py` (Sprint 14.10)

Real-LLM tests need a provider key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run pytest -m real_llm
```

---

## 4. Try the intake pipeline (no SaaS accounts)

The fastest way to see the backlog pipeline work. The `file://` source reads
requirements straight off the local filesystem — no Confluence, Notion, or
Jira account required. Point it at the bundled sample (or any markdown
file/directory of your own):

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # the intent/spec stages still call an LLM

# Dry-run: source → intents → gaps → specs → would-be Jira issues (writes nothing)
uv run orchestrator ingest --source file://./examples/intake/sample-spec.md
```

Other source kinds are drop-in (`confluence://<page_id>`, `notion://<page_id>`)
once their credentials are set in `.env` — run `orchestrator doctor` to check.
`file://` accepts a single file or a directory (walked breadth-first); set
`FILE_SOURCE_ROOT` to confine reads to a sandbox base dir.

---

## 5. Running the dev API

> **One command:** from a source checkout with Docker running, `orchestrator up`
> brings up the infra (Postgres + Temporal), applies migrations, and launches the
> web/API server **and** the SDLC worker together — then prints the URL
> (`http://localhost:8000/app`) and login key. Ctrl-C stops the app processes. The
> steps below are the manual equivalent (useful for `--reload` dev loops or running
> a single process).

```bash
# In one terminal: the registry + task API
export ORCHESTRATOR_API_KEY=dev-key
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY
uv run uvicorn orchestrator.registry.api.app:create_app --factory --reload --port 8000
```

Health check:

```bash
curl -s -H "X-API-Key: dev-key" http://localhost:8000/v1/agent-templates | jq
```

End-to-end smoke test (publishes a template, submits a task, exercises
the planner + runtime):

```bash
ANTHROPIC_API_KEY=sk-ant-... ./scripts/smoke-test.sh
```

---

## 6. Running the Temporal worker (Sprint 13+)

The synchronous `/v1/tasks` path works without Temporal. To use
`execution_mode=temporal` (or to fire approval gates), run the worker:

```bash
# Requires docker compose services from step 2 to be running.
uv run python -m orchestrator.temporal.worker
```

The worker logs `temporal.worker.start` and subscribes to the
`orchestrator-tasks` task queue. SIGINT/SIGTERM drains cleanly.

Submit a workflow-mode task:

```bash
curl -X POST http://localhost:8000/v1/tasks \
  -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  -d '{
    "objective": "Summarise the Big Bang",
    "execution_mode": "temporal",
    "glossary": {"topic": "cosmology"}
  }'
```

Open the Temporal Web UI at <http://localhost:8233> to inspect runs.

---

## 7. Environment variables

| Variable | Default | Where it matters |
|---|---|---|
| `ORCHESTRATOR_API_KEY` | `dev-key` | Auth for `/v1/*` endpoints (set via `X-API-Key` header) |
| `ORCHESTRATOR_DATABASE_URL` | `postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator` | Main DB connection |
| `ORCHESTRATOR_EXECUTION_MODE` | `sync` | Deployment-wide default for `/v1/tasks` (`sync` or `temporal`) |
| `ORCHESTRATOR_ARTIFACT_STORE` | (unset → MinIO) | Set to `memory` to use in-memory artifact store (tests) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | — | LLM provider creds (LiteLLM auto-routes) |
| `FILE_SOURCE_ROOT` | (unset → CWD) | Optional sandbox base dir for the `file://` intake source; reads are confined to it when set |
| `TEMPORAL_HOST` | `localhost:7233` | Temporal frontend; cloud namespaces use `<ns>.tmprl.cloud:7233` |
| `TEMPORAL_NAMESPACE` | `default` | Temporal namespace |
| `TEMPORAL_TASK_QUEUE` | `orchestrator-tasks` | Worker's task queue subscription |
| `TEMPORAL_API_KEY` | — | Set → cloud mode with TLS; unset → local plaintext |
| `LANGSMITH_PROJECT` | — | Set → `/trace/{task_id}` HTML gains a LangSmith deep-link |
| `E2B_API_KEY` | — | Required by `run_python_analysis` tool's E2B backend |
| `OBJECT_STORE_ENDPOINT` / `OBJECT_STORE_ACCESS_KEY` / `OBJECT_STORE_SECRET_KEY` / `OBJECT_STORE_BUCKET_ARTIFACTS` / `OBJECT_STORE_BUCKET_DOCUMENTS` | docker-compose defaults | MinIO / S3 wiring |

---

## 8. Common workflows

### Publish an agent template

```bash
# Templates live in examples/templates/*.yaml
curl -X POST http://localhost:8000/v1/agent-templates \
  -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  --data-binary @<(yq -o=json examples/templates/research_agent.yaml)

# Promote draft → published
curl -X POST http://localhost:8000/v1/agent-templates/agent.research/0.1.0/publish \
  -H "X-API-Key: dev-key"
```

### Submit a task

```bash
curl -X POST http://localhost:8000/v1/tasks \
  -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  -d '{
    "objective": "Do antibiotics work against viral infections?",
    "template": {"id": "agent.research"}
  }'
```

### View the trace

- JSON: `GET /v1/tasks/{task_id}/trace` (auth required)
- HTML: `GET /trace/{task_id}` (no auth, shareable)

### Approve a pending workflow gate (Sprint 14+)

```bash
# List pending
curl -s -H "X-API-Key: dev-key" http://localhost:8000/v1/approvals | jq

# Approve
curl -X POST -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  http://localhost:8000/v1/approvals/<id>/approve \
  -d '{"rationale": "looks good"}'

# Reject
curl -X POST -H "X-API-Key: dev-key" \
  http://localhost:8000/v1/approvals/<id>/reject

# Approve with input patch (modify the next pass's glossary)
curl -X POST -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  http://localhost:8000/v1/approvals/<id>/modify_input \
  -d '{"rationale": "narrower scope", "modified_input": {"focus": "FDA only"}}'
```

---

## 9. Migrations

Every schema change ships as an Alembic revision under `migrations/versions/`:

| Revision | What it adds |
|---|---|
| 0001 | Initial registry tables (agent_templates, tool_contracts, audit_log) |
| 0002 | Glossary entries |
| 0003 | Calibration history (Sprint 11.6 confidence-calibration ranking) |
| 0004 | Approval requests (Sprint 14) |

Apply: `uv run alembic upgrade head` · Roll back one: `uv run alembic downgrade -1`

Generate a new revision after editing models:

```bash
uv run alembic revision --autogenerate -m "your change"
# Edit the generated file in migrations/versions/ — autogenerate is a
# starting point, not a finished migration. Review carefully.
```

---

## 10. Project layout

```
src/orchestrator/
├── core/              # LLM client (LiteLLM + Mock), state schema
├── ir/                # GraphIR Pydantic models + validator
├── planner/           # PlannerV0 + PlannerV1 (multi-pattern + replan)
├── registry/          # Agent / tool / glossary registry, REST API, DB models
│   ├── api/           # FastAPI routes (tasks, approvals, trace, agent_templates)
│   └── db/            # SQLAlchemy ORM + session
├── runtime/           # LangGraph builders + verifier chain + chain node
│   └── verifiers/     # Schema, Confidence, Evidence, Policy, Glossary
├── temporal/          # Workflow + worker + activities (Sprint 13+)
├── approval/          # Approval Pydantic models + repository (Sprint 14)
├── gateway/           # MCP tool gateway + invocation handlers
├── storage/           # Object-store client (MinIO/S3)
└── cli.py             # `orchestrator` CLI

migrations/versions/   # Alembic revisions 0001–0004
examples/              # Agent template YAMLs + tool contracts
scripts/               # smoke-test.sh
tests/                 # unit + integration + temporal + approval
docs/                  # Planning docs + specs (gitignored by default;
                       # included in this archive bundle)
```

---

## 11. Troubleshooting

**`temporal-test-server` orphaned after killed pytest** — kill stragglers
with `pkill -f temporal-test-server`. Time-skipping test server doesn't
clean up after `SIGKILL`.

**`EndpointConnectionError` running the manager-workflow integration test** —
MinIO isn't reachable. Run `docker compose -f docker-compose.dev.yml up -d minio`
or set `ORCHESTRATOR_ARTIFACT_STORE=memory` for tests that don't need
real artifact persistence.

**`AgentNodeError: required input 'X' not resolvable`** — the template
has multiple required inputs but the request only sent `objective`.
Templates with exactly one required `str` input auto-receive the
objective; multi-input templates need each slot bound in the request
glossary (e.g. `{"glossary": {"research_question": "...", "max_sources": 5}}`).

**Migrations fail with "relation already exists"** — a prior run left
tables behind. Reset the dev DB:

```bash
docker compose -f docker-compose.dev.yml down -v   # NUKES the volume
docker compose -f docker-compose.dev.yml up -d
uv run alembic upgrade head
```

**`mypy` errors after adding a new dependency** — `uv sync` to pick up
new type stubs, then re-run `uv run mypy`.

---

## 12. Where to learn more

| Topic | File |
|---|---|
| Project rationale + concepts | `README.md` |
| Sprint-by-sprint progress + design choices + deviations | `PROGRESS.md` |
| Full development roadmap (Sprints 1–22+) | `docs/full-development-tasks.md` |
| Master index of planning docs | `docs/MASTER-INDEX-v1.0.md` |
| GoTo-market / ops bundles | `docs/bundle-05-*.md`, `docs/bundle-06-*.md`, `docs/bundle-07-*.md` |
| Pydantic model specifications | `docs/specs/models.md` |
| Contributing guidelines | `CONTRIBUTING.md` |
| Security policy | `SECURITY.md` |
| License | `LICENSE` (MIT) |
