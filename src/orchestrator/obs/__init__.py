"""Observability — live tracing over the existing ``trace_id`` (G16).

See ``docs/specs/live-observability-otel.md``. ``tracing`` is a no-op unless
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, so the default path pays nothing.
"""
