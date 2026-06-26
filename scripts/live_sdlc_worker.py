"""Launch the SDLC worker with the full live adapter set (first E2E run).

Same wiring as ``build_deps`` (env-selected codegen/review/CI), plus the one
adapter the worker doesn't yet env-select: the real ``GhPRAdapter``, opening
PRs against ``develop``.

Required env (see .env): provider keys, GitHub App creds; plus
    SDLC_CODEGEN=llm SDLC_REVIEW=llm SDLC_CI=gha
    SDLC_GITHUB_INSTALLATION_ID=<id>
    SDLC_REPO_URL=https://github.com/<owner>/<repo>.git
    SDLC_WORKSPACE_ROOT=/tmp/sdlc-live-workspaces
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from orchestrator.core.env import load_local_env  # noqa: E402

load_local_env(str(REPO / ".env"))

from orchestrator.core.llm import BudgetedLLMClient, LiteLLMClient  # noqa: E402
from orchestrator.sdlc.codegen import LLMCodegenAdapter  # noqa: E402
from orchestrator.sdlc.forge import GhPRAdapter  # noqa: E402
from orchestrator.sdlc.grounding import PKGCodegenGrounder  # noqa: E402
from orchestrator.sdlc.review import SemanticReviewAdapter  # noqa: E402
from orchestrator.sdlc.worker import build_deps, build_run_budget, run_sdlc_worker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

import os  # noqa: E402

WORKSPACE_BASE = Path(os.getenv("SDLC_WORKSPACE_ROOT", "/tmp/sdlc-live-workspaces")) / "_base"
MODEL = os.getenv("SDLC_CODEGEN_MODEL", "claude-sonnet-4-6")


async def main() -> None:
    # Per-run spend cap (G9, SDLC_RUN_BUDGET_USD): the budgeted client refuses
    # further calls once a run hits its cap — run #6's credit burn can't recur.
    budget = build_run_budget()
    # Coding-tuned models emit several files per call — 60s is too tight.
    llm = BudgetedLLMClient(LiteLLMClient(request_timeout_seconds=300.0), budget)
    # PKG-GROUNDED codegen: the A/B measured grounded 3/3 mergeable vs
    # ungrounded 0/3 — running live without the grounder repeats the 0/3 arm.
    grounder = PKGCodegenGrounder.from_repo(WORKSPACE_BASE)
    deps = dataclasses.replace(
        build_deps(),
        codegen=LLMCodegenAdapter(llm, model=MODEL, grounder=grounder),
        review=SemanticReviewAdapter(llm, model=os.getenv("SDLC_REVIEW_MODEL", MODEL)),
        pr=GhPRAdapter(base_branch="develop", commit_prefix=""),
        budget=budget,
    )
    await run_sdlc_worker(deps)


if __name__ == "__main__":
    asyncio.run(main())
