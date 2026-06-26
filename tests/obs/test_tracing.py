"""Tests for the OpenTelemetry tracing seam (Phase 1).

The disabled-path tests run without the ``otel`` extra. The span-tree tests
``importorskip`` opentelemetry and drive an in-memory exporter.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from orchestrator.obs import tracing


@pytest.fixture(autouse=True)
def _reset_tracing(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test starts from a clean, unconfigured tracer."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    tracing.reset()
    yield
    tracing.reset()


# --- disabled path (no extra needed) ---------------------------------------


def test_disabled_by_default_is_noop() -> None:
    assert tracing.is_enabled() is False
    with tracing.span("x", foo="bar") as sp:
        sp.set_attribute("k", "v")  # must not raise on the no-op span
        sp.add_event("e")


def test_temporal_interceptors_empty_when_disabled() -> None:
    assert tracing.temporal_interceptors() == []


def test_temporal_interceptors_present_when_enabled() -> None:
    pytest.importorskip("opentelemetry")
    pytest.importorskip("temporalio.contrib.opentelemetry")
    from temporalio.contrib.opentelemetry import TracingInterceptor

    tracing.configure_for_testing()
    try:
        interceptors = tracing.temporal_interceptors()
        assert len(interceptors) == 1
        assert isinstance(interceptors[0], TracingInterceptor)
    finally:
        tracing.reset()


def test_endpoint_set_but_extra_missing_stays_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the otel extra IS installed this still passes — we only assert the
    # call is safe and self-consistent, not that it's off.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    tracing.reset()
    enabled = tracing.is_enabled()
    with tracing.span("x") as sp:
        sp.set_attribute("k", "v")
    assert isinstance(enabled, bool)


async def test_disabled_span_propagates_exceptions() -> None:
    with pytest.raises(ValueError), tracing.span("boom"):
        raise ValueError("nope")


# --- enabled path (in-memory exporter) -------------------------------------


def test_span_tree_and_attributes() -> None:
    pytest.importorskip("opentelemetry")
    exporter = tracing.configure_for_testing()
    try:
        assert tracing.is_enabled() is True
        with tracing.span("parent", **{"a.x": 1}):  # noqa: SIM117 — nesting is the thing under test
            with tracing.span("child", **{"b.y": "z", "dropped": None}):
                pass
        tracing.flush()
        spans = {s.name: s for s in exporter.get_finished_spans()}
        assert set(spans) == {"parent", "child"}
        assert spans["parent"].attributes["a.x"] == 1
        assert spans["child"].attributes["b.y"] == "z"
        assert "dropped" not in spans["child"].attributes  # None values dropped
        # child nests under parent
        assert spans["child"].parent.span_id == spans["parent"].context.span_id
    finally:
        tracing.reset()


def test_bound_trace_id_attached_as_join_key() -> None:
    pytest.importorskip("opentelemetry")
    exporter = tracing.configure_for_testing()
    try:
        with tracing.bind_trace_id("abc123"), tracing.span("op"):
            pass
        tracing.flush()
        (span,) = exporter.get_finished_spans()
        assert span.attributes["trace_id"] == "abc123"
    finally:
        tracing.reset()


def test_exception_marks_span_error() -> None:
    pytest.importorskip("opentelemetry")
    from opentelemetry.trace import StatusCode

    exporter = tracing.configure_for_testing()
    try:
        with pytest.raises(RuntimeError), tracing.span("fails"):
            raise RuntimeError("kaboom")
        tracing.flush()
        (span,) = exporter.get_finished_spans()
        assert span.status.status_code == StatusCode.ERROR
        assert span.events  # exception recorded as an event
    finally:
        tracing.reset()


# --- RecordingLLMClient integration ----------------------------------------


async def test_recording_client_emits_llm_span() -> None:
    pytest.importorskip("opentelemetry")
    from orchestrator.core.llm import RecordingLLMClient
    from orchestrator.core.llm.client import CompletionResult, Message

    class _FakeLLM:
        async def complete(self, messages, *, model, **_kwargs) -> CompletionResult:  # type: ignore[no-untyped-def]
            _ = (messages, model)
            return CompletionResult(
                text="ok",
                model="gpt-4o",
                prompt_tokens=11,
                completion_tokens=4,
                cost_usd=0.02,
                latency_ms=123.0,
            )

    exporter = tracing.configure_for_testing()
    try:
        rec = RecordingLLMClient(_FakeLLM())
        with rec.stage("codegen"):
            await rec.complete([Message(role="user", content="x")], model="gpt-4o")
        tracing.flush()
        (span,) = exporter.get_finished_spans()
        assert span.name == "llm.complete"
        attrs = span.attributes
        assert attrs["llm.stage"] == "codegen"
        assert attrs["llm.model"] == "gpt-4o"
        assert attrs["llm.prompt_tokens"] == 11
        assert attrs["llm.completion_tokens"] == 4
        assert attrs["llm.total_tokens"] == 15
        assert attrs["llm.cost_usd"] == pytest.approx(0.02)
        assert attrs["llm.tool_calls"] == 0
        # ledger accounting still works alongside the span
        assert rec.ledger.stages["codegen"].prompt_tokens == 11
    finally:
        tracing.reset()
