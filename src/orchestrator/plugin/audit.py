"""Per-person audit of the HTTP transport: who called what, as one line per call.

Over stdio a local subprocess acts for one user with that user's ``.env``; nothing to
attribute. Over HTTP several people share one server, so **every** call made with a verified
token — read, plan and run alike, plus every scope denial and every repository refusal — is
recorded against the person the token names (D21, D35). Auditing only the run tier recorded
nothing on a v1 shared server, which serves read and plan only.

What is recorded: the principal (subject, email and issuer when a company login issued the
token, the calling app, the scopes), the tool, its scope, the argument **names** and a digest
of the argument values — never the values, which may carry a spec, a trace, or a secret — and
the outcome (``ok``, ``error``, ``denied`` with the scope it needed, or ``refused`` with the
argument the repository list refused). The email is personal data on purpose: the audit exists
to say who did what.

Where it goes: always one JSON line on the ``orchestrator.plugin.audit`` logger at INFO —
``orchestrator-mcp --http`` writes that logger to stderr as bare JSON, so a container's log is
the audit stream. When ``ORCHESTRATOR_API_URL`` names a registry, the same record is also sent
to its audit log (``POST /v1/audit``); a registry that does not answer costs a WARNING, never
the call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("orchestrator.plugin.audit")

#: The env that names a registry; unset, the log line is the whole record.
REGISTRY_ENV = "ORCHESTRATOR_API_URL"


def arguments_digest(arguments: dict[str, Any]) -> dict[str, Any]:
    """``{"keys": [...], "sha256": "..."}`` — the shape of a call, not its contents."""
    canonical = json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    return {"keys": sorted(arguments), "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def principal_of(token: Any) -> dict[str, Any]:
    """The person and app a verified bearer token names, as the audit line shows them."""
    claims = getattr(token, "claims", None) or {}
    return {
        "client_id": getattr(token, "client_id", None),
        "subject": getattr(token, "subject", None),
        "email": claims.get("email"),
        "issuer": claims.get("iss"),
        "scopes": sorted(getattr(token, "scopes", None) or []),
    }


class _BareJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


#: The attributes every ``LogRecord`` has; anything else on a record came from ``extra=``.
_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class _EventJson(logging.Formatter):
    """One JSON line per event: its name, its level and the fields passed as ``extra``."""

    def format(self, record: logging.LogRecord) -> str:
        extra = {k: v for k, v in record.__dict__.items() if k not in _RECORD_FIELDS}
        return json.dumps({"event": record.getMessage(), "level": record.levelname, **extra}, default=str)


def _attach(target: logging.Logger, formatter: logging.Formatter) -> None:
    for handler in target.handlers:
        if getattr(handler, "_spine_audit", False):
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    handler._spine_audit = True  # type: ignore[attr-defined]
    target.addHandler(handler)
    target.propagate = False  # the root handler would print each line a second time, reformatted


def configure_audit_logging() -> None:
    """Send audit lines to stderr as bare JSON, once. Called by ``orchestrator-mcp --http``.

    Sign-in refusals go the same way: a token refused, or one granting no Spine scope, is a
    security event, and through the root handler only its message printed, never the reason."""
    _attach(logger, _BareJson())
    logger.setLevel(logging.INFO)
    _attach(logging.getLogger("orchestrator.plugin.oidc"), _EventJson())


async def record_invocation(
    *,
    token: Any,
    tool: str,
    scope: str,
    arguments: dict[str, Any],
    outcome: str,
    denied_scope: str | None = None,
    refused_argument: str | None = None,
) -> bool:
    """Record one call, denial or refusal against the token's person. Returns whether a
    registry took it; ``False`` means the log line is the record. Never raises."""
    payload: dict[str, Any] = {
        "principal": principal_of(token),
        "tool": tool,
        "scope": scope,
        "arguments": arguments_digest(arguments),
        "outcome": outcome,
    }
    if denied_scope:
        payload["denied_scope"] = denied_scope
    if refused_argument:
        payload["refused_argument"] = refused_argument
    action = (
        "mcp_scope_denied" if denied_scope else "mcp_repo_refused" if refused_argument else "mcp_tool_invoked"
    )
    logger.info(json.dumps({"event": action, **payload}, sort_keys=True, default=str))
    if not os.getenv(REGISTRY_ENV):
        return False
    try:
        from orchestrator.plugin.registry_client import registry_client

        client = registry_client()
        try:
            await client.audit(action=action, resource_type="mcp_tool", resource_id=tool, after=payload)
            return True
        finally:
            await client.aclose()
    except Exception as exc:  # unreachable, refused — the log line already holds the record
        logger.warning("plugin.audit.registry_failed %s", action, extra={"registry_error": str(exc)[:200]})
        return False


__all__ = ["REGISTRY_ENV", "arguments_digest", "configure_audit_logging", "principal_of", "record_invocation"]
