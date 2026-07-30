# Live Integration Testing — Blocks A & B

How to exercise the SDLC-orchestrator wedges against **real** services
(GitHub, Confluence, Jira, a real LLM). Everything here is **safe by
default**: read-only or print-only until you explicitly opt into writes.

> Setup: `cp .env.example .env`, fill in creds, then run the commands below.
> `.env` is gitignored; pydantic-settings auto-loads it.

---

## Order of testing (least → most side-effecting)

1. **Block B — Confluence ingest, dry-run** — read-only on Confluence,
   no Jira writes. Safest. Validates auth, content extraction, intent +
   spec quality.
2. **Block A — PR review, print-only** — read-only on GitHub, no review
   posted. Validates App auth, diff fetch, review quality.
3. **Block A — PR review, posted** — writes one review to a real PR.
4. **Block B — Jira create** — writes issues to a real (sandbox) project.

---

## 1. Block B — Confluence ingest (dry-run, read-only)

**Creds:** `ANTHROPIC_API_KEY`, `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL`,
`CONFLUENCE_API_TOKEN`. (No Jira creds needed in dry-run.)

**Get a page id:** open a Confluence page; the id is in the URL
(`/pages/<id>/...`) or via *··· → Page Information*.

```bash
uv run orchestrator ingest --source confluence://<PAGE_ID>
```

Prints: document count, extracted intents (with scope/deps/NFRs/open
questions), gap findings, whether gaps gate approval, and the would-be
Jira issues. **Nothing is written.**

What to check:
- Did Confluence auth succeed (no 401/403)?
- Was the page body extracted cleanly (storage-XHTML → readable text)?
- Are the intents sensible and appropriately split?
- Do the gaps make sense? Does `blocked` reflect real open questions?

---

## 1a. Block B — Web preview (browser, read-only)

Same analysis as the dry-run CLI above, in a browser: paste a Confluence
source, see the proposed backlog (intents, gaps, specs) before anything is
written. **Read-only by construction** — the app only ever calls
`analyze`; it has no path to Jira creation (that stays with the CLI behind
the intent-approval bookend).

**Creds:** same as the dry-run CLI (`ANTHROPIC_API_KEY` + `CONFLUENCE_*`).
The app loads `.env` on startup.

```bash
uv run uvicorn orchestrator.intake.web:create_app --factory --port 8001
```

Open <http://localhost:8001>, paste `confluence://<PAGE_ID>`, click
**Preview**. The JSON behind the page is also available directly:

```bash
curl -s localhost:8001/v1/intake/preview \
    -H 'content-type: application/json' \
    -d '{"source": "confluence://<PAGE_ID>"}' | jq
```

Response codes: `400` for a bad/non-Confluence source URI or missing
Confluence creds; `502` for an upstream Confluence failure (bad page id,
auth, outage); `200` with the backlog otherwise. `/healthz` returns
`{"status":"ok"}`.

This is the adopter-facing surface for non-engineers — share the URL, no
CLI needed. It is **not** hardened for public exposure (no auth in front);
run it locally or behind your own access control.

---

## 2. Block A — PR review (print-only, read-only)

**Creds:** `ANTHROPIC_API_KEY`, `GITHUB_APP_ID`,
`GITHUB_APP_PRIVATE_KEY` (or `_PATH`).

**Prereqs:** a GitHub App created + installed on a repo. You need the
**installation id** (Settings → Integrations → your App, or the
`installation.id` from any webhook delivery), the **repo** (`owner/name`),
and a **PR number**.

```bash
uv run python scripts/live_review.py \
    --repo owner/name --pr 12 --installation 12345678
```

Prints the computed review (verdict, summary, inline comments) **without
posting**. Validates App JWT → installation token → diff fetch → LLM +
verifier review.

What to check:
- Did token minting succeed (no 401)?
- Were the changed files + line numbers fetched correctly?
- Is the verdict right (BLOCKER → REQUEST_CHANGES; else COMMENT)?
- Do inline comments anchor to real changed lines?

---

## 3. Block A — PR review (posted)

Same as #2 with `--post`. **Writes one review to the PR.** Use a throwaway
PR first.

```bash
uv run python scripts/live_review.py \
    --repo owner/name --pr 12 --installation 12345678 --post
```

To test the **webhook** end-to-end (auto-review on PR open), you also need
a public URL. Run the API with `GITHUB_APP_ENABLED=true`, expose it
(`ngrok http 8000` or deploy to Cloud Run), and set the App's webhook URL
to `https://<public>/v1/github/webhook` with the matching
`GITHUB_APP_WEBHOOK_SECRET`. Open a PR → the review posts automatically.

---

## 4. Block B — Jira create (writes issues)

**Creds:** add `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`,
`JIRA_PROJECT_KEY`. Use a **sandbox project**.

```bash
# Preview first (dry-run is the default):
uv run orchestrator ingest --source confluence://<PAGE_ID>

# Then create for real (refused if gaps gate approval, unless --force):
uv run orchestrator ingest --source confluence://<PAGE_ID> --create
```

`--create` is refused when gaps gate the intent-approval bookend — resolve
the gaps in Confluence and re-ingest, or pass `--force`. Created issue keys
+ URLs are printed.

---

## 5. Block C — Run the SDLC skeleton (Temporal)

