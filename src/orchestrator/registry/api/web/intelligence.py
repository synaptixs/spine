"""Repo Intelligence pages (Phase B): the flagship — Spine "understands your repo".

Five pages over the Phase-0 capability/job endpoints, so the invisible
comprehension capabilities become the UI's centrepiece:

- B1 ``/app/understand``   — run ``understand`` as a job with live progress.
- B2 ``/app/state``        — render the current-state report (developer/stakeholder).
- B3 ``/app/memory-bank``  — browse a repo's committed ``memory-bank/*.md``.
- B4 ``/app/graph``        — a module-level knowledge-graph overview.
- B5 ``/app/catalog``      — what Spine can do in this repo (catalog + plan).

Each page is a thin shell; the work is client-side against ``/v1/capabilities/*``
+ ``/v1/jobs`` (see ``jobrun.js`` / ``md.js``). Every repo is named by a path
resolved under the workspace root, so no arbitrary-path exposure.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


def _repo_bar(button_id: str, button_label: str, *, extra: str = "") -> str:
    """A repo-source input + action button shared by the intelligence pages.

    Accepts a local path or a git URL (github/bitbucket/gitlab/enterprise) —
    URLs are cloned on demand server-side."""
    return (
        '<div class="repo-bar">'
        '<label>repo <input id="repo" value="." '
        'placeholder="local path or git URL (https://github.com/org/repo)" size="40"></label>'
        f"{extra}"
        f'<button id="{button_id}" class="primary">{button_label}</button>'
        "</div>"
        '<p class="repo-hint muted">A path under the workspace root, or a git URL to clone '
        "(github.com / bitbucket.org / gitlab.com, or a configured enterprise host).</p>"
        '<div id="status" class="muted"></div>'
    )


def _page(*, title: str, active: str, intro: str, body: str, script: str) -> HTMLResponse:
    return HTMLResponse(
        content=page_shell(
            title=title,
            active=active,
            body=f"<h1>{title}</h1><p class='lead'>{intro}</p>{body}",
            head='<link rel="stylesheet" href="/static/intelligence.css">',
            scripts=script,
        )
    )


@router.get("/app/understand", response_class=HTMLResponse)
async def understand_page(_p: WebPrincipalDep) -> HTMLResponse:
    return _page(
        title="Understand",
        active="Understand",
        intro=(
            "Point Spine at a repo and it maps the code — languages, structure, data layer — "
            "into a committed <strong>memory bank</strong>. Deterministic, no LLM. Runs as a job; "
            "watch progress below, then open the results."
        ),
        body=_repo_bar("run", "Analyze")
        + "<div id='progress' class='progress'></div><div id='result'></div>",
        script='<script src="/static/jobrun.js"></script><script src="/static/understand.js"></script>',
    )


@router.get("/app/state", response_class=HTMLResponse)
async def state_page(_p: WebPrincipalDep) -> HTMLResponse:
    lens = (
        '<label>lens <select id="lens">'
        '<option value="developer">developer</option>'
        '<option value="stakeholder">stakeholder</option>'
        "</select></label>"
    )
    return _page(
        title="Current State",
        active="Current State",
        intro=(
            "A code-true report of what this system <em>is</em> right now — overview, runtime, "
            "structure, architecture — in a <strong>developer</strong> or <strong>stakeholder</strong> "
            "lens. Deterministic, generated from the knowledge graph."
        ),
        body=_repo_bar("run", "Generate", extra=lens) + "<article id='report' class='report'></article>",
        script='<script src="/static/jobrun.js"></script><script src="/static/md.js"></script>'
        '<script src="/static/state.js"></script>',
    )


@router.get("/app/memory-bank", response_class=HTMLResponse)
async def memory_bank_page(_p: WebPrincipalDep) -> HTMLResponse:
    return _page(
        title="Memory bank",
        active="Memory bank",
        intro=(
            "The committed, code-true knowledge <code>understand</code> writes to "
            "<code>memory-bank/*.md</code> — architecture, domain model, tech context, conventions. "
            "Haven't run it yet? Use <a href='/app/understand'>Understand</a> first."
        ),
        body=_repo_bar("load", "Load")
        + "<div class='mb'><nav id='mb-nav' class='mb-nav'></nav>"
        + "<article id='mb-doc' class='report'></article></div>",
        script='<script src="/static/jobrun.js"></script><script src="/static/md.js"></script>'
        '<script src="/static/memory-bank.js"></script>',
    )


@router.get("/app/graph", response_class=HTMLResponse)
async def graph_page(_p: WebPrincipalDep) -> HTMLResponse:
    return _page(
        title="Knowledge graph",
        active="Knowledge graph",
        intro=(
            "A module-level view of the Product Knowledge Graph: the node/edge mix, the biggest "
            "modules, how they depend on each other, and the highest-connected symbols. Extracted "
            "as a job; a bounded overview (top modules/edges), not the raw graph."
        ),
        body=_repo_bar("run", "Extract") + "<div id='progress' class='progress'></div><div id='graph'></div>",
        script='<script src="/static/jobrun.js"></script><script src="/static/graph.js"></script>',
    )


@router.get("/app/catalog", response_class=HTMLResponse)
async def catalog_page(_p: WebPrincipalDep) -> HTMLResponse:
    intent = '<label>intent <input id="intent" placeholder="e.g. add an endpoint" size="22"></label>'
    return _page(
        title="Catalog",
        active="Catalog",
        intro=(
            "What Spine can do <em>in this repo</em>: the full capability catalog, and the subset the "
            "planner would select for a given intent (languages, greenfield/brownfield, skills)."
        ),
        body=_repo_bar("planbtn", "Plan for repo", extra=intent)
        + "<div id='plan'></div><h2>Full catalog</h2><div id='catalog'><p class='muted'>Loading…</p></div>",
        script='<script src="/static/jobrun.js"></script><script src="/static/catalog.js"></script>',
    )


__all__ = ["router"]
