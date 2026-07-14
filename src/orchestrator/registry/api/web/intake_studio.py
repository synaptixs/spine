"""Intake studio page (Phase D2): preview any source, then delegate a gated run.

Two steps over endpoints that already exist (now genericised): preview a source
of any supported scheme (``confluence`` / ``notion`` / ``file`` / ``openspec`` /
``mcp-confluence``) into a curated backlog — intents, gaps, draft specs — then
delegate the full **gated** SDLC run (dry-run by default) via ``/v1/runs/start``.

The side-effectful *live* flows — single-feature `--live` build (opens a real
PR), address-review, and `openspec draft` — have no HTTP surface and make
external writes, so they're shown here as the exact CLI commands, not as web
buttons that would open real PRs on a click.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])

_BODY = (
    "<h1>Intake studio</h1>"
    "<p class='lead'>Preview any source as a backlog — intents, gaps, draft specs — then delegate a "
    "gated run. Supported: <code>confluence://</code> <code>notion://</code> <code>file://</code> "
    "<code>openspec://</code> <code>mcp-confluence://</code>.</p>"
    "<h2>1 · Preview a source</h2>"
    "<div class='repo-bar'>"
    "<label>source <input id='source' "
    "placeholder='file://./examples/intake/sample-spec.md' size='42'></label>"
    "<button id='preview' class='primary'>Preview</button>"
    "</div>"
    "<div id='pstatus' class='muted'></div><div id='backlog'></div>"
    "<h2>2 · Delegate a run</h2>"
    "<p class='muted'>Starts the full gated pipeline (intent gate → build → merge gate). "
    "<strong>Dry-run by default</strong> — a local branch + diff, no external writes. "
    "Needs the Temporal worker running.</p>"
    "<div class='repo-bar'>"
    "<label><input type='checkbox' id='create_jira'> create real Jira issues</label>"
    "<button id='delegate' class='primary'>Delegate run</button>"
    "</div>"
    "<div id='dstatus' class='muted'></div><div id='runout'></div>"
    "<div class='panel'><div class='panel-head'>Advanced flows (CLI)</div>"
    "<p class='panel-sub'>These make external writes (real PRs, pushed commits) and run from the "
    "CLI/MCP, not this page:</p>"
    "<div class='cli'>orchestrator sdlc feature --source &lt;uri&gt; --live   # one feature → a real PR</div>"
    "<div class='cli'>orchestrator sdlc address-review --pr &lt;url&gt;"
    "         # revise a PR from review comments</div>"
    "<div class='cli'>orchestrator openspec draft --source &lt;uri&gt;"
    "          # bootstrap OpenSpec changes</div>"
    "</div>"
)


@router.get("/app/intake", response_class=HTMLResponse)
async def intake_studio_page(_p: WebPrincipalDep) -> HTMLResponse:
    return HTMLResponse(
        content=page_shell(
            title="Intake studio",
            active="Intake studio",
            body=_BODY,
            head='<link rel="stylesheet" href="/static/intelligence.css">',
            scripts='<script src="/static/jobrun.js"></script>'
            '<script src="/static/intake-studio.js"></script>',
        )
    )


__all__ = ["router"]
