"""Per-principal audit of the HTTP transport: who called what, recorded where the registry
already keeps history.

Over stdio a local subprocess acts for one user with that user's ``.env``; nothing to
attribute. Over HTTP several people may share one server, and the run-scope tools spend
money and write where it cannot be taken back — so every ``spine:run`` call, and every
scope denial, is recorded **against the token's principal**, in the registry's audit log,
through ``POST /v1/audit``. The plugin process keeps no database credentials (#321), so the
registry is the one writer, and the actor is whatever principal the registry resolves for
the API key the plugin holds — with the *token's* principal in the row's payload.

What is recorded: the principal (client id, subject when the IdP issued one), the tool, its
scope, the argument **names** and a digest of the argument values — never the values, which
may carry a spec, a trace, or a secret — and the outcome (``ok``, ``error``, or the denied
scope). A registry that is unreachable or unconfigured degrades to a structured log line
with the same fields; the audit must never be the thing that fails a call.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger("orchestrator.plugin.audit")

#: Scopes whose calls are recorded. Read-scope calls are cheap and numerous; the run tier is
#: where money is spent and external state changes.
AUDITED_SCOPES: frozenset[str] = frozenset({"spine:run"})


def arguments_digest(arguments: dict[str, Any]) -> dict[str, Any]:
    """``{"keys": [...], "sha256": "..."}`` — the shape of a call, not its contents."""
    canonical = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    return {"keys": sorted(arguments), "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def principal_of(token: Any) -> dict[str, Any]:
    """The identity a verified bearer token carries, as the row's payload shows it."""
    return {
        "client_id": getattr(token, "client_id", None),
        "subject": getattr(token, "subject", None),
        "scopes": sorted(getattr(token, "scopes", None) or []),
    }


async def record_invocation(
    *,
    token: Any,
    tool: str,
    scope: str,
    arguments: dict[str, Any],
    outcome: str,
    denied_scope: str | None = None,
) -> bool:
    """Record one call (or denial) against the token's principal. Returns whether the registry
    took it; ``False`` means it went to the log instead. Never raises."""
    payload: dict[str, Any] = {
        "principal": principal_of(token),
        "tool": tool,
        "scope": scope,
        "arguments": arguments_digest(arguments),
        "outcome": outcome,
    }
    if denied_scope:
        payload["denied_scope"] = denied_scope
    action = "mcp_scope_denied" if denied_scope else "mcp_tool_invoked"
    try:
        from orchestrator.plugin.registry_client import registry_client

        client = registry_client()
        try:
            await client.audit(action=action, resource_type="mcp_tool", resource_id=tool, after=payload)
            return True
        finally:
            await client.aclose()
    except Exception as exc:  # unreachable, unconfigured, refused — the log keeps the row
        logger.warning("plugin.audit.%s", action, extra={**payload, "registry_error": str(exc)[:200]})
        return False


__all__ = ["AUDITED_SCOPES", "arguments_digest", "principal_of", "record_invocation"]
