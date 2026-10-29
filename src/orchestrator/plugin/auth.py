"""Bearer-token auth for the remote (HTTP) plugin server.

Phase C runs the orchestrator plugin over ``streamable-http`` so hosted clients
(the Codex app, claude.ai) can reach it. Per the MCP authorization spec the
server is an OAuth 2.1 **Resource Server**: it does *not* mint tokens, it
*verifies* bearer tokens minted by a separate authorization server (your IdP)
and advertises that AS via protected-resource metadata. FastMCP serves the
metadata + 401/403s; we supply the ``TokenVerifier``.

Three verifier modes, selected by env in this order (see ``build_auth_from_env``):

- **Introspection** (``ORCHESTRATOR_MCP_INTROSPECTION_URL``) — the real OAuth
  path: every token is checked against the AS's RFC 7662 endpoint.
- **Static shared secret** (``ORCHESTRATOR_MCP_TOKEN``) — one long-lived bearer
  token compared in constant time. Simplest for a single-tenant self-host
  behind TLS; not OAuth, but the same Authorization: Bearer wire shape.
- **Company login** (``ORCHESTRATOR_MCP_ISSUER_URL`` alone) — OIDC access tokens checked
  against the provider's published keys, with the person and their groups read from the
  token (``orchestrator.plugin.oidc``). Entra has no introspection endpoint, so this is the
  mode that works with any OIDC provider issuing JWT access tokens (plain Google sign-in
  issues opaque ones and fits no mode). An issuer set beside a static token
  or an introspection URL does not switch to it: those configs keep their meaning.

None set → no auth settings (the caller decides whether to allow that, and
only ever on loopback).

**Scopes follow the tiers.** ``spine:read`` (comprehension and observing a run),
``spine:plan`` (the build document and its approval), ``spine:run`` (anything that
spends money or writes where it cannot be taken back). The SDK checks scopes once,
server-wide, and only for the floor (``spine:read``); the per-tool check is
``plugin.server``'s scope guard, which reads the verified token at call time and
refuses with the scope it needed. What the metadata *advertises* can differ from that floor
(``ORCHESTRATOR_MCP_ADVERTISED_SCOPES``, see ``advertised_scopes_from_env``). (The pre-3.31
``sdlc`` scope was accepted as an alias for all three through 3.31.x and is retired: a token
carrying only ``sdlc`` now fails the floor.)
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from orchestrator.core.secrets import get_secret

if TYPE_CHECKING:
    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from mcp.server.auth.settings import AuthSettings

logger = logging.getLogger("orchestrator.plugin.auth")

SCOPE_READ = "spine:read"
SCOPE_PLAN = "spine:plan"
SCOPE_RUN = "spine:run"
ALL_SCOPES = [SCOPE_READ, SCOPE_PLAN, SCOPE_RUN]

#: What the static token carries when ``ORCHESTRATOR_MCP_REQUIRED_SCOPES`` is unset: every
#: tier, so a single-tenant self-host behaves as it did with the one ``sdlc`` scope.
_DEFAULT_SCOPES = list(ALL_SCOPES)
#: What the SDK requires of every token before any request reaches a tool. The floor is
#: read: a token that cannot comprehend has no business at any tier.
_SERVER_FLOOR = [SCOPE_READ]


def _scopes_from_env(value: str | None) -> list[str]:
    """Parse a comma/space-delimited scope list."""
    if not value:
        return list(_DEFAULT_SCOPES)
    return [s for s in value.replace(",", " ").split() if s]


def advertised_scopes_from_env() -> list[str]:
    """The scopes the protected-resource metadata names; empty means it names the floor.

    A client asks the provider for what ``scopes_supported`` lists. Okta lets the scope carry
    Spine's own name, but Entra reads a bare ``spine:read`` as a Microsoft Graph scope and
    refuses the sign-in (AADSTS9010010: the scope's API differs from ``resource``); it needs its
    own fully qualified name, ``<App ID URI>/Spine.Access``. Advertising that name changes only
    what a client asks for: the token's scopes still come from the role map, and the SDK still
    requires the floor. Unset, empty or only separators means the floor, as before. A scope
    named twice is listed once.
    """
    value = os.getenv("ORCHESTRATOR_MCP_ADVERTISED_SCOPES") or ""
    return list(dict.fromkeys(value.replace(",", " ").split()))


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

    ``ORCHESTRATOR_MCP_REQUIRED_SCOPES`` is what the **static token carries** (default:
    all three tier scopes); a read-only token is that variable set to ``spine:read``.
    The server-wide requirement the SDK enforces is the floor, ``spine:read``; the
    tiers above it are checked per tool by the scope guard.
    """
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    from orchestrator.plugin.oidc import JwksTokenVerifier, jwks_verifier_from_env

    resource_url = os.getenv("ORCHESTRATOR_MCP_RESOURCE_URL")
    issuer_url = os.getenv("ORCHESTRATOR_MCP_ISSUER_URL")
    scopes = _scopes_from_env(os.getenv("ORCHESTRATOR_MCP_REQUIRED_SCOPES"))

    introspection_url = os.getenv("ORCHESTRATOR_MCP_INTROSPECTION_URL")
    static_secret = get_secret("ORCHESTRATOR_MCP_TOKEN")

    verifier: TokenVerifier | None
    if introspection_url:
        verifier = IntrospectionTokenVerifier(
            introspection_url,
            client_id=os.getenv("ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_ID"),
            client_secret=get_secret("ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_SECRET"),
        )
    elif static_secret:
        verifier = StaticTokenVerifier(static_secret, scopes=scopes)
    elif issuer_url:
        verifier = jwks_verifier_from_env()
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

    extra: dict[str, Any] = {}
    if isinstance(verifier, JwksTokenVerifier):
        # The company-login check matches `aud` against its own audience list, which is
        # rarely the resource URL (Entra's is the App ID URI), so the SDK's check stays off.
        extra["validate_token_resource"] = False
    settings = AuthSettings(
        issuer_url=AnyHttpUrl(effective_issuer),
        resource_server_url=AnyHttpUrl(resource_url) if resource_url else None,
        required_scopes=list(_SERVER_FLOOR),
        **extra,
    )
    return settings, verifier


__all__ = [
    "ALL_SCOPES",
    "SCOPE_PLAN",
    "SCOPE_READ",
    "SCOPE_RUN",
    "IntrospectionTokenVerifier",
    "StaticTokenVerifier",
    "advertised_scopes_from_env",
    "build_auth_from_env",
]
