# Design: unified UI — one backbone, three faces

**Status:** **P0–P3 IMPLEMENTED** (the buildable UI; P4 TUI + P5 SPA remain the optional,
when-complexity-warrants follow-ons). P0 — one app + one nav + one stylesheet + real static
assets, console/backlog folded in, **login/session auth** (shipped in 1.16.0). P1 — `GET /v1/stream`
SSE run-state feed (tails the audit log; `TraceIdMiddleware` rewritten to pure ASGI so streaming
works) + `/v1/personas` + `/v1/skills` read API. P2 — the **delegation inbox** (`/app/inbox`): live
feed over SSE + inline approval gates + a composer that starts a run via `POST /v1/runs/start`
(thin wrapper over `run_control.start_run`). P3 — the personas browser (`/app/personas`) + home
linking the full surface. All on `develop`, CI-green; P1–P3 unreleased past 1.16.0.

Addresses the fragmented operator/UX
surface: three separate embedded-in-Python web UIs on different ports, inconsistent (or absent)
auth, no shared navigation, plus Temporal UI / MinIO as further islands. The strategy is **unify
the existing surfaces under one app + one auth + one real-time core ("the backbone"), then expose
three faces over it**: a **delegation inbox** (home), an **operator console** (the full views),
and a **developer TUI** (follow-on). Not a from-scratch SPA — but built **headless-ready** so a
React/Vite (or lighter SvelteKit/Solid) frontend is a clean later swap (§6), in a wheel-isolated
`web/` folder that never touches the orchestrator package.

**Thesis:** the fragmentation is ~80% fixable by *consolidation*, not a rewrite. The product story
has also shifted with the persona work toward "delegate to an engineer," so the **inbox** — not a
generic dashboard — should be the front door; the console views are the structural backbone behind
it, and the TUI is cheap once the API is unified.

## Where the UI stands today
All UI is HTML/CSS/JS as Python string literals; surfaces are disconnected:

| Surface | File | Auth | Delivery |
|---|---|---|---|
| Approvals + runs console (`/console`) | `registry/api/console.py` | API key typed into a form (localStorage) | inline JS-in-Python, 10s polling |
| Trace timeline (`/trace/{id}`) | `registry/api/trace.py` | **none** (shareable URL) | server-rendered HTML, static |
| Intake/backlog preview (`/`) | `intake/web/app.py` | **none** | **separate FastAPI app, own port** |
| Tool gateway (`/v1/tools`) | `gateway/api/app.py` | API key | launched separately |
| Registry JSON API (`/v1/*`) | `registry/api/app.py` (+ routers) | `deps.require_principal` (X-API-Key) | swagger at `/docs`, unlinked |
| Temporal UI / MinIO | docker-compose | none / its own | separate ecosystems |

The data the faces need **already exists** behind `/v1`: `runs.py` (run summaries from the audit
log), `approvals.py` (CRUD + decisions), `trace.py` (timeline JSON + HTML), `tasks.py`,
`agent-templates`/`tool-contracts`/`glossary`. What's missing is consolidation, consistent auth, a
real-time channel, and a personas/skills read.

## Design

### 1. The unify layer (the backbone — the real work)
Four consolidations; the faces are thin on top.

- **One app, one port.** Mount the intake preview's routes into the registry FastAPI app
  (`registry/api/app.py`) and retire the standalone `intake/web/app.py` process; the gateway can
  stay a separate service or fold in behind the same nav. **Extract the inline HTML/CSS/JS** from
  `console.py` / `trace.py` / `intake/web/app.py` into shared **templates + static assets** (a
  `registry/api/web/` dir), killing the JS-in-a-Python-string problem and giving every face one
  design system + one nav shell.
- **One auth model.** Today `console` uses a key-in-a-form, `trace` and `intake` have none. Apply
  one principal/session model uniformly via the existing `deps.require_principal` (X-API-Key, with
  the existing single-key and `ORCHESTRATOR_PRINCIPALS` multi-tenant modes). Trace links that were
  "shareable, unauthed" become authed (or explicit signed read-only links if sharing matters).
- **One read model.** Consolidate run / approval / trace / backlog reads behind a coherent `/v1`
  surface (most exist) and **add a personas/skills read** (`GET /v1/personas`, `GET /v1/skills`)
  to surface the persona-skill work (`catalog.skills`, `personas/`, the `AgentTemplate` registry).
- **One real-time channel** — see §2. The single genuinely new capability.

### 2. The SSE contract (run-state stream)
The console *polls* every 10s today; the inbox needs *push*. Add **one** server-sent-events
endpoint — not a general event bus.

- **Endpoint:** `GET /v1/stream` → `text/event-stream`; authed; optional `?run_id=` filter to scope
  to one run. Heartbeat comment every ~15s; `Last-Event-ID` support for resume.
- **Event envelope (JSON `data:`):**
  `{ "type": ..., "run_id": "<sdlc_id>", "seq": <int>, "ts": "<iso8601>", "payload": {...} }`
