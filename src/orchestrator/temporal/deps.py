"""Sprint 13.3: dependencies that orchestrator activities need at execution time.

Activities can't read FastAPI app state, environment globals, or whatever the
HTTP request context happened to carry. Instead, the worker process builds an
``ActivityDeps`` at startup and registers each activity as a bound method.
Every activity invocation reads from this single, frozen container.

Three concerns live here:

  - ``session_factory``: async sessionmaker. Activities open a short-lived
    session per invocation so a failed activity (with retry) doesn't share
    a transaction with the next attempt.
  - ``llm``: the configured LLMClient. Single instance, shared across
    activities; LiteLLM is stateless per-call.
  - ``artifact_store``: ObjectStoreArtifactStore or InMemoryArtifactStore.

Audit-logger callable can't ride here because it's currently tied to a
FastAPI ``Request``. The Temporal path opens a fresh session per audit row
inside each activity — same effect, slightly different plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrator.core.llm import LLMClient
from orchestrator.runtime.artifacts import ArtifactStore


@dataclass(frozen=True)
class ActivityDeps:
    session_factory: async_sessionmaker[AsyncSession]
    llm: LLMClient
    artifact_store: ArtifactStore
    actor: str = "system"
