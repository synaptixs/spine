"""Bearer-token auth for the remote (HTTP) plugin server.

Phase C runs the orchestrator plugin over ``streamable-http`` so hosted clients
(the Codex app, claude.ai) can reach it. Per the MCP authorization spec the
server is an OAuth 2.1 **Resource Server**: it does *not* mint tokens, it
*verifies* bearer tokens minted by a separate authorization server (your IdP)
and advertises that AS via protected-resource metadata. FastMCP serves the
metadata + 401/403s; we supply the ``TokenVerifier``.

Two verifier modes, selected by env (see ``build_auth_from_env``):

- **Introspection** (``ORCHESTRATOR_MCP_INTROSPECTION_URL``) — the real OAuth
  path: every token is checked against the AS's RFC 7662 endpoint.
- **Static shared secret** (``ORCHESTRATOR_MCP_TOKEN``) — one long-lived bearer
  token compared in constant time. Simplest for a single-tenant self-host
  behind TLS; not OAuth, but the same Authorization: Bearer wire shape.

Neither set → no auth settings (the caller decides whether to allow that, and
only ever on loopback).
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from mcp.server.auth.settings import AuthSettings

logger = logging.getLogger("orchestrator.plugin.auth")

_DEFAULT_SCOPES = ["sdlc"]


def _scopes_from_env(value: str | None) -> list[str]:
    """Parse a comma/space-delimited scope list."""
    if not value:
        return list(_DEFAULT_SCOPES)
    return [s for s in value.replace(",", " ").split() if s]


class StaticTokenVerifier:
    """Accept exactly one configured bearer token (constant-time compare).

    For a single-tenant self-host: set ``ORCHESTRATOR_MCP_TOKEN`` and hand the
    same value to the client as its bearer token. No token issuance, rotation,
    or per-user identity — use introspection for that.
    """

    def __init__(self, secret: str, *, scopes: list[str] | None = None) -> None:
        if not secret:
            raise ValueError("StaticTokenVerifier needs a non-empty secret")
        self._secret = secret
        self._scopes = scopes or list(_DEFAULT_SCOPES)

    async def verify_token(self, token: str) -> AccessToken | None:
        from mcp.server.auth.provider import AccessToken

        if not token or not hmac.compare_digest(token, self._secret):
            return None
        return AccessToken(token=token, client_id="static", scopes=list(self._scopes), expires_at=None)


class IntrospectionTokenVerifier:
    """Verify bearer tokens against an OAuth 2.0 introspection endpoint (RFC 7662).

    The orchestrator stays a pure Resource Server: the IdP owns issuance and
    revocation; we ask it ``active?`` on every call. Optional client credentials
    authenticate *us* to the introspection endpoint.
    """

    def __init__(
        self,
        introspection_url: str,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._url = introspection_url
        self._auth = (client_id, client_secret) if client_id and client_secret else None
        self._timeout = timeout

    async def verify_token(self, token: str) -> AccessToken | None:
        import httpx
        from mcp.server.auth.provider import AccessToken

        try:
            async with httpx.AsyncClient(timeout=self._timeout, auth=self._auth) as client:
                resp = await client.post(
                    self._url,
                    data={"token": token, "token_type_hint": "access_token"},
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            logger.warning("plugin.auth.introspection_failed", extra={"error": str(exc)[:200]})
            return None
        if resp.status_code != 200:
            logger.warning("plugin.auth.introspection_status", extra={"status": resp.status_code})
            return None

        data: dict[str, Any] = resp.json()
        if not data.get("active"):
            return None
        exp = data.get("exp")
        if isinstance(exp, (int, float)) and exp < time.time():
            return None
        scopes = _scopes_from_env(data.get("scope")) if data.get("scope") else []
        return AccessToken(
            token=token,
            client_id=str(data.get("client_id", "introspected")),
            scopes=scopes,
            expires_at=int(exp) if isinstance(exp, (int, float)) else None,
            resource=data.get("aud") if isinstance(data.get("aud"), str) else None,
        )


def build_auth_from_env() -> tuple[AuthSettings | None, TokenVerifier | None]:
    """Build ``(AuthSettings, TokenVerifier)`` from env, or ``(None, None)``.

    Introspection wins over the static secret when both are set. ``AuthSettings``
    requires an ``issuer_url`` (the AS); for the static path it defaults to the
    resource URL when not given. Returns ``(None, None)`` when neither verifier
    is configured — an unauthenticated server (loopback only; the runner gates
    public binds).
    """
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    resource_url = os.getenv("ORCHESTRATOR_MCP_RESOURCE_URL")
    issuer_url = os.getenv("ORCHESTRATOR_MCP_ISSUER_URL")
    scopes = _scopes_from_env(os.getenv("ORCHESTRATOR_MCP_REQUIRED_SCOPES"))

    introspection_url = os.getenv("ORCHESTRATOR_MCP_INTROSPECTION_URL")
    static_secret = os.getenv("ORCHESTRATOR_MCP_TOKEN")

    verifier: TokenVerifier | None
    if introspection_url:
        verifier = IntrospectionTokenVerifier(
            introspection_url,
            client_id=os.getenv("ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_ID"),
            client_secret=os.getenv("ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_SECRET"),
        )
    elif static_secret:
        verifier = StaticTokenVerifier(static_secret, scopes=scopes)
    else:
        return None, None

    # issuer_url is required by AuthSettings; fall back to the resource URL for
    # the static (no real AS) path so a self-host needs only one URL.
    effective_issuer = issuer_url or resource_url
    if not effective_issuer:
        raise ValueError(
            "Remote auth needs ORCHESTRATOR_MCP_ISSUER_URL (the OAuth authorization "
            "server) or, for a static token, ORCHESTRATOR_MCP_RESOURCE_URL."
        )

    settings = AuthSettings(
        issuer_url=AnyHttpUrl(effective_issuer),
        resource_server_url=AnyHttpUrl(resource_url) if resource_url else None,
        required_scopes=scopes,
    )
    return settings, verifier


__all__ = [
    "IntrospectionTokenVerifier",
    "StaticTokenVerifier",
    "build_auth_from_env",
]