The Block-C skeleton marches a source page A→Z to a **stubbed** prod deploy
through two human approval gates. Everything domain-specific (codegen, CI,
deploy, E2E) is stubbed — this exercises the **orchestration**, not real
delivery. Real Cloud Run / codegen are Block D.

**Offline first (no docker, no creds):** the unit suite drives the whole
pipeline through both gates on an in-process time-skipping server:

```bash
uv run python -m pytest tests/sdlc -q
```

**Full-loop integration (real Temporal + real Postgres):** a single
`-m integration` test stands up a real in-process worker against the docker
Temporal server, runs the **real** activities (real git worktrees, stub
deploy, an injected offline intake), decides both gates by signalling the
`task-{sdlc_id}` handle once each gate's `ApprovalRequest` row goes pending,
and asserts the prod revision + the audit trail land in Postgres. It skips
cleanly when Temporal is unreachable.

```bash
docker compose -f docker-compose.dev.yml up -d temporal temporal-postgres postgres
uv run pytest tests/integration/test_sdlc_workflow_e2e.py -m integration -v
```

**Against a real Temporal server:** start the dev stack, then run the SDLC
worker on its own task queue and start a workflow.

```bash
# 1. Temporal dev server (docker-compose.dev.yml) must be up on :7233.
# 2. Run the SDLC worker (own queue, default 'sdlc-tasks'):
uv run python -m orchestrator.sdlc.worker

# 3. In another shell, launch a run. This generates an sdlc_id and starts
#    SDLCWorkflow with id task-{sdlc_id} (the id the approval API routes to):
uv run orchestrator sdlc run --source confluence://<PAGE_ID>
```

`sdlc run` prints the `sdlc_id`, the `task-{sdlc_id}` workflow id, the task
queue, and the two gate approval ids (`sdlc-{sdlc_id}-0` for intents,
`sdlc-{sdlc_id}-1` for prod). It returns right after starting the workflow;
pass `--wait` to block until it finishes and print the result, or
`--create-jira` to write real Jira issues (default is dry-run synthetic keys).

Config knobs (env):

| Var | Default | Purpose |
|---|---|---|
| `SDLC_TASK_QUEUE` | `sdlc-tasks` | worker's task queue (isolated from `orchestrator-tasks`) |
| `SDLC_WORKSPACE_ROOT` | `/tmp/sdlc-workspaces` | where per-issue git worktrees live |
| `SDLC_REPO_URL` | _(unset)_ | clone this repo for worktrees; unset = scratch `git init` |
| `ORCHESTRATOR_DATABASE_URL` | local Postgres | audit rows |

The intake stage calls the **real** `BacklogService`, so a live run needs the
same Confluence creds as §1. Jira create defaults to **dry-run** (synthetic
keys), so the fan-out gets issue keys without writing to a tracker.

**Approval gates are real, decidable rows.** Each gate persists an
`ApprovalRequest` (id `sdlc-{sdlc_id}-{0|1}`, task_id `{sdlc_id}`) so you decide
it through the existing REST API instead of raw Temporal signals:

```bash
# List what's waiting:
curl -s localhost:8000/v1/approvals -H "x-api-key: $KEY"
# Approve gate 1 (intents):
curl -X POST localhost:8000/v1/approvals/sdlc-<sdlc_id>-0/approve -H "x-api-key: $KEY"
# Promote to prod with release notes (gate 2):
curl -X POST localhost:8000/v1/approvals/sdlc-<sdlc_id>-1/modify_input \
     -H "x-api-key: $KEY" -H "content-type: application/json" \
     -d '{"modified_input": {"release_notes": "v1 ships"}}'
```

> **Routing:** the REST API signals `task-{task_id}`, so start the workflow
> with id `task-{sdlc_id}` for decisions to reach it. (Raw `approve` / `deny` /
> `modify_input` Temporal signals still work for tests.)

What to check:
- Worker registers on `sdlc-tasks` and picks up the workflow.
- Gate 1 blocks until you `approve`; `deny` terminates with
  `intents_denied`.
- N children fan out (one git worktree each) and fan back in before
  integration test.
- Gate 2 blocks until you `approve`/`modify_input`; the run ends at the stub
  prod smoke test with `prod_revision` set.
- An audit row lands at every stage.

---

## Getting the credentials

**Atlassian API token (Confluence + Jira):**
<https://id.atlassian.com/manage-profile/security/api-tokens> → Create
token. Use your account email + the token for Basic auth.

**GitHub App:** Settings → Developer settings → GitHub Apps → New GitHub
App. Permissions: `Pull requests: Read & write`, `Contents: Read`.
Generate a private key (downloads a `.pem`). Install the App on a repo.
The App ID is on the App's settings page; the installation id is in the
install URL or any webhook delivery payload.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `GitHub App not configured` | `GITHUB_APP_ID` / private key missing |
| 401 minting installation token | wrong App ID, bad/rotated private key, or App not installed on the repo |
| `Confluence not configured` | `CONFLUENCE_BASE_URL` / `EMAIL` / `API_TOKEN` missing |
| Web preview 400 "not configured" | `.env` not loaded / `CONFLUENCE_*` missing when uvicorn started |
| Web preview 502 | upstream Confluence error — check the page id and token access |
| Confluence 404 on a page | wrong page id, or the token's account lacks access |
| `Jira not configured` (only on `--create`) | `JIRA_*` incl. `PROJECT_KEY` missing |
| Jira 400 on create | issue type name not in the project (default "Story") |
| Empty/short LLM output | provider key invalid or rate-limited |
