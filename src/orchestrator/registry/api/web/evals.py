"""Evals & skill-quality page (Phase E1).

Surfaces the one quality signal that actually exists in-app: each skill's
promotion status and (when promoted) its held-out eval score, from `/v1/skills`.
The eval *harness* itself runs offline (dev scripts, Markdown scorecards) with no
in-app run and no scores-over-time store — the page says so plainly rather than
faking a metrics dashboard.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


@router.get("/app/evals", response_class=HTMLResponse)
async def evals_page(_p: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Evals &amp; skill quality</h1>"
        "<p class='lead'>How Spine measures the skills its engineer uses. A skill graduates from "
        "<span class='pill stat-candidate'>candidate</span> to <span class='pill stat-active'>active</span> "
        "when its held-out acceptance beats the baseline by ≥10 points on the eval harness.</p>"
        "<div class='panel'><div class='panel-head'>How evals run</div>"
        "<p class='panel-sub'>The harness runs <strong>offline</strong> (dev scripts) and writes Markdown "
        "scorecards under <code>docs/evals/</code> — there's no in-app run trigger and no "
        "scores-over-time store yet. Promoted scores are baked into the catalog and shown below.</p></div>"
        "<div id='status' class='muted'></div>"
        "<h2>Active skills</h2><div id='active'><p class='muted'>Loading…</p></div>"
        "<h2>Candidate skills</h2><div id='candidate'></div>"
    )
    return HTMLResponse(
        content=page_shell(
            title="Evals",
            active="Evals",
            body=body,
            head='<link rel="stylesheet" href="/static/intelligence.css">',
            scripts='<script src="/static/jobrun.js"></script><script src="/static/evals.js"></script>',
        )
    )


__all__ = ["router"]
