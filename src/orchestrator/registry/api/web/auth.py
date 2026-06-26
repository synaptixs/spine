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
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["auth"])


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
    body = (
        "<h1>Sign in</h1>"
        '<p class="lead">Enter your API key to access the console.</p>'
        '<form id="login" class="intake-form">'
        "<input id='key' type='password' placeholder='API key' autocomplete='current-password' autofocus>"
        "<button class='primary' type='submit'>Sign in</button>"
        "</form><div id='msg'></div>"
    )
    return HTMLResponse(
        page_shell(
            title="Sign in",
            active="",
            body=body,
            head='<link rel="stylesheet" href="/static/intake.css">',
            scripts='<script src="/static/login.js"></script>',
        )
    )


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
