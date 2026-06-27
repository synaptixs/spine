"""Live OpenTelemetry tracing — the live lens over runs (Phase 1).

A thin seam over ``opentelemetry-sdk`` that stays a **no-op until configured**:
with no ``OTEL_EXPORTER_OTLP_ENDPOINT`` set (or the optional ``otel`` extra
uninstalled) ``span()`` yields a do-nothing span and the default path pays
nothing. When an endpoint is set, spans export over OTLP/HTTP.

The whole design is dual-sink (``docs/specs/live-observability-otel.md``): the
append-only audit log stays the forensic record; these spans are the live view.
They **join on the existing ``trace_id``** — bind it once with ``bind_trace_id``
and every span underneath carries it as an attribute, so a span in the tracing
UI maps back to the exact ``AuditLogRow`` and run bundle.

Phase 1 instruments the LLM chokepoint (``RecordingLLMClient``). Layers 1 and 3
(the agentic loop and Temporal activities) come later over this same seam.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import logging
import os
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger("orchestrator.obs.tracing")

_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_SERVICE_NAME = "synaptixs-spine"

# The current run's correlation id. Bind it (``bind_trace_id``) at a request /
# workflow boundary; ``span()`` attaches it to every span underneath so spans
# and audit rows share one join key.
current_trace_id: ContextVar[str | None] = ContextVar("obs_current_trace_id", default=None)


class _State:
    """Module-global tracer state. ``tracer is None`` means tracing is off."""

    def __init__(self) -> None:
        self.configured = False
        self.tracer: Any | None = None
        self.provider: Any | None = None


_state = _State()


class _NoopSpan:
    """Stand-in returned by ``span()`` when tracing is disabled."""

    def set_attribute(self, *_a: Any, **_k: Any) -> None: ...
    def add_event(self, *_a: Any, **_k: Any) -> None: ...
    def record_exception(self, *_a: Any, **_k: Any) -> None: ...
    def set_status(self, *_a: Any, **_k: Any) -> None: ...


def _ensure_configured() -> None:
    """Lazily build the tracer the first time anyone traces.

    No endpoint → stays disabled (no-op). Endpoint set but the ``otel`` extra
    missing → warn once and stay disabled. Idempotent.
    """
    if _state.configured:
        return
    _state.configured = True  # set first so a failed import doesn't retry every call

    endpoint = os.getenv(_ENDPOINT_ENV)
    if not endpoint:
        return  # disabled by default

    try:
        # import-not-found when the `otel` extra is absent; unused-ignore when it
        # is present — tolerate both so this type-checks with or without the extra.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found,unused-ignore]
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:  # pragma: no cover - import guard
        logger.warning(
            "obs.tracing: %s is set but the 'otel' extra is not installed; tracing disabled "
            "(pip install 'synaptixs-spine[otel]')",
            _ENDPOINT_ENV,
        )
        return

    # Construct the exporter with no explicit endpoint so it follows OTel's
    # standard env resolution: it reads OTEL_EXPORTER_OTLP_TRACES_ENDPOINT, or
    # OTEL_EXPORTER_OTLP_ENDPOINT with the "/v1/traces" path appended (plus any
    # OTEL_EXPORTER_OTLP_HEADERS). Passing endpoint= verbatim would skip the
    # signal-path append and POST to the wrong URL.
    provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    _state.provider = provider
    _state.tracer = provider.get_tracer("orchestrator")
    logger.info("obs.tracing: enabled, exporting via OTLP to %s/v1/traces", endpoint.rstrip("/"))


def is_enabled() -> bool:
    """True when spans are actually recorded (endpoint set + extra installed)."""
    _ensure_configured()
    return _state.tracer is not None


def get_tracer() -> Any | None:
    """The configured tracer, or ``None`` when tracing is disabled."""
    _ensure_configured()
    return _state.tracer


def temporal_interceptors() -> list[Any]:
    """Temporal client/worker interceptors for the cross-process trace (Phase 3).

    Returns ``[TracingInterceptor]`` bound to our tracer so a workflow's spans
    (and the activity spans, and everything nested under them — ``agent.step``,
    ``llm.complete``, ``tool.<name>``) join one trace propagated over W3C trace
    context. Empty list — a true no-op — when tracing is off or the deps are
    missing, so the worker/client wiring is unconditional and safe.
    """
    tracer = get_tracer()
    if tracer is None:
        return []
    try:
        from temporalio.contrib.opentelemetry import TracingInterceptor
    except Exception:  # pragma: no cover - import guard
        logger.warning("obs.tracing: temporalio OTel contrib unavailable; Temporal tracing disabled")
        return []
    return [TracingInterceptor(tracer)]


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Open a span ``name`` with ``attributes``; no-op when tracing is disabled.

    The bound ``trace_id`` (if any) is attached automatically. ``None`` valued
    attributes are dropped (OTel rejects ``None``). On an exception the span is
    marked errored and the exception recorded, then re-raised.
    """
    _ensure_configured()
    tracer = _state.tracer
    if tracer is None:
        yield _NoopSpan()
        return

    with tracer.start_as_current_span(name) as otel_span:
        tid = current_trace_id.get()
        if tid:
            otel_span.set_attribute("trace_id", tid)
        for key, value in attributes.items():
            if value is not None:
                otel_span.set_attribute(key, value)
        try:
            yield otel_span
        except Exception as exc:
            from opentelemetry.trace import Status, StatusCode

            otel_span.record_exception(exc)
            otel_span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def add_event(name: str, **attributes: Any) -> None:
    """Attach an event to the currently-active span; no-op when tracing is off.

    Used for in-step moments worth seeing without their own span — e.g. a policy
    block or a needs-approval pause inside ``agent.step``. ``None`` valued
    attributes are dropped.
    """
    _ensure_configured()
    if _state.tracer is None:
        return
    from opentelemetry import trace

    span_ = trace.get_current_span()
    span_.add_event(name, attributes={k: v for k, v in attributes.items() if v is not None})


def traced(name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator wrapping a sync or async function in a span."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or fn.__qualname__
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                with span(span_name):
                    return await fn(*args, **kwargs)

            return awrapper

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with span(span_name):
                return fn(*args, **kwargs)

        return wrapper

    return decorate


@contextlib.contextmanager
def bind_trace_id(trace_id: str | None) -> Iterator[None]:
    """Bind ``trace_id`` so spans opened in this scope carry it as the join key."""
    token = current_trace_id.set(trace_id)
    try:
        yield
    finally:
        current_trace_id.reset(token)


def flush() -> None:
    """Force-export buffered spans (useful in tests and on shutdown)."""
    if _state.provider is not None:
        _state.provider.force_flush()


def configure_for_testing() -> Any:
    """Wire an in-memory exporter and return it; for tests only.

    Returns the ``InMemorySpanExporter`` so a test can assert on
    ``exporter.get_finished_spans()``. Uses a synchronous processor so spans
    are visible immediately. Call ``reset()`` in teardown.
    """
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _state.configured = True
    _state.provider = provider
    _state.tracer = provider.get_tracer("orchestrator-test")
    return exporter


def reset() -> None:
    """Tear down tracer state so the next use reconfigures from the environment."""
    _state.configured = False
    _state.tracer = None
    _state.provider = None


__all__ = [
    "add_event",
    "bind_trace_id",
    "configure_for_testing",
    "current_trace_id",
    "flush",
    "get_tracer",
    "is_enabled",
    "reset",
    "span",
    "temporal_interceptors",
    "traced",
]