- **Event types (small, closed set):**
  - `run.created` — a delegation started (payload: title, persona, model, source).
  - `run.stage` — phase transition (payload: `stage` ∈ plan|ground|implement|author_tests|run_tests|refine|pr, `status` ∈ started|passed|failed, plus cost/iterations).
  - `run.gate` — an in-loop approval pause (payload: approval_id, risk, summary, target).
  - `approval.updated` — a gate decided elsewhere (payload: approval_id, decision).
  - `run.completed` — terminal (payload: verdict, pr_url, files, cost).
- **The architectural wrinkle (call it out):** runs execute in the **Temporal worker process**,
  separate from the API process — so the API cannot observe run state in-memory. The **audit log /
  a `run_events` table is the source of truth**: activities/workflows already write audit rows;
  the stream is the **API tailing those rows and emitting deltas**. Mechanism options (decision
  below): Postgres `LISTEN/NOTIFY`, a short-interval audit-log tail (simplest), or Redis pub/sub.
  `seq` is the audit row id, making resume and ordering trivial. The single-shot `feature_runner`
  path (no Temporal) emits the same events directly.

### 3. Face A — delegation inbox (home)
- **Composer** → POST a delegation = a feature run (maps to `run_feature` / the SDLC workflow):
  persona + model + source (Jira / Confluence-via-MCP / file) selectors.
- **Feed** — each task is a run; live stage chips (plan → implement → test → PR) driven by the SSE
  stream; cost / iterations inline.
- **Inline gates** — a `run.gate` event renders a "needs you" card → approve / edit-input / reject
  hits the existing approvals API (`/v1/approvals/{id}`), resuming the in-loop pause.
- **Inline PR/diff review** — link to the PR; show the diff/trace on expand.

### 4. Face B — operator console (backbone views)
The same data as full tables, re-skinned under the shared shell: runs list + filters; run detail =
the `trace.py` timeline; the approvals queue (`console.py`'s core); backlog (the folded-in intake
preview); a **personas/skills browser** (new — the catalog + registry). The inbox is a curated
lens; the console is the full table — one API underneath.

### 5. Face C — developer TUI (follow-on)
A Textual app against the same `/v1` API + SSE: runs list, trace, approvals, a "new" composer,
keyboard-driven. Cheap *because* the API is unified; the existing `orchestrator` CLI stays, the
TUI is its interactive cousin.

### 6. The "better alternative" — a headless frontend (React/Vite or lighter)
The server-rendered templates (P0/P3) are **deliberately disposable**. The durable contract is the
**JSON `/v1` read API + `/v1/stream` SSE + `deps.require_principal` auth** — the same contract the TUI
consumes, which forces it to be UI-agnostic (headless) by construction. A component-framework SPA is
therefore a **drop-in replacement of the presentation layer only**, not a backend rewrite.

- **Reused on migration, unchanged:** the `/v1` JSON endpoints, the `/v1/stream` SSE (`EventSource` is
  native in the browser), auth, and the run/approval/persona data model.
- **Discarded:** the Jinja templates + HTMX sprinkles — the cheapest code in the system, by design.
- **Migrate incrementally, page by page:** stand the SPA up, point it at `/v1` + `/v1/stream`, replace
  the inbox first, then the console — the backbone never moves.

**Framework.** The choice barely matters once the boundary is clean — each just consumes `/v1` + SSE:
- **React + Vite** — safe default; largest ecosystem/hiring pool (pair with TanStack Query + a thin
  `EventSource` hook).
