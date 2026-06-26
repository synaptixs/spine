"""Block A — Standalone PR-Reviewer (GitHub App).

The first adopter wedge of the SDLC-orchestrator initiative
(see ``docs/specs/SDLC-ORCHESTRATOR-PLAN.md`` §6 Block A).

A GitHub App that watches pull requests, runs the verifier chain over the
diff, and posts review comments. Zero workspace state, zero git operations:
webhook → fetch diff → analyze → comment.

Surface:
  - ``config``: GitHub App connection settings (env-driven).
  - ``models``: typed slices of the GitHub webhook payloads we consume.
  - ``webhook``: the FastAPI router that receives + verifies + dispatches
    pull_request events.

Auth (JWT + installation tokens), the github tools, the code-aware
verifiers, and the review orchestration land in subsequent commits
(Block A.2–A.5).
"""

from orchestrator.codereview.config import GitHubAppConfig

__all__ = ["GitHubAppConfig"]
