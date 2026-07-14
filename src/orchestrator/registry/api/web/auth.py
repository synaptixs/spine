"""Web login / session for the unified UI (P0b).

The web surfaces are server-rendered (some, like ``/trace/{id}``, embed data), so
a browser navigation must be authenticated — but a plain navigation can't send an
``X-API-Key`` header. So the operator **logs in once** (``POST /login`` validates
an API key and sets a signed session cookie), and every web page requires that
session (``WebPrincipalDep``); unauthenticated navigations redirect to ``/login``.
The ``/v1`` JSON API accepts the same cookie (same-origin browser fetches) or an
``X-API-Key`` (CLI), so the pages' JS needs no key once logged in.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from orchestrator.registry.api.deps import Principal, principal_from_session, resolve_principal_from_key
from orchestrator.registry.api.session import COOKIE_NAME, MAX_AGE_SECONDS, sign_session
from orchestrator.registry.api.web.icons import brand_mark, icon

router = APIRouter(tags=["auth"])

# (icon, headline, sub) — the four beats of "what Spine does", animated top-to-bottom
# on the login hero as a packet falls down the pipeline and each stage lights up.
_PIPELINE: tuple[tuple[str, str, str], ...] = (
    ("file", "You write a requirement", "A Confluence page, a Notion doc, or a note."),
    ("search", "Spine understands your repo", "It maps the structure and conventions first."),
    ("cpu", "It writes and tests the code", "Grounded in what already exists."),
    ("gitpr", "You get a pull request", "Reviewed by you before anything ships."),
)


def _login_html() -> str:
    nodes = "".join(
        f'<div class="pnode"><span class="pico" style="--d:{0.3 + i * 1.2:.1f}s">{icon(glyph)}</span>'
        f"<div><strong>{head}</strong><span class='lbl'>{sub}</span></div></div>"
        for i, (glyph, head, sub) in enumerate(_PIPELINE)
    )
    pipeline = (
        '<div class="pipe" aria-hidden="true">'
        '<span class="pipe-line"></span><span class="pipe-dot"></span>'
        f"{nodes}</div>"
    )
    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Sign in · Spine</title>"
        '<link rel="stylesheet" href="/static/app.css">'
        '<link rel="stylesheet" href="/static/login.css">'
        '</head><body class="login-body">'
        '<div class="login-wrap">'
        '<section class="login-hero">'
        f'<div class="login-brand">{brand_mark()}Spine</div>'
        "<h1>Delegate a ticket.<br>Get a reviewed pull request.</h1>"
        '<p class="sub">Spine is an AI software engineer you hand a requirement to — it reads it, '
        "learns your codebase, writes and tests the change, and opens a PR for you to review.</p>"
        f"{pipeline}"
        "</section>"
        '<section class="login-card">'
        "<h2>Sign in</h2>"
        '<p class="hint">Enter your API key to continue.</p>'
        '<form id="login">'
        "<input id='key' type='password' placeholder='API key' autocomplete='current-password' autofocus>"
        "<button class='primary' type='submit'>Sign in</button>"
        "</form><div id='msg'></div>"
        "</section></div>"
        '<script src="/static/login.js"></script>'
        "</body></html>"
    )


class WebAuthRequiredError(Exception):
    """Raised by ``require_web_session`` when no valid session → redirect to /login."""


def require_web_session(request: Request) -> Principal:
    """The session principal for a web page, or a redirect-to-login signal."""
    principal = principal_from_session(request)
    if principal is None:
        raise WebAuthRequiredError
    return principal


WebPrincipalDep = Annotated[Principal, Depends(require_web_session)]


async def web_auth_redirect(_request: Request, _exc: WebAuthRequiredError) -> RedirectResponse:
    """Send unauthenticated page navigations to the login screen."""
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


class LoginRequest(BaseModel):
    api_key: str


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    """A standalone, animated sign-in page (no app nav) — a light blue/green hero
    that shows what Spine does beside the key field."""
    return HTMLResponse(_login_html())


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(body: LoginRequest, request: Request, response: Response) -> Response:
    principal = resolve_principal_from_key(body.api_key, request.app.state.settings)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")
    token = sign_session(
        {"id": principal.id, "tenant_id": principal.tenant_id, "roles": sorted(principal.roles)},
        request.app.state.settings.session_secret,
    )
    response.set_cookie(COOKIE_NAME, token, max_age=MAX_AGE_SECONDS, httponly=True, samesite="lax", path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


__all__ = ["WebAuthRequiredError", "WebPrincipalDep", "require_web_session", "router", "web_auth_redirect"]
