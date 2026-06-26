"""FastAPI app for the Block B backlog preview.

GET  /                     → the single-page form (paste a Confluence source).
POST /v1/intake/preview    → run analyze, return the proposed backlog as JSON.
GET  /healthz              → liveness.

Read-only by construction: the preview only ever calls
``BacklogService.analyze``. Live Jira creation is the CLI's job, gated by the
intent-approval bookend. ``create_app`` takes an optional ``service_builder``
so tests inject a fake service with no env or network.

The preview *logic* lives in ``run_preview`` so it can also be hosted by the
unified registry app under the shared web shell (``/app/backlog``). This
standalone app is kept for back-compat but is being superseded by that surface.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from orchestrator.core.env import load_local_env
from orchestrator.intake.confluence import ConfluenceError
from orchestrator.intake.factory import IntakeNotConfiguredError, build_confluence_service
from orchestrator.intake.service import (
    BacklogPlan,
    BacklogService,
    SourceUriError,
    parse_source_uri,
    spec_to_issue_request,
)

logger = logging.getLogger("orchestrator.intake.web")

ServiceBuilder = Callable[..., BacklogService]


class PreviewRequest(BaseModel):
    source: str = Field(..., description="Source root, e.g. confluence://<page_id>.")
    rules: str | None = Field(default=None, description="Optional path to a gap-rules YAML.")


def _plan_to_dict(plan: BacklogPlan) -> dict[str, object]:
    return {
        "documents": len(plan.documents),
        "truncated": plan.truncated,
        "blocked": plan.blocked,
        "intents": [i.model_dump() for i in plan.intents],
        "gaps": [
            {
                "intent": g.intent_id,
                "rule": g.rule_id,
                "severity": g.severity.value,
                "message": g.message,
            }
            for g in plan.gaps
        ],
        "specs": [
            {
                "intent_id": s.intent_id,
                "title": s.title,
                "summary": s.summary,
                "user_story": s.user_story,
                "acceptance_criteria": list(s.acceptance_criteria),
                "technical_notes": s.technical_notes,
                "nfrs": list(s.nfrs),
                "dependencies": list(s.dependencies),
                "estimate": s.estimate,
                "issue_summary": spec_to_issue_request(s).summary,
            }
            for s in plan.specs
        ],
    }


async def run_preview(req: PreviewRequest, builder: ServiceBuilder) -> dict[str, object]:
    """Run a read-only backlog preview for ``req`` via ``builder``.

    Shared by the standalone app and the registry-hosted ``/app/backlog`` surface.
    Raises ``HTTPException`` (400 bad/unsupported source or unconfigured intake,
    502 upstream Confluence failure)."""
    try:
        kind, root_id = parse_source_uri(req.source)
    except SourceUriError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if kind != "confluence":
        raise HTTPException(
            status_code=400, detail=f"unsupported source kind {kind!r} (only 'confluence' today)"
        )
    try:
        service = builder(dry_run=True, rules_path=req.rules)
    except IntakeNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        plan = await service.analyze(root_id)
    except ConfluenceError as exc:
        # Upstream fetch failed (bad page id, auth, Confluence down).
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _plan_to_dict(plan)


def create_app(*, service_builder: ServiceBuilder | None = None) -> FastAPI:
    """Build the preview app. ``service_builder(dry_run=..., rules_path=...)``
    defaults to the Confluence factory; tests pass a fake."""
    builder: ServiceBuilder = service_builder or build_confluence_service

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Bridge .env → os.environ so the factory sees Confluence/provider keys
        # when launched via uvicorn, matching the CLI's behavior.
        load_local_env()
        yield

    app = FastAPI(title="Orchestrator Backlog Preview", version="0.0.0", lifespan=_lifespan)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse, tags=["ui"])
    async def index() -> str:
        return _INDEX_HTML

    @app.post("/v1/intake/preview", tags=["preview"])
    async def preview(req: PreviewRequest) -> dict[str, object]:
        return await run_preview(req, builder)

    return app


_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backlog Preview</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 880px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.4rem; }
  form { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; margin: 1rem 0; }
  input[type=text] { flex: 1; min-width: 18rem; padding: .5rem; font: inherit; }
  button { padding: .5rem 1rem; font: inherit; cursor: pointer; }
  .hint { color: #888; font-size: .85rem; }
  .card { border: 1px solid #8884; border-radius: 8px; padding: .75rem 1rem; margin: .75rem 0; }
  .gap { border-left: 4px solid #c93; padding-left: .6rem; margin: .3rem 0; }
  .gap.blocker, .gap.needs_input { border-left-color: #d33; }
  .badge { display: inline-block; padding: 0 .4rem; border-radius: 4px; background: #8883; font-size: .8rem; }
  .blocked { color: #d33; font-weight: 600; }
  ul { margin: .3rem 0; padding-left: 1.2rem; }
  pre { white-space: pre-wrap; }
  .err { color: #d33; }
</style>
</head>
<body>
<h1>Confluence → Backlog preview</h1>
<p class="hint">Read-only. Derives intents, flags gaps, drafts specs. Nothing is written to Jira.</p>
<form id="f">
  <input type="text" id="source" placeholder="confluence://&lt;page_id&gt;" autofocus>
  <button type="submit" id="go">Preview</button>
</form>
<div id="status"></div>
<div id="out"></div>
<script>
const f = document.getElementById('f');
const out = document.getElementById('out');
const statusEl = document.getElementById('status');
const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const list = xs => (xs && xs.length)
  ? '<ul>' + xs.map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>' : '';

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  const source = document.getElementById('source').value.trim();
  if (!source) return;
  out.innerHTML = '';
  statusEl.textContent = 'Analyzing…';
  document.getElementById('go').disabled = true;
  try {
    const r = await fetch('/v1/intake/preview', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({source}),
    });
    const data = await r.json();
    if (!r.ok) {
      statusEl.innerHTML = '<span class="err">' + esc(data.detail || r.status) + '</span>';
      return;
    }
    statusEl.textContent = '';
    render(data);
  } catch (err) {
    statusEl.innerHTML = '<span class="err">' + esc(err) + '</span>';
  } finally {
    document.getElementById('go').disabled = false;
  }
});

function render(d) {
  let h = '';
  h += '<p>' + d.documents + ' document(s) read' + (d.truncated ? ' (truncated)' : '') + '. ';
  h += d.blocked
    ? '<span class="blocked">Blocked: gaps gate intent approval.</span>'
    : 'No blocking gaps.';
  h += '</p>';

  if (d.gaps.length) {
    h += '<h2>Gaps</h2>';
    for (const g of d.gaps) {
      h += '<div class="gap ' + esc(g.severity) + '"><span class="badge">' + esc(g.severity)
         + '</span> <strong>' + esc(g.intent) + '</strong>: ' + esc(g.message) + '</div>';
    }
  }

  h += '<h2>Specs (' + d.specs.length + ')</h2>';
  for (const s of d.specs) {
    h += '<div class="card"><strong>' + esc(s.title) + '</strong>'
       + (s.estimate ? ' <span class="badge">' + esc(s.estimate) + '</span>' : '');
    if (s.summary) h += '<p>' + esc(s.summary) + '</p>';
    if (s.user_story) h += '<p><em>' + esc(s.user_story) + '</em></p>';
    if (s.acceptance_criteria.length) h += '<p>Acceptance criteria:</p>' + list(s.acceptance_criteria);
    if (s.dependencies.length) h += '<p>Dependencies:</p>' + list(s.dependencies);
    h += '</div>';
  }
  out.innerHTML = h;
}
</script>
</body>
</html>
"""
