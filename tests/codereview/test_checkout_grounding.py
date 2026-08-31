"""The PKG-grounded review layer reaches a pull request — or says it did not.

Until 2026-08-31 it reached neither. `webhook.py` built `ReviewService` without `verifiers` or
`impact_source`, so the impact brief, the fact-freshness check and the doc-drift finding never
ran on a PR — all of them need a checkout, and that path has none. These tests are the wiring,
and the honesty when the wiring cannot be used.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from orchestrator.codereview.checkout import ENABLE_ENV, Grounding, PRCheckoutGrounding, checkout_enabled
from orchestrator.codereview.github_client import ChangedFile, PRDiff
from orchestrator.codereview.reviewer import LLMReviewer, ReviewService
from orchestrator.codereview.verifiers import Finding, Severity
from orchestrator.core.llm import CompletionResult, Message

PATCH = "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"


def _diff() -> PRDiff:
    cf = ChangedFile(filename="m.py", status="modified", additions=1, deletions=1, patch=PATCH)
    return PRDiff(repo="acme/app", pr_number=7, head_sha="d" * 40, files=(cf,))


class _FakeLLM:
    async def complete(self, messages: list[Message], **_: Any) -> CompletionResult:
        return CompletionResult('{"summary": "looks fine", "findings": []}', "fake", 1, 1, 0.0, 1.0)


class _FakeGitHub:
    def __init__(self) -> None:
        self.submitted: list[Any] = []

    async def fetch_pr_diff(self, **_: Any) -> PRDiff:
        return _diff()

    async def submit_review(self, *, submission: Any, **_: Any) -> dict[str, object]:
        self.submitted.append(submission)
        return {}


class _MarkerVerifier:
    """Stands in for the PKG verifier: proves the grounded chain actually ran."""

    verifier_id = "pkg.grounding"

    def scan(self, diff: PRDiff) -> list[Finding]:
        return [
            Finding(
                verifier_id=self.verifier_id,
                rule="stale_fact",
                severity=Severity.WARNING,
                path="m.py",
                line=1,
                message="grounded finding",
            )
        ]


class _FakeGrounding:
    """A grounding provider that yields, or declines to."""

    def __init__(self, *, available: bool) -> None:
        self._available = available

    @contextlib.asynccontextmanager
    async def for_diff(self, diff: PRDiff) -> AsyncIterator[Grounding | None]:
        yield Grounding(impact_source=None, verifiers=[_MarkerVerifier()]) if self._available else None


def _service(grounding: Any) -> tuple[ReviewService, _FakeGitHub]:
    github = _FakeGitHub()
    service = ReviewService(
        github=github,  # type: ignore[arg-type]
        llm_reviewer=LLMReviewer(_FakeLLM()),
        grounding=grounding,
    )
    return service, github


# ---- the wiring -------------------------------------------------------------


async def test_grounded_findings_reach_the_review() -> None:
    service, _ = _service(_FakeGrounding(available=True))
    _diff_out, submission, findings = await service._compute(installation_id=1, repo="acme/app", pr_number=7)
    assert any(f.verifier_id == "pkg.grounding" for f in findings)
    assert "did not run" not in submission.summary


async def test_a_failed_checkout_still_produces_a_review() -> None:
    """A network blip must not block a review three verifiers and a model could produce."""
    service, _ = _service(_FakeGrounding(available=False))
    _diff_out, submission, findings = await service._compute(installation_id=1, repo="acme/app", pr_number=7)
    assert submission is not None
    assert not any(f.verifier_id == "pkg.grounding" for f in findings)


async def test_a_degraded_review_says_so_in_its_body() -> None:
    """Absent is not clean. A degraded review that reads like a clean one is the whole trap."""
    service, _ = _service(_FakeGrounding(available=False))
    _diff_out, submission, _f = await service._compute(installation_id=1, repo="acme/app", pr_number=7)
    assert "did not run" in submission.summary
    assert "not clean" in submission.summary


async def test_no_grounding_configured_is_not_reported_as_degraded() -> None:
    """Opt-in: a deployment that never asked for it has nothing to apologise for."""
    service, _ = _service(None)
    _diff_out, submission, _f = await service._compute(installation_id=1, repo="acme/app", pr_number=7)
    assert "did not run" not in submission.summary


# ---- the opt-in -------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected", [("1", True), ("true", True), ("on", True), ("", False), ("0", False)]
)
def test_the_checkout_is_opt_in(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv(ENABLE_ENV, value)
    assert checkout_enabled() is expected


def test_it_is_off_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    assert checkout_enabled() is False


async def test_a_disabled_provider_yields_nothing_and_fetches_nothing() -> None:
    calls: list[str] = []

    async def _url(repo: str) -> str | None:
        calls.append(repo)
        return "https://example.invalid/x.git"

    provider = PRCheckoutGrounding(clone_url_for=_url, enabled=False)
    async with provider.for_diff(_diff()) as grounding:
        assert grounding is None
    assert calls == []  # disabled means no network, not a fetch whose result is discarded


async def test_a_missing_clone_url_degrades_rather_than_raising() -> None:
    async def _url(repo: str) -> str | None:
        return None

    provider = PRCheckoutGrounding(clone_url_for=_url, enabled=True)
    async with provider.for_diff(_diff()) as grounding:
        assert grounding is None


async def test_an_unfetchable_repo_degrades_rather_than_raising(tmp_path: Path) -> None:
    """The head SHA here exists nowhere; materialisation must fail into a None, not an exception."""

    async def _url(repo: str) -> str | None:
        return str(tmp_path / "not-a-repo")

    provider = PRCheckoutGrounding(clone_url_for=_url, enabled=True)
    async with provider.for_diff(_diff()) as grounding:
        assert grounding is None
