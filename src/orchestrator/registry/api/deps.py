"""FastAPI dependencies: session, auth, request context."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrator.registry.api.config import Settings, get_settings  # noqa: F401


@dataclass(frozen=True)
class Principal:
    """The authenticated caller (Bet 2c-ii): a stable id, a tenant, and roles.

    ``id`` is recorded as the actor / ``decided_by``; ``tenant_id`` scopes runs,
    approvals, and audit; ``roles`` gates who may decide an approval. The
    sentinel role ``"*"`` satisfies every role check (the single-key default).
    """

    id: str
    tenant_id: str = "default"
    roles: frozenset[str] = field(default_factory=frozenset)

    def has_role(self, *needed: str) -> bool:
        """True if this principal may act in any of ``needed`` roles. ``"*"`` in
        the principal's roles is a wildcard; ``"any"`` among ``needed`` means the
        approval named no specific role, so any authenticated caller qualifies."""
        if "*" in self.roles or "any" in needed:
            return True
        return any(r in self.roles for r in needed)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


def resolve_principal_from_key(api_key: str | None, settings: Settings) -> Principal | None:
    """The ``Principal`` for ``api_key`` under ``settings``, or ``None`` if invalid.

    Two modes: a ``principals`` map (per-key tenant + roles) when set, else the
    single ``api_key`` → a wildcard principal in tenant ``"default"`` (today's
    single-tenant, everyone-can-approve default)."""
    principals: dict[str, dict[str, object]] = getattr(settings, "principals", {}) or {}
    if principals:
        spec = principals.get(api_key) if api_key else None
        if not spec:
            return None
        raw_roles = spec.get("roles") or []
        roles = raw_roles if isinstance(raw_roles, (list, tuple, set, frozenset)) else []
        return Principal(
            id=str(spec.get("id") or api_key),
            tenant_id=str(spec.get("tenant_id") or "default"),
            roles=frozenset(str(r) for r in roles),
        )
    if not api_key or api_key != settings.api_key:
        return None
    return Principal(id=api_key, tenant_id="default", roles=frozenset({"*"}))


def principal_from_session(request: Request) -> Principal | None:
    """The ``Principal`` carried by a valid web-session cookie, or ``None`` (P0b)."""
    from orchestrator.registry.api.session import COOKIE_NAME, read_session

    cookies = getattr(request, "cookies", None) or {}
    token = cookies.get(COOKIE_NAME)
    if not token:
        return None
    data = read_session(token, request.app.state.settings.session_secret)
    if not data or not data.get("id"):
        return None
    roles = data.get("roles") or []
    roles = roles if isinstance(roles, (list, tuple, set, frozenset)) else []
    return Principal(
        id=str(data["id"]),
        tenant_id=str(data.get("tenant_id") or "default"),
        roles=frozenset(str(r) for r in roles),
    )


def require_principal(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    """Resolve the caller's ``Principal`` from the X-API-Key header **or** a web
    session cookie (P0b). The key path serves the CLI / programmatic callers; the
    session path serves same-origin browser fetches from the authed web pages.
    Settings are read off ``request.app.state.settings`` so a test ``Settings``
    overrides without monkeypatching.
    """
    principal = resolve_principal_from_key(x_api_key, request.app.state.settings)
    if principal is None:
        principal = principal_from_session(request)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key or session.",
        )
    return principal


def require_api_key(principal: Annotated[Principal, Depends(require_principal)]) -> str:
    """Back-compat shim: endpoints that only need the actor string get the
    principal's stable id (was the raw API key)."""
    return principal.id


def request_trace_id(request: Request) -> str:
    """Return the trace id stamped on the request by ``TraceIdMiddleware``.

    Falls back to a fresh UUID when the middleware isn't installed (e.g.,
    direct dependency invocation from unit tests).
    """
    return getattr(request.state, "trace_id", None) or uuid.uuid4().hex


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ApiKeyDep = Annotated[str, Depends(require_api_key)]
PrincipalDep = Annotated[Principal, Depends(require_principal)]
TraceIdDep = Annotated[str, Depends(request_trace_id)]
