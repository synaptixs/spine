"""HTTP middleware: trace id propagation and structured access logging.

Per-request audit log writes happen inline inside the handlers in
``routes.py`` — they need handler-specific context (action name,
resource id, before/after payload) that a generic middleware can't
infer. This middleware handles the cross-cutting parts only.

It is a **pure ASGI** middleware (not ``BaseHTTPMiddleware``) so it does not
buffer the response body — which is required for the SSE run-state stream
(``/v1/stream``); ``BaseHTTPMiddleware`` collects/blocks streaming responses.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from starlette.datastructures import MutableHeaders

logger = logging.getLogger("orchestrator.registry.access")

TRACE_HEADER = "X-Trace-Id"

ASGIApp = Any
Scope = dict[str, Any]
Receive = Any
Send = Any


class TraceIdMiddleware:
    """Stamp every request with a trace id and emit one access log line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = _request_header(scope, TRACE_HEADER) or uuid.uuid4().hex
        scope.setdefault("state", {})["trace_id"] = trace_id
        method = scope.get("method", "")
        path = scope.get("path", "")
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[TRACE_HEADER] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            _log(trace_id, method, path, start, 500)
            raise
        _log(trace_id, method, path, start, status)


def _request_header(scope: Scope, name: str) -> str | None:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key == target:
            return str(value.decode("latin-1"))
    return None


def _log(trace_id: str, method: str, path: str, start: float, status: int) -> None:
    logger.info(
        "access",
        extra={
            "trace_id": trace_id,
            "method": method,
            "path": path,
            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            "status": status,
        },
    )
