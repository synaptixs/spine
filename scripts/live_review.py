#!/usr/bin/env python
"""Live integration probe for Block A (PR reviewer).

Runs the real review pipeline against a real GitHub PR using a real LLM —
*without* posting by default. Prints the computed review (verdict, summary,
inline comments) so you can eyeball quality before letting it write.

Safe by default: ``--print`` (the default) computes and prints only.
``--post`` actually submits the review to the PR.

Required env (or .env):
    GITHUB_APP_ID
    GITHUB_APP_PRIVATE_KEY        (PEM contents)  -- or --
    GITHUB_APP_PRIVATE_KEY_PATH   (path to PEM)
    ANTHROPIC_API_KEY             (or another LiteLLM-supported provider key)

Usage:
    uv run python scripts/live_review.py \
        --repo owner/name --pr 12 --installation 12345678
    uv run python scripts/live_review.py --repo o/n --pr 12 --installation 1 --post
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Live Block-A PR review probe.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument("--installation", required=True, type=int, help="GitHub App installation id")
    parser.add_argument(
        "--post", action="store_true", help="Actually submit the review (default: print only)."
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Local checkout of the reviewed repo — enables PKG grounding "
        "(blast-radius prompt context + anchored impact findings).",
    )
    args = parser.parse_args()

    from orchestrator.codereview.auth import GitHubAppAuth
    from orchestrator.codereview.config import GitHubAppConfig
    from orchestrator.codereview.github_client import GitHubClient
    from orchestrator.codereview.grounding import PKGReviewGrounder
    from orchestrator.codereview.reviewer import LLMReviewer, ReviewService
    from orchestrator.core.env import load_local_env
    from orchestrator.core.llm import LiteLLMClient

    load_local_env()  # bridge .env → os.environ for the LLM provider key
    config = GitHubAppConfig()
    if not config.api_configured:
        print(
            "GitHub App not configured: set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY (or _PATH).",
            file=sys.stderr,
        )
        return 2

    # The reviewer defaults to claude-sonnet-4-6; honor an override so it can
    # run on gpt-4o with an OpenAI key (ORCHESTRATOR_REVIEW_MODEL, falling
    # back to the shared ORCHESTRATOR_INTAKE_MODEL).
    review_model = os.getenv("ORCHESTRATOR_REVIEW_MODEL") or os.getenv("ORCHESTRATOR_INTAKE_MODEL")
    llm = LiteLLMClient()

    grounder = None
    if args.repo_path:
        grounder = PKGReviewGrounder.from_repo(args.repo_path)
        print(f"[pkg] grounding enabled from {args.repo_path}", file=sys.stderr)

    if review_model:
        reviewer = LLMReviewer(llm, model=review_model, grounder=grounder)
    else:
        reviewer = LLMReviewer(llm, grounder=grounder)

    service = ReviewService(
        github=GitHubClient(GitHubAppAuth(config), config),
        llm_reviewer=reviewer,
        impact_source=grounder,
    )

    diff, submission = await service.preview_pull_request(
        installation_id=args.installation, repo=args.repo, pr_number=args.pr
    )
    print(
        json.dumps(
            {
                "repo": args.repo,
                "pr": args.pr,
                "head_sha": diff.head_sha,
                "files_reviewed": len(diff.files),
                "truncated": diff.truncated,
                "verdict": submission.verdict.value,
                "summary": submission.summary,
                "inline_comments": [
                    {"path": c.path, "line": c.line, "body": c.body} for c in submission.comments
                ],
            },
            indent=2,
        )
    )

    if args.post:
        await service.review_pull_request(
            installation_id=args.installation, repo=args.repo, pr_number=args.pr
        )
        print(f"\nPosted review to {args.repo}#{args.pr}.")
    else:
        print("\nPrint-only: no review posted. Re-run with --post to submit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
