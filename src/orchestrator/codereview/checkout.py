"""Give the PKG-grounded review a tree to read.

The impact brief and the grounding verifiers all need a **checkout** —
``PKGReviewGrounder.from_repo(root)``, ``PKGGroundingVerifier.from_repo(root)`` — and the
webhook path had none: it works from the GitHub API's diff alone. So neither was ever
constructed in production, and the freshness check and the doc-drift finding never reached a
pull request (``STATE-OF-SPINE`` §8). This is the missing half.

**Opt-in.** Cloning turns a stateless webhook into one that fetches a repository per review:
real cost, a real failure surface, and it needs the App's token for a private repo. Enabling it
is an operator's deliberate choice, not a behaviour change that arrives with an upgrade —
``ORCHESTRATOR_REVIEW_CHECKOUT=1``.

**Degrade loudly, never silently.** A checkout that fails does not block the review: the LLM
and the three diff-only verifiers still have everything they need. But the review then says so
in its body, because a degraded review that looks identical to a clean one is the failure this
codebase keeps finding — a check that confirms the expected path and stays quiet about the rest.

**Depth 1 at the head SHA.** Extraction reads a tree, not a history. The commit comes from
``PRDiff.head_sha``, so the graph is built from exactly what the review is about.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.codereview.github_client import PRDiff
from orchestrator.core.pinned_checkout import CheckoutError, materialize_at

logger = logging.getLogger("orchestrator.codereview")

ENABLE_ENV = "ORCHESTRATOR_REVIEW_CHECKOUT"


def checkout_enabled() -> bool:
    """Whether an operator has opted this deployment into PKG-grounded review."""
    return os.getenv(ENABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Grounding:
    """What a materialised checkout buys a review: an impact brief and extra verifiers."""

    impact_source: Any
    verifiers: list[Any]


class PRCheckoutGrounding:
    """Materialises a PR's head and builds the PKG-grounded review pieces from it.

    Stateless between reviews on purpose: the checkout lives for one review and is removed,
    so nothing survives to be half-trusted by the next one. That costs a fetch per review and
    buys the guarantee that a graph is never read from a tree whose provenance is unclear.
    """

    def __init__(self, *, clone_url_for: Any, enabled: bool | None = None) -> None:
        #: ``(repo) -> str | None`` — the URL to fetch, with credentials embedded when the
        #: deployment has them. Injected rather than imported so a test needs no network and
        #: no GitHub App.
        self._clone_url_for = clone_url_for
        self._enabled = checkout_enabled() if enabled is None else enabled

    @contextlib.asynccontextmanager
    async def for_diff(self, diff: PRDiff) -> AsyncIterator[Grounding | None]:
        """Yield the grounding for ``diff``, or ``None`` if it is off or unavailable.

        ``None`` is a *result*, not an error: the caller posts the review without the grounded
        findings and says so. Raising here would let a transient network failure block a review
        that three verifiers and a model could still produce.
        """
        if not self._enabled:
            yield None
            return

        url = await self._clone_url_for(diff.repo)
        if not url:
            logger.warning(
                "review checkout skipped: no clone URL for %s",
                diff.repo,
                extra={"event": "codereview.checkout_unavailable", "repo": diff.repo},
            )
            yield None
            return

        with tempfile.TemporaryDirectory(prefix="spine-review-") as tmp:
            try:
                root = materialize_at(url, diff.head_sha, Path(tmp) / "head")
            except CheckoutError as exc:
                # Loud in the log, and the caller says it in the review body. Silence here
                # would make "the graph found nothing" and "the graph was never built" look
                # the same to a reader.
                logger.warning(
                    "review checkout failed for %s@%s: %s",
                    diff.repo,
                    diff.head_sha[:12],
                    exc,
                    extra={"event": "codereview.checkout_failed", "repo": diff.repo},
                )
                yield None
                return

            from orchestrator.codereview.grounding import PKGGroundingVerifier, PKGReviewGrounder

            try:
                grounder = PKGReviewGrounder.from_repo(root)
                verifier = PKGGroundingVerifier.from_repo(root)
            except Exception as exc:  # noqa: BLE001 — extraction failure must not fail a review
                logger.warning(
                    "review grounding failed for %s: %s",
                    diff.repo,
                    exc,
                    extra={"event": "codereview.grounding_failed", "repo": diff.repo},
                )
                yield None
                return

            yield Grounding(impact_source=grounder, verifiers=[verifier])


__all__ = ["ENABLE_ENV", "Grounding", "PRCheckoutGrounding", "checkout_enabled"]
