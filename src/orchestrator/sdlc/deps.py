"""Block C: worker-side dependencies for the SDLC activities.

Mirrors ``orchestrator.temporal.deps.ActivityDeps`` but adds the pieces the
SDLC stages need beyond DB + audit. Every domain step the workflow calls is a
swappable adapter so Block D can drop real implementations (LLM codegen, real
CI, GitHub PRs, Cloud Run) in behind the same Protocols without touching the
activities or the workflow:

  - ``workspace``    per-issue git worktrees (real already)
  - ``codegen``      plan / implement / author-tests / refine
  - ``tests``        run the worktree's tests (real subprocess by default)
  - ``review``       review the change; a BLOCKER blocks the PR
  - ``pr``           open the PR
  - ``ci``           cross-issue integration checks

``service_builder`` lets the intake stage be driven by a fake ``BacklogService``
in tests while staying production-shaped (the default builder is the real
Confluence factory). The refinement-loop cap lives on the workflow input
(orchestration logic), not here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrator.core.llm import LLMClient, RunBudget
from orchestrator.intake.service import BacklogService
from orchestrator.runtime import ArtifactStore, InMemoryArtifactStore
from orchestrator.sdlc.ci import CIAdapter, StubCIAdapter
from orchestrator.sdlc.codegen import CodegenAdapter, StubCodegenAdapter
from orchestrator.sdlc.escalation import EscalationPolicy
from orchestrator.sdlc.forge import PRAdapter, StubPRAdapter
from orchestrator.sdlc.preflight import PreflightRunner, StubPreflightRunner
from orchestrator.sdlc.review import ReviewAdapter, StubReviewAdapter
from orchestrator.sdlc.testrunner import StubTestRunner, TestRunner
from orchestrator.sdlc.workspace import WorkspaceManager

# A builder that returns a configured BacklogService given the Jira dry-run
# flag. Production wires this to ``build_confluence_service``; tests inject a
# builder that returns a service backed by a fake source adapter.
ServiceBuilder = Callable[..., BacklogService]


@dataclass(frozen=True)
class SDLCDeps:
    session_factory: async_sessionmaker[AsyncSession]
    workspace: WorkspaceManager
    actor: str = "system"
    service_builder: ServiceBuilder | None = None
    codegen: CodegenAdapter = field(default_factory=StubCodegenAdapter)
    # Default to the in-memory stub so unit/activity tests don't spawn pytest;
    # the production worker swaps in ``SubprocessTestRunner`` (see build_deps).
    tests: TestRunner = field(default_factory=StubTestRunner)
    review: ReviewAdapter = field(default_factory=StubReviewAdapter)
    pr: PRAdapter = field(default_factory=StubPRAdapter)
    ci: CIAdapter = field(default_factory=StubCIAdapter)
    # Per-run LLM spend cap (G9). None = no enforcement (stub adapters spend
    # nothing). The worker wires a shared RunBudget into the budgeted LLM
    # client it hands the codegen/review adapters; activities activate it
    # with the run's sdlc_id so concurrent runs stay independently capped.
    budget: RunBudget | None = None
    escalation: EscalationPolicy = field(default_factory=EscalationPolicy)
    preflight: PreflightRunner = field(default_factory=StubPreflightRunner)
    # The LLM client for post-merge memory consolidation (Phase 2b). None when no
    # LLM is configured (stub codegen) — the consolidate activity then no-ops.
    llm: LLMClient | None = None
    # Where the repo-comprehension milestone (M1) persists its architectural
    # artifacts (knowledge graph, memory bank, current-state). Defaults to an
    # in-memory store for tests; the worker wires the env-selected store.
    artifact_store: ArtifactStore = field(default_factory=InMemoryArtifactStore)
