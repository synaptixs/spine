"""Block B.5: issue-tracker adapter + Jira implementation.

The last hop of the backlog ingest: turn feature specs into tracker issues.
``IssueTrackerAdapter`` is the seam (Jira first; Linear / GitHub Issues
later); ``JiraAdapter`` implements it against the Jira Cloud REST API v3.

**Dry-run is first-class.** ``JiraConfig.dry_run`` (the plan's
read-only-default adoption lever) makes ``create_issue`` / ``link_issues``
return the *would-be* result with a synthetic ``DRY-N`` key and no API
call. The backlog service runs dry-run by default and only writes for real
after the intent-approval bookend — so a misfire can't litter a Jira
project.

Descriptions are wrapped in minimal Atlassian Document Format (ADF), which
v3 requires; ``_text_to_adf`` turns plain text into a paragraph-per-line
document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IssueTrackerError(RuntimeError):
    """Raised when an issue-tracker API call fails."""


@dataclass(frozen=True)
class IssueRequest:
    """A desired issue, tracker-agnostic."""

    summary: str
    description: str = ""
    issue_type: str = "Story"  # Story | Task | Epic | Bug …
    labels: tuple[str, ...] = ()
    parent_key: str = ""  # epic / parent issue key, if any


@dataclass
class CreatedIssue:
    key: str  # e.g. "PROJ-123" (or "DRY-1" in dry-run)
    id: str = ""
    url: str = ""
    dry_run: bool = False


@dataclass(frozen=True)
class IssueLink:
    inward_key: str
    outward_key: str
    link_type: str = "Relates"  # Jira link-type name


class IssueTrackerAdapter(Protocol):
    """Minimal contract every issue-tracker adapter satisfies."""

    tracker_kind: str  # "jira" | "linear" | …

    @property
    def dry_run(self) -> bool: ...

    async def create_issue(self, request: IssueRequest) -> CreatedIssue: ...

    async def link_issues(self, link: IssueLink) -> None: ...


class JiraConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JIRA_", env_file=".env", extra="ignore")

    base_url: str = Field(default="", description="Jira Cloud base, e.g. https://yourorg.atlassian.net")
    email: str = Field(default="", description="Atlassian account email for Basic auth.")
    api_token: str = Field(default="", description="Atlassian API token for Basic auth.")
    project_key: str = Field(default="", description="Target Jira project key, e.g. ENG.")
    dry_run: bool = Field(
        default=True,
        description="When true, no writes hit Jira; create/link return would-be results.",
    )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.email and self.api_token and self.project_key)

    @property
    def api_base(self) -> str:
        return f"{self.base_url.rstrip('/')}/rest/api/3"


def _text_to_adf(text: str) -> dict[str, Any]:
    """Wrap plain text in a minimal ADF doc (one paragraph per non-blank line)."""
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.splitlines()
        if line.strip()
    ] or [{"type": "paragraph", "content": []}]
    return {"type": "doc", "version": 1, "content": paragraphs}


class JiraAdapter:
    """IssueTrackerAdapter over Jira Cloud v3, with a real dry-run mode."""

    tracker_kind = "jira"

    def __init__(self, config: JiraConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client
        self._owns_client = http_client is None
        self._dry_counter = 0

    @property
    def dry_run(self) -> bool:
        return self._config.dry_run

    async def create_issue(self, request: IssueRequest) -> CreatedIssue:
        if self._config.dry_run:
            self._dry_counter += 1
            return CreatedIssue(key=f"DRY-{self._dry_counter}", dry_run=True)

        fields: dict[str, Any] = {
            "project": {"key": self._config.project_key},
            "summary": request.summary,
            "issuetype": {"name": request.issue_type},
            "description": _text_to_adf(request.description),
        }
        if request.labels:
            fields["labels"] = list(request.labels)
        if request.parent_key:
            fields["parent"] = {"key": request.parent_key}

        data = await self._post("/issue", {"fields": fields})
        key = str(data.get("key", ""))
        browse = f"{self._config.base_url.rstrip('/')}/browse/{key}" if key else ""
        return CreatedIssue(key=key, id=str(data.get("id", "")), url=browse, dry_run=False)

    async def link_issues(self, link: IssueLink) -> None:
        if self._config.dry_run:
            return
        await self._post(
            "/issueLink",
            {
                "type": {"name": link.link_type},
                "inwardIssue": {"key": link.inward_key},
                "outwardIssue": {"key": link.outward_key},
            },
            expect_json=False,
        )

    async def comment_issue(self, issue_key: str, body: str) -> None:
        """Post a progress/PR-link comment onto an existing issue.

        Honors dry-run: in dry-run mode it makes no API call (the pipeline
        still logs the would-be comment). The body is wrapped in ADF, same as
        issue descriptions.
        """
        if self._config.dry_run:
            return
        await self._post(
            f"/issue/{issue_key}/comment",
            {"body": _text_to_adf(body)},
        )

    async def transition_issue(self, issue_key: str, target_status: str) -> str | None:
        """Move ``issue_key`` to the workflow state named ``target_status``.

        Jira transition ids are workflow-specific, so we resolve by the
        destination status *name* (case-insensitive) from the issue's available
        transitions rather than hardcoding an id. Honors dry-run (no API call,
        returns ``None``). Returns the status transitioned to, or raises
        ``IssueTrackerError`` when no available transition reaches it.
        """
        if self._config.dry_run:
            return None
        data = await self._get(f"/issue/{issue_key}/transitions")
        transitions = data.get("transitions", [])
        target = target_status.strip().lower()
        for t in transitions:
            to_name = str((t.get("to") or {}).get("name", ""))
            if to_name.strip().lower() == target:
                await self._post(
                    f"/issue/{issue_key}/transitions",
                    {"transition": {"id": t["id"]}},
                    expect_json=False,
                )
                return to_name
        available = ", ".join(sorted(str((t.get("to") or {}).get("name", "")) for t in transitions))
        raise IssueTrackerError(
            f"no transition to {target_status!r} available for {issue_key} "
            f"(available: {available or 'none'})."
        )

    async def _get(self, path: str) -> dict[str, Any]:
        if not self._config.configured:
            raise IssueTrackerError(
                "Jira not configured (need JIRA_BASE_URL / EMAIL / API_TOKEN / PROJECT_KEY)."
            )
        url = f"{self._config.api_base}{path}"
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            resp = await client.get(
                url,
                auth=(self._config.email, self._config.api_token),
                headers={"Accept": "application/json"},
            )
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()
        if resp.status_code != httpx.codes.OK:
            raise IssueTrackerError(f"GET {path} failed: HTTP {resp.status_code} {resp.text[:256]}")
        data: dict[str, Any] = resp.json()
        return data

    async def _post(self, path: str, payload: dict[str, Any], *, expect_json: bool = True) -> dict[str, Any]:
        if not self._config.configured:
            raise IssueTrackerError(
                "Jira not configured (need JIRA_BASE_URL / EMAIL / API_TOKEN / PROJECT_KEY)."
            )
        url = f"{self._config.api_base}{path}"
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        try:
            resp = await client.post(
                url,
                json=payload,
                auth=(self._config.email, self._config.api_token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        finally:
            if self._owns_client and self._client is None:
                await client.aclose()
        if resp.status_code not in (httpx.codes.OK, httpx.codes.CREATED, httpx.codes.NO_CONTENT):
            raise IssueTrackerError(f"POST {path} failed: HTTP {resp.status_code} {resp.text[:256]}")
        if not expect_json or resp.status_code == httpx.codes.NO_CONTENT:
            return {}
        data: dict[str, Any] = resp.json()
        return data

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()


@dataclass
class BacklogCreationResult:
    """Outcome of creating a batch of issues from feature specs."""

    issues: list[CreatedIssue] = field(default_factory=list)
    dry_run: bool = True
