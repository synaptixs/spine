"""Block C: the SDLC Temporal worker.

Run with:

    uv run python -m orchestrator.sdlc.worker

Subscribes to its **own** task queue (``SDLC_TASK_QUEUE``, default
``sdlc-tasks``) so it stays isolated from the existing ``orchestrator-tasks``
worker — independent scaling, no risk to that worker. Registers
``SDLCWorkflow`` + ``FeatureImplementationWorkflow`` and the ``SDLCActivities``
methods, holding the side-effecting deps (DB session factory, workspace
manager, adapters). Reuses ``connect_client`` / ``TemporalConfig`` for
the connection and the same SIGINT/SIGTERM graceful-shutdown shape as the
orchestrator worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import os
import signal
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.worker import Worker

from orchestrator.core.llm import BudgetedLLMClient, LLMClient, RunBudget
from orchestrator.runtime import artifact_store_from_env
from orchestrator.sdlc.activities import SDLCActivities
from orchestrator.sdlc.ci import CIAdapter, GHACIAdapter, StubCIAdapter
from orchestrator.sdlc.codegen import (
    CodegenAdapter,
    LLMCodegenAdapter,
    StubCodegenAdapter,
    resolve_codegen_model,
)
from orchestrator.sdlc.deps import SDLCDeps
from orchestrator.sdlc.grounding import PKGCodegenGrounder
from orchestrator.sdlc.preflight import SubprocessPreflightRunner
from orchestrator.sdlc.review import ReviewAdapter, SemanticReviewAdapter, StubReviewAdapter
from orchestrator.sdlc.testrunner import SubprocessTestRunner
from orchestrator.sdlc.workflows import FeatureImplementationWorkflow, SDLCWorkflow
from orchestrator.sdlc.workspace import WorkspaceManager
from orchestrator.temporal.config import TemporalConfig, connect_client

logger = logging.getLogger("orchestrator.sdlc.worker")


def _default_database_url() -> str:
    return os.getenv(
        "ORCHESTRATOR_DATABASE_URL",
        "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
    )


def _default_workspace_root() -> Path:
    return Path(os.getenv("SDLC_WORKSPACE_ROOT", "/tmp/sdlc-workspaces"))


def sdlc_task_queue() -> str:
    """Own queue, default ``sdlc-tasks`` (overridable via ``SDLC_TASK_QUEUE``)."""
    return os.getenv("SDLC_TASK_QUEUE", "sdlc-tasks")


def build_run_budget() -> RunBudget:
    """Per-run LLM spend cap (G9), from ``SDLC_RUN_BUDGET_USD``.

    Defaults to $25 per run — generous for a normal feature, fatal for a
    runaway loop (run #6's credit burn is the motivating case). Set ``0`` to
    disable enforcement (spend is still tracked).
    """
    return RunBudget(max_cost_usd=float(os.getenv("SDLC_RUN_BUDGET_USD", "25")))


def _build_llm(budget: RunBudget) -> LLMClient:
    """One shared, budget-enforced LLM client for every LLM-backed adapter."""
    from orchestrator.core.llm import LiteLLMClient

    return BudgetedLLMClient(LiteLLMClient(), budget)


def _build_codegen(
    llm: Callable[[], LLMClient],
    *,
    memory_factory: Any = None,
    memory_repo_key: str | None = None,
) -> CodegenAdapter:
    """Select the codegen adapter from the environment.

    Default is the deterministic stub (no creds needed). ``SDLC_CODEGEN=llm``
    swaps in the real LLM-backed adapter — it needs the same provider key the
    intake pipeline uses. The model follows ``resolve_codegen_model``:
    ``SDLC_CODEGEN_MODEL`` overrides, else it inherits ``ORCHESTRATOR_INTAKE_MODEL``
    so one model setting drives the whole pipeline.

    The grounder factory builds a per-worktree PKG grounder so each fanned-out
    feature grounds codegen in *its own* target clone (built lazily + cached per
    root inside the adapter) — matching the ``feature`` CLI's grounding.
    """
    if (os.getenv("SDLC_CODEGEN") or "stub").strip().lower() == "llm":
        from orchestrator.spine.grounder import compose_factory_with_ontomesh

        model = resolve_codegen_model()
        # Spine Seam 1: compose domain-true ontomesh grounding with the code-true PKG
        # grounder when SPINE_ONTOMESH_URL is configured (else unchanged).
        factory = compose_factory_with_ontomesh(PKGCodegenGrounder.from_repo)
        # SDLC_AGENTIC_CODEGEN=1 runs implement as the tool-use loop (Phase 5),
        # conditioned per run by the gate-approved capability plan (skills + MCP
        # servers threaded through FeatureWorkflowInput). Off by default — the
        # single-shot path stays the default until the loop is proven at scale.
        agentic = (os.getenv("SDLC_AGENTIC_CODEGEN") or "").strip().lower() in {"1", "true", "yes"}
        kwargs: dict[str, Any] = {"grounder_factory": factory, "agentic": agentic}
        if model:
            kwargs["model"] = model
        # Phase 2b — the agentic loop runs *as* the software-engineer persona: its
        # role leads the implement prompt and its skills are resolved through the
        # vetting gate. Only the agentic path consumes the persona; the single-shot
        # path is untouched.
        if agentic:
            from orchestrator.personas.software_engineer import SOFTWARE_ENGINEER

            kwargs["persona"] = SOFTWARE_ENGINEER
        # Bet 2c — SDLC_AGENTIC_POLICY points at a YAML/JSON policy file that
        # gates every in-loop tool call. ``require_approval`` rules pause the loop
        # for a real human decision at the feature workflow's in-loop gate.
        # Unset = no governance (no pauses), preserving the prior behavior.
        policy_path = (os.getenv("SDLC_AGENTIC_POLICY") or "").strip()
        if policy_path:
            from orchestrator.agentic import Policy

            kwargs["policy"] = Policy.from_file(policy_path)
        # Phase 1b — cross-run semantic memory. Pass the DB session factory + a
        # repo key so the agentic loop can recall + be primed by past-run facts.
        # The adapter still gates on ORCHESTRATOR_SEMANTIC_MEMORY, so this is
        # inert unless that flag is set.
        if memory_factory is not None and memory_repo_key:
            kwargs["memory_factory"] = memory_factory
            kwargs["memory_repo_key"] = memory_repo_key
        return LLMCodegenAdapter(llm(), **kwargs)
    return StubCodegenAdapter()


def _build_review(llm: Callable[[], LLMClient]) -> ReviewAdapter:
    """Select the review adapter from the environment.

    Default is the always-COMMENT stub. ``SDLC_REVIEW=llm`` swaps in the
    semantic-correctness judge (acceptance criteria vs generated code);
    ``SDLC_REVIEW_MODEL`` overrides its model.
    """
    if (os.getenv("SDLC_REVIEW") or "stub").strip().lower() != "llm":
        return StubReviewAdapter()
    model = os.getenv("SDLC_REVIEW_MODEL")
    if model:
        return SemanticReviewAdapter(llm(), model=model)
    return SemanticReviewAdapter(llm())


def _build_ci() -> CIAdapter:
    """Select the CI adapter from the environment.

    Default is the always-pass stub. ``SDLC_CI=gha`` awaits real GitHub
    Actions check runs on each feature PR — it needs the Block-A GitHub App
    creds plus ``SDLC_GITHUB_INSTALLATION_ID``.
    """
    if (os.getenv("SDLC_CI") or "stub").strip().lower() != "gha":
        return StubCIAdapter()
    from orchestrator.codereview.auth import GitHubAppAuth
    from orchestrator.codereview.config import GitHubAppConfig
    from orchestrator.codereview.github_client import GitHubClient

    installation = int(os.getenv("SDLC_GITHUB_INSTALLATION_ID", "0"))
    if installation <= 0:
        raise RuntimeError("SDLC_CI=gha requires SDLC_GITHUB_INSTALLATION_ID")
    config = GitHubAppConfig()
    return GHACIAdapter(GitHubClient(GitHubAppAuth(config), config), installation_id=installation)


def build_deps() -> SDLCDeps:
    """Wire up the SDLC worker-side dependencies. Exposed so tests can swap parts."""
    engine = create_async_engine(_default_database_url(), future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo_url = os.getenv("SDLC_REPO_URL") or None
    # One budget + one budgeted client shared by codegen and review, so a
    # run's spend is capped across all its LLM legs, not per adapter. Lazy:
    # only constructed when an LLM adapter is actually selected.
    budget = build_run_budget()
    shared: list[LLMClient] = []

    def llm() -> LLMClient:
        if not shared:
            shared.append(_build_llm(budget))
        return shared[0]

    # Realize the shared LLM for post-merge memory consolidation only when a real
    # codegen LLM is configured (otherwise leave it None so the activity no-ops
    # without constructing a client). Reuses the same cached shared instance.
    codegen_is_llm = (os.getenv("SDLC_CODEGEN") or "stub").strip().lower() == "llm"
    return SDLCDeps(
        session_factory=factory,
        workspace=WorkspaceManager(root=_default_workspace_root(), repo_url=repo_url),
        # Real test execution by default — the generated tests are runnable.
        # Codegen is real when SDLC_CODEGEN=llm; CI is real when SDLC_CI=gha.
        codegen=_build_codegen(llm, memory_factory=factory, memory_repo_key=repo_url),
        ci=_build_ci(),
        review=_build_review(llm),
        tests=SubprocessTestRunner(),
        # CI-parity gate; self-skips in scratch worktrees (no pyproject).
        preflight=SubprocessPreflightRunner(),
        budget=budget,
        llm=llm() if codegen_is_llm else None,
        artifact_store=artifact_store_from_env(),
    )


def _sdlc_config(base: TemporalConfig | None = None) -> TemporalConfig:
    """Resolve the base Temporal config but force the SDLC task queue."""
    cfg = base or TemporalConfig.from_env()
    return dataclasses.replace(cfg, task_queue=sdlc_task_queue())


def sdlc_activity_methods(instance: SDLCActivities) -> list[Any]:
    """The activity methods the SDLC worker registers, in workflow-call order.

    Exposed so tests can stand up a worker with the same registration the
    production worker uses, without duplicating the list.
    """
    return [
        instance.record_audit,
        instance.raise_approval_request,
        instance.intake_analyze,
        instance.profile_and_plan,
        instance.comprehend_repo,
        instance.design_feature,
        instance.create_jira_issues,
        instance.integration_test,
        instance.merge_prs,
        instance.consolidate_memory,
        instance.register_units,
        instance.create_workspace,
        instance.code_plan,
        instance.implement,
        instance.implement_resume,
        instance.test_author,
        instance.refine,
        instance.run_tests,
        instance.preflight,
        instance.review,
        instance.escalation_check,
        instance.open_pr,
        instance.cleanup_workspace,
    ]


async def run_sdlc_worker(
    deps: SDLCDeps | None = None,
    config: TemporalConfig | None = None,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the SDLC worker until ``stop_event`` is set (or forever if None)."""
    deps = deps or build_deps()
    cfg = _sdlc_config(config)
    client = await connect_client(cfg)
    activities_instance = SDLCActivities(deps)

    worker = Worker(
        client,
        task_queue=cfg.task_queue,
        workflows=[SDLCWorkflow, FeatureImplementationWorkflow],
        activities=sdlc_activity_methods(activities_instance),
    )
    logger.info(
        "sdlc.worker.start",
        extra={"host": cfg.host, "namespace": cfg.namespace, "task_queue": cfg.task_queue},
    )

    if stop_event is None:
        await worker.run()
        return

    worker_task = asyncio.create_task(worker.run())
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait({worker_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        await worker.shutdown()
        for task in pending:
            task.cancel()
    for task in done:
        if task is worker_task:
            task.result()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    await run_sdlc_worker(stop_event=stop_event)


if __name__ == "__main__":
    asyncio.run(_main())
