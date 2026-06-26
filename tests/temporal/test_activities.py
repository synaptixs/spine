"""Unit tests for Temporal activities.

Uses Temporal's ``ActivityEnvironment`` to invoke activity methods without
spinning up a worker or hitting the server. Activities still need real
deps (session factory, LLM, artifact store) — but we hand them stub
implementations rather than the full SQLAlchemy machinery.
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.runtime.artifacts import InMemoryArtifactStore
from orchestrator.runtime.task_orchestration import TaskOrchestrationError
from orchestrator.temporal.activities import Activities
from orchestrator.temporal.deps import ActivityDeps


class _StubSession:
    """Minimum session surface: ``async with`` + ``execute`` + ``commit``."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    async def __aenter__(self) -> _StubSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, stmt: object) -> _StubResult:
        _ = stmt
        return _StubResult(self._rows)

    async def commit(self) -> None:
        return None


class _StubResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _StubResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def one_or_none(self) -> Any:
        class _Agg:
            n = 0
            mean_conf = None
            passes = 0

        return _Agg()


def _stub_session_factory(rows: list[Any] | None = None) -> Any:
    """Return something that quacks like ``async_sessionmaker``."""

    def factory() -> _StubSession:
        return _StubSession(rows)

    return factory


def _llm_returning(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        _ = messages, kwargs
        return CompletionResult(
            text=text,
            model="claude-opus-4-7",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


def _deps_with(llm: MockLLMClient, rows: list[Any] | None = None) -> ActivityDeps:
    return ActivityDeps(
        session_factory=_stub_session_factory(rows),
        llm=llm,
        artifact_store=InMemoryArtifactStore(),
    )


async def test_plan_initial_ir_returns_a_serialised_ir() -> None:
    """Planner activity runs end-to-end against a stub session and returns a
    JSON-serialisable IR dump."""

    class _Row:
        def __init__(self) -> None:
            self.id = "agent.x"
            self.version = "0.1.0"
            self.description = "x"
            self.tags: list[str] = []
            self.status = "published"
            self.spec_json = {
                "inputs": [{"name": "topic", "type": "str"}],
                "outputs": [{"name": "findings", "type": "str"}],
                "known_limitations": [],
            }

    llm = _llm_returning('{"pattern": "single_agent", "template_id": "agent.x", "template_version": "0.1.0"}')
    acts = Activities(_deps_with(llm, rows=[_Row()]))
    ir_dump = await acts.plan_initial_ir({"objective": "Test objective", "glossary": {}})

    assert ir_dump["spec"]["workflow_pattern"] == "single_agent"
    assert ir_dump["spec"]["nodes"][0]["template_id"] == "agent.x"


async def test_plan_initial_ir_raises_when_planner_fails() -> None:
    """No published templates → planner raises → activity surfaces a
    ``TaskOrchestrationError`` Temporal can wrap."""
    llm = _llm_returning("{}")
    acts = Activities(_deps_with(llm, rows=[]))
    with pytest.raises(TaskOrchestrationError):
        await acts.plan_initial_ir({"objective": "x"})