- **SvelteKit / SolidStart** — lighter, less boilerplate, simpler reactivity; a strong fit since the
  project is already TS-first (TS codegen + the README's "TS SDK and console" ambition).
- **Recommendation: don't pick now.** Keep P0–P3 headless-ready and choose at frontend-build time. If
  forced: React+Vite for ecosystem, SvelteKit if you value simplicity and stay TS-first.

**Contract-driven typing (the single most important drift-killer).** FastAPI already emits an OpenAPI
schema; generate a typed TS client from it (`openapi-typescript` / `orval`) so the frontend is *typed
against the live API*, with the SSE event types shared from one schema source.

**Where it lives — recommendation: a monorepo `web/` folder now; a separate repo only later.**
- **Now — same repo, top-level `web/` (or `apps/console/`).** Keeps the API + UI contract **atomic**
  (one PR changes `/v1` and its consumer together) — the biggest risk while the contract is still
  evolving. Local dev and CI stay in one place.
- **It does not touch the orchestrator wheel.** Hatchling packages `src/orchestrator` **only**
  (`[tool.hatch.build.targets.wheel] packages = ["src/orchestrator"]`), so a `web/` folder ships
  *nothing* into the pip artifact — the Python library/CLI is byte-for-byte unaffected. The frontend
  has its own `package.json`/tooling and is **never imported by Python**.
- **CI isolation:** the existing Python `check` job is unchanged; add a separate frontend job gated by a
  `web/**` path filter so the Node and Python pipelines never entangle.
- **The only coupling is the API contract** — versioned (`/v1`) + OpenAPI-typed. That is exactly what
  makes a *later* split into a separate repo (`agent-orchestrator-web`) cheap: do it once the frontend
  grows its own release cadence/team and the contract has stabilized. Splitting earlier trades atomic
  contract changes for cross-repo coordination — not worth it while `/v1`/SSE are young.

### Gaps to close *before* building the headless frontend
- **P0 + P1 must land first** — a SPA can't be built against a fragmented API; the consolidated `/v1`
  read API + a **stable, versioned SSE schema** are prerequisites (re-skinning churn is cheap; a React
  app re-coding against a moving SSE contract is not).
- **OpenAPI completeness** — the generated TS client is only as good as the routers' Pydantic *response*
  models; audit `runs.py` / `approvals.py` / `trace.py` / the new personas endpoint for accurate
  response schemas first.
- **Browser auth model** — X-API-Key-in-localStorage is fine for the templated UI; a separate-origin SPA
  wants a real session/token (+ CSRF posture). Decide before the SPA, not during.
- **Build/deploy seam** — decide where the Vite build is served: behind the same FastAPI app as static
  assets (same origin → no CORS, simplest) vs a separate host/CDN (needs CORS). Start same-origin.
- **Real-time at scale** — SSE from a single API process is fine; multiple API replicas each need to tail
  the same audit-log/pub-sub source. Note it for when you scale past one process.
- **Persona-first tension (again)** — a real SPA is even more dev-facing surface area against the
  north-star deferral; sequence it consciously.

## Phasing (each independently shippable)
- **P0 — Unify** (parity, no new features): one app, one auth, shared nav/shell; inline UI →
  templates/static; fold in intake; link `/docs`. **Kills ~80% of the fragmentation; de-risks the
  rest.** ← first.
- **P1 — Live core** (backend-only): the `/v1/stream` SSE + the `run_events` source + the
  consolidated read API (+ `GET /v1/personas`, `/v1/skills`). Nothing visual changes yet.
- **P2 — Inbox**: composer + feed + inline approvals over the stream — the differentiated front door.
- **P3 — Console**: runs / trace / approvals / backlog / personas under the shell.
- **P4 — TUI**: Textual app on the same API + SSE.
- **P5 — Headless frontend** (optional; only when complexity warrants): a React/Vite — or SvelteKit /
  SolidStart — SPA in `web/`, OpenAPI-typed, consuming the **unchanged** `/v1` + SSE, migrated page by
  page. Triggered by HTMX/template sprawl, not adopted by default. Discards templates, keeps the backbone.

## Decisions to confirm
1. **SSE transport for cross-process run state:** audit-log tail (simplest, recommended to start) vs
   Postgres `LISTEN/NOTIFY` vs Redis pub/sub. Recommend the **tail** for P1, swappable later.
2. **Auth on trace links:** make them authed (recommended) vs keep shareable read-only via signed
   links.
3. **Rendering stack:** server-rendered + HTMX/Alpine (recommended — no build step, matches the
   "keep it boring" stance) vs a small SPA. Defer the SPA until concurrent-user demand is real.
4. **Inbox vs console as the default route** — recommend the **inbox** as `/` (on-thesis), console at
   `/console`.
5. **Keep the gateway a separate service** vs fold it behind the same nav (recommend: separate for
   now; link to it).
6. **Frontend framework** (at P5) — React+Vite (ecosystem) vs SvelteKit/SolidStart (lighter, TS-first).
   Recommend deferring the choice; keep P0–P3 headless-ready so it stays open.
7. **Frontend placement** — monorepo `web/` now (recommended; atomic contract, wheel-isolated) vs a
   separate repo later (once `/v1`/SSE stabilize and it grows its own cadence).

## Honest risks / limits
- **The SSE stream is the only real new capability** — everything else is re-skinning existing data.
  The cross-process sourcing (worker writes, API streams) is the subtle part; keep it a single
  run-state channel, not a general event bus.
- **Auth unification touches every surface** — do it in P0 while the surface is small, not retrofitted.
- **Persona-first tension:** this is dev-facing surface area, against the north-star deferral. The
  inbox is the *least* premature option (it's where the persona becomes usable), but it is still a
  deliberate spend on UX — sequence it consciously against persona depth.
- **No SPA yet:** server-rendered + HTMX/Alpine is enough until real concurrent-user demand; P0–P1
  don't have to be thrown away to graduate to a SPA later.
- **Temporal UI / MinIO stay separate** — link to them from the shell rather than reimplement; they
  serve workflow-internals / artifact-store needs the unified UI shouldn't duplicate.

## First step
**P0 unify:** fold `console` + `trace` + `intake` into one FastAPI app (`registry/api/app.py`) with
one nav + one auth, the inline HTML/CSS/JS extracted to `registry/api/web/` templates + static.
Zero new features, immediate coherence — and the exact foundation the inbox, console, and TUI all
share. Then P1 lights up the SSE stream the inbox needs.
