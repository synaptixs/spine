"""Block A.3: GitHub REST client for the PR reviewer.

The operations the review flow (Block A.5) needs:

  - ``fetch_pr_diff``: read the PR's changed files + per-file patch hunks.
    This is what the code_reviewer agent reasons over.
  - ``submit_review``: post a single review with inline comments and a
    verdict (APPROVE / REQUEST_CHANGES / COMMENT). GitHub's review API does
    comments + verdict atomically, so "post_review_comment" and
    "request_changes" from the plan collapse into one call.

These are *infrastructure* calls made by the orchestration glue, not tools
the LLM decides to invoke — so this is a plain async client, not a
gateway ToolContract. Every call mints/reuses an installation token via
``GitHubAppAuth`` (Block A.2). The HTTP client is injectable for testing.

File-list pagination is capped (``_MAX_FILE_PAGES`` × 100): a PR touching
hundreds of files isn't a useful unit for an LLM review, and the cap keeps
one webhook from fanning out into unbounded API calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import httpx

from orchestrator.codereview.auth import GitHubAppAuth
from orchestrator.codereview.config import GitHubAppConfig

_MAX_FILE_PAGES = 3
_PER_PAGE = 100
_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class GitHubClientError(RuntimeError):
    """Raised when a GitHub API call returns a non-success status."""


class ReviewVerdict(str, Enum):
    """The ``event`` value GitHub's review API accepts."""

    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


@dataclass(frozen=True)
class ChangedFile:
    filename: str
    status: str  # added | modified | removed | renamed
    additions: int
    deletions: int
    patch: str  # unified-diff hunk for this file ("" for binary / too-large)


@dataclass(frozen=True)
class PRDiff:
    repo: str
    pr_number: int
    head_sha: str
    files: tuple[ChangedFile, ...]
    truncated: bool = False  # True when the file list hit the page cap

    @property
    def diff_text(self) -> str:
        """A concatenated patch view, convenient as agent input."""
        parts = [f"--- {f.filename} ({f.status})\n{f.patch}" for f in self.files if f.patch]
        return "\n\n".join(parts)


@dataclass(frozen=True)
class CheckRun:
    """One CI check run attached to a commit."""

    name: str
    status: str  # queued | in_progress | completed
    conclusion: str  # success | failure | neutral | cancelled | skipped | timed_out | "" while running

    @property
    def completed(self) -> bool:
        return self.status == "completed"

    @property
    def passed(self) -> bool:
        return self.completed and self.conclusion in ("success", "neutral", "skipped")


@dataclass(frozen=True)
class ReviewComment:
    """One inline comment anchored to a file + line in the PR head."""

    path: str
    line: int
    body: str
    side: str = "RIGHT"  # RIGHT = the new version; LEFT = the base


@dataclass
class ReviewSubmission:
    verdict: ReviewVerdict
    summary: str
    comments: list[ReviewComment] = field(default_factory=list)


