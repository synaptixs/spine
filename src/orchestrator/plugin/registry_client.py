"""HTTP client for the registry ``/v1`` API, used by the plugin's operator tools.

The ``registry_*`` tools are the successor to the terminal UI removed in #316: an
assistant asks "what is running, what is waiting on me", and decides a gate. They
go **over HTTP to the registry** rather than in-process through Temporal like the
``sdlc_*`` run tools, and that is a choice, not an accident: the plugin process
then needs only ``ORCHESTRATOR_API_URL`` and ``ORCHESTRATOR_API_KEY`` — no database,
no Temporal — the registry enforces tenant scoping, and the audit log records the
API-key principal as the actor, exactly as it does for the web inbox.

A thin async wrapper over httpx, unit-testable with an httpx ``MockTransport``.
Tools obtain one through :func:`registry_client` so a test can swap the factory
without a server.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from orchestrator.core.secrets import get_secret

DEFAULT_URL = "http://localhost:8000"
DEFAULT_KEY = "dev-key"

#: What a failed call tells the assistant to do about it.
UNREACHABLE_HINT = (
    "The registry did not answer. Start it with `orchestrator up`, or point "
    "ORCHESTRATOR_API_URL at a running one (and ORCHESTRATOR_API_KEY at its key)."
)


class RegistryError(RuntimeError):
    """A registry call that did not succeed, with a hint the tool can pass on."""

    def __init__(self, message: str, *, hint: str = UNREACHABLE_HINT) -> None:
        super().__init__(message)
        self.hint = hint


class RegistryClient:
    """Talks to the registry API as the operator (``X-API-Key`` auth)."""

    def __init__(
        self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-API-Key": api_key},
            transport=transport,
            timeout=15.0,
        )

    async def runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return await self._items("/v1/runs", params={"limit": limit})

    async def approvals(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return await self._items("/v1/approvals", params={"limit": limit})

    async def approval(self, approval_id: str) -> dict[str, Any]:
        return await self._json("GET", f"/v1/approvals/{approval_id}")

    async def decide(
        self,
        approval_id: str,
        action: str,
        *,
        rationale: str | None = None,
        modified_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if rationale:
            body["rationale"] = rationale
        if modified_input is not None:
            body["modified_input"] = modified_input
        return await self._json("POST", f"/v1/approvals/{approval_id}/{action}", json=body)

    async def trace(self, task_id: str) -> dict[str, Any]:
        return await self._json("GET", f"/v1/tasks/{task_id}/trace")

    async def audit(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        after: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Record one row as this key's principal (``POST /v1/audit``). Short timeout: an audit
        write must not add a registry's worth of latency to the tool it describes."""
        body: dict[str, Any] = {"action": action, "resource_type": resource_type, "resource_id": resource_id}
        if after is not None:
            body["after"] = after
        return await self._json("POST", "/v1/audit", json=body, timeout=timeout)

    async def _items(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        data = await self._json("GET", path, params=params)
        return list(data.get("items", []))

    async def _json(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            resp = await self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:  # connect refused, timeout, DNS — the server is not there
            raise RegistryError(f"{method} {self.base_url}{path}: {exc.__class__.__name__}: {exc}") from exc
        if resp.status_code >= 400:
            hint = UNREACHABLE_HINT
            if resp.status_code in (401, 403):
                hint = "The registry refused the key. Check ORCHESTRATOR_API_KEY matches the server's."
            elif resp.status_code == 404:
                hint = "No such id at this registry — list first (registry_runs / registry_approvals)."
            elif resp.status_code < 500:
                hint = "The registry rejected the request; the detail says what it wanted."
            raise RegistryError(f"{method} {path} → {resp.status_code}: {_detail(resp)}", hint=hint)
        return dict(resp.json())

    async def aclose(self) -> None:
        await self._client.aclose()


def _detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200]
    return str(data.get("detail", data))[:200] if isinstance(data, dict) else str(data)[:200]


def registry_client() -> RegistryClient:
    """The client the tools use, from the same env every other Spine client reads.

    Module-level and looked up at call time (``registry_client()``, not a bound
    default) so ``tests/plugin`` can replace it with one over a ``MockTransport``.
    """
    return RegistryClient(
        os.getenv("ORCHESTRATOR_API_URL", DEFAULT_URL),
        get_secret("ORCHESTRATOR_API_KEY") or DEFAULT_KEY,
    )


ClientFactory = Callable[[], RegistryClient]

__all__ = [
    "DEFAULT_KEY",
    "DEFAULT_URL",
    "UNREACHABLE_HINT",
    "ClientFactory",
    "RegistryClient",
    "RegistryError",
    "registry_client",
]
