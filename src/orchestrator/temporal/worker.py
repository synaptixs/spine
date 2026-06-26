"""Sprint 13.4: orchestrator-worker — the long-lived Temporal worker process.

Run with:

    uv run python -m orchestrator.temporal.worker

Connects to the Temporal frontend (auto-detected: local docker-compose
unless ``TEMPORAL_API_KEY`` is set, then cloud), subscribes to the
configured task queue, and dispatches activities + workflows.

The worker holds the side-effecting deps: a SQLAlchemy session factory,
the configured LLM client, and the artifact store. Activities are
registered as bound methods of an ``Activities`` instance so each
activity invocation closes over those deps without reaching for globals.

Health-check + graceful-shutdown are deliberately simple: SIGINT / SIGTERM
flag the run loop, the worker stops accepting new tasks, in-flight
activities finish under their own timeouts, and the process exits cleanly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.worker import Worker

from orchestrator.core.llm import LiteLLMClient, LLMClient
from orchestrator.runtime import InMemoryArtifactStore, ObjectStoreArtifactStore
from orchestrator.runtime.artifacts import ArtifactStore
from orchestrator.temporal.activities import Activities
from orchestrator.temporal.config import TemporalConfig, connect_client
from orchestrator.temporal.deps import ActivityDeps
from orchestrator.temporal.workflow import ApprovalTimeoutSweepWorkflow, OrchestratorWorkflow

logger = logging.getLogger("orchestrator.temporal.worker")


def _default_database_url() -> str:
    """Worker reads the same env var as the FastAPI app for the orchestrator DB."""
    return os.getenv(
        "ORCHESTRATOR_DATABASE_URL",
        "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
    )


def _build_artifact_store() -> ArtifactStore:
    """Pick between MinIO/S3 (default) and in-memory (test/CI hint via env)."""
    if os.getenv("ORCHESTRATOR_ARTIFACT_STORE", "").lower() == "memory":
        return InMemoryArtifactStore()
    return ObjectStoreArtifactStore()


def _build_llm_client() -> LLMClient:
    """Default to LiteLLM. Tests inject their own MockLLMClient by importing
    the worker module and rebuilding deps."""
    return LiteLLMClient()


def build_deps() -> ActivityDeps:
    """Wire up the worker-side dependencies. Exposed so tests can swap parts."""
    engine = create_async_engine(_default_database_url(), future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return ActivityDeps(
        session_factory=factory,
        llm=_build_llm_client(),
        artifact_store=_build_artifact_store(),
    )


async def run_worker(
    deps: ActivityDeps | None = None,
    config: TemporalConfig | None = None,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the worker until ``stop_event`` is set (or forever if None).

    Returns when the worker has drained in-flight activities and shut down
    cleanly. Designed so tests can drive a worker with a short lifetime.
    """
    deps = deps or build_deps()
    cfg = config or TemporalConfig.from_env()
    client = await connect_client(cfg)
    activities_instance = Activities(deps)

    activity_methods: list[Any] = [
        activities_instance.plan_initial_ir,
        activities_instance.validate_ir,
        activities_instance.execute_graph_pass,
        activities_instance.replan_ir,
        activities_instance.raise_approval_request,
        activities_instance.find_timed_out_approvals,
        activities_instance.expire_approval,
        activities_instance.signal_task_workflow,
        activities_instance.record_audit,
    ]

    worker = Worker(
        client,
        task_queue=cfg.task_queue,
        workflows=[OrchestratorWorkflow, ApprovalTimeoutSweepWorkflow],
        activities=activity_methods,
        # Tracing interceptor is NOT added here: the worker already applies the
        # interceptors on its client (added in connect_client), so registering it
        # again would double every span (Phase 3, verified live).
    )
    logger.info(
        "temporal.worker.start",
        extra={"host": cfg.host, "namespace": cfg.namespace, "task_queue": cfg.task_queue},
    )

    if stop_event is None:
        # Run until externally interrupted via SIGINT/SIGTERM (handled below).
        await worker.run()
        return

    # Run side-by-side with the stop signal. Whichever finishes first wins;
    # the worker drains gracefully on shutdown.
    worker_task = asyncio.create_task(worker.run())
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait({worker_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        await worker.shutdown()
        for task in pending:
            task.cancel()
    for task in done:
        # Re-raise worker exceptions if the run itself failed.
        if task is worker_task:
            task.result()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Make SIGINT/SIGTERM set the stop_event instead of killing the loop."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows test envs don't support add_signal_handler; ctrl-c still
        # interrupts via the default handler, so suppress and move on.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    await run_worker(stop_event=stop_event)


if __name__ == "__main__":
    asyncio.run(_main())