class GitHubClient:
    """Thin async wrapper over the GitHub PR + review REST endpoints."""

    def __init__(
        self,
        auth: GitHubAppAuth,
        config: GitHubAppConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._auth = auth
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None

    async def fetch_pr_diff(self, *, installation_id: int, repo: str, pr_number: int) -> PRDiff:
        """Fetch the PR's changed files (paginated, capped) + head SHA."""
        token = await self._auth.installation_token(installation_id)
        head_sha = await self._fetch_head_sha(token, repo, pr_number)
        files, truncated = await self._fetch_files(token, repo, pr_number)
        return PRDiff(
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            files=tuple(files),
            truncated=truncated,
        )

    async def submit_review(
        self,
        *,
        installation_id: int,
        repo: str,
        pr_number: int,
        head_sha: str,
        submission: ReviewSubmission,
    ) -> dict[str, object]:
        """Post one review with inline comments + a verdict.

        Returns the created review payload. Inline comments anchored to a
        line GitHub rejects (e.g. outside the diff) would fail the whole
        call, so callers should only attach comments to changed lines.
        """
        token = await self._auth.installation_token(installation_id)
        url = f"{self._config.api_base_url}/repos/{repo}/pulls/{pr_number}/reviews"
        payload: dict[str, object] = {
            "commit_id": head_sha,
            "body": submission.summary,
            "event": submission.verdict.value,
            "comments": [
                {"path": c.path, "line": c.line, "side": c.side, "body": c.body} for c in submission.comments
            ],
        }
        resp = await self._request("POST", url, token, json=payload)
        if resp.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
            raise GitHubClientError(
                f"submit_review failed for {repo}#{pr_number}: HTTP {resp.status_code} {resp.text[:256]}"
            )
        result: dict[str, object] = resp.json()
        return result

    async def fetch_pr_head_sha(self, *, installation_id: int, repo: str, pr_number: int) -> str:
        """The PR's current head commit SHA (without fetching the file list)."""
        token = await self._auth.installation_token(installation_id)
        return await self._fetch_head_sha(token, repo, pr_number)

    async def fetch_check_runs(self, *, installation_id: int, repo: str, ref: str) -> list[CheckRun]:
        """Check runs for a commit SHA or branch ref (one page of 100)."""
        token = await self._auth.installation_token(installation_id)
        url = f"{self._config.api_base_url}/repos/{repo}/commits/{ref}/check-runs?per_page=100"
        resp = await self._request("GET", url, token)
        if resp.status_code != httpx.codes.OK:
            raise GitHubClientError(
                f"fetch check runs {repo}@{ref} failed: HTTP {resp.status_code} {resp.text[:256]}"
            )
        data = resp.json()
        return [
            CheckRun(
                name=str(item.get("name", "")),
                status=str(item.get("status", "")),
                conclusion=str(item.get("conclusion") or ""),
            )
            for item in (data.get("check_runs") or [])
        ]

    # ---- internals --------------------------------------------------------

    async def _fetch_head_sha(self, token: str, repo: str, pr_number: int) -> str:
        url = f"{self._config.api_base_url}/repos/{repo}/pulls/{pr_number}"
        resp = await self._request("GET", url, token)
        if resp.status_code != httpx.codes.OK:
            raise GitHubClientError(
                f"fetch PR {repo}#{pr_number} failed: HTTP {resp.status_code} {resp.text[:256]}"
            )
        data = resp.json()
        return str((data.get("head") or {}).get("sha") or "")

    async def _fetch_files(self, token: str, repo: str, pr_number: int) -> tuple[list[ChangedFile], bool]:
        files: list[ChangedFile] = []
        base = f"{self._config.api_base_url}/repos/{repo}/pulls/{pr_number}/files"
        truncated = False
        for page in range(1, _MAX_FILE_PAGES + 1):
            url = f"{base}?per_page={_PER_PAGE}&page={page}"
            resp = await self._request("GET", url, token)
            if resp.status_code != httpx.codes.OK:
                raise GitHubClientError(
                    f"fetch files {repo}#{pr_number} failed: HTTP {resp.status_code} {resp.text[:256]}"
                )
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for item in batch:
                files.append(
                    ChangedFile(
                        filename=str(item.get("filename", "")),
                        status=str(item.get("status", "")),
                        additions=int(item.get("additions", 0)),
                        deletions=int(item.get("deletions", 0)),
                        patch=str(item.get("patch", "")),
                    )
                )
            if len(batch) < _PER_PAGE:
                break
            if page == _MAX_FILE_PAGES:
                truncated = True
        return files, truncated

    async def _request(
        self, method: str, url: str, token: str, *, json: dict[str, object] | None = None
    ) -> httpx.Response:
        headers = {**_GH_HEADERS, "Authorization": f"Bearer {token}"}
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            return await client.request(method, url, headers=headers, json=json)
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
