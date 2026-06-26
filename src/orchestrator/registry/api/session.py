"""Signed-cookie web sessions (unified UI — P0b auth).

A dependency-free session token: ``b64url(json payload) + "." + b64url(HMAC-SHA256)``,
verified in constant time, with an expiry baked into the payload. This matches the
project's hand-rolled style (the ``.env`` reader, the SKILL.md frontmatter parser) —
no ``itsdangerous`` / ``SessionMiddleware`` dependency. The cookie carries only the
caller's principal (id, tenant, roles); the signature makes it unforgeable, so the
server trusts it without a server-side session store.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

COOKIE_NAME = "orchestrator_session"
MAX_AGE_SECONDS = 12 * 3600  # a working day; re-login after.


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sig(body: str, secret: str) -> str:
    return _b64e(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def sign_session(payload: dict[str, Any], secret: str, *, now: float | None = None) -> str:
    """A signed token for ``payload`` (an ``exp`` is added) using ``secret``."""
    data = {**payload, "exp": int((now if now is not None else time.time()) + MAX_AGE_SECONDS)}
    body = _b64e(json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_sig(body, secret)}"


def read_session(token: str, secret: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The payload if ``token`` is validly signed by ``secret`` and unexpired, else ``None``."""
    body, _, sig = token.partition(".")
    if not body or not sig or not hmac.compare_digest(sig, _sig(body, secret)):
        return None
    try:
        data = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or float(data.get("exp", 0)) < (now if now is not None else time.time()):
        return None
    return data


__all__ = ["COOKIE_NAME", "MAX_AGE_SECONDS", "read_session", "sign_session"]
