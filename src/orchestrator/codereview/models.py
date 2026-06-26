"""Typed slices of the GitHub webhook payloads the PR reviewer consumes.

GitHub sends large JSON bodies; we model only the fields we act on and set
``extra="ignore"`` so unmodelled fields pass through harmlessly. The full
schemas live at https://docs.github.com/webhooks/webhook-events-and-payloads.

We care about the ``pull_request`` event today. ``ACTIONS_THAT_TRIGGER_REVIEW``
is the allowlist of ``action`` values that should kick off a review —
opened / reopened / synchronize (new commits pushed). Everything else
(labeled, assigned, closed, …) is acknowledged but ignored.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# pull_request actions that warrant a (re)review.
ACTIONS_THAT_TRIGGER_REVIEW: frozenset[str] = frozenset({"opened", "reopened", "synchronize"})


class _Loose(BaseModel):
    """Base that ignores unmodelled GitHub fields."""

    model_config = ConfigDict(extra="ignore")


class GitHubAccount(_Loose):
    login: str = ""


class GitHubRef(_Loose):
    """A branch ref (head or base) on a PR."""

    ref: str = ""
    sha: str = ""


class GitHubRepository(_Loose):
    full_name: str = ""  # "owner/repo"
    name: str = ""
    owner: GitHubAccount = GitHubAccount()


class GitHubInstallation(_Loose):
    id: int = 0


class GitHubPullRequest(_Loose):
    number: int = 0
    title: str = ""
    state: str = ""
    html_url: str = ""
    diff_url: str = ""
    head: GitHubRef = GitHubRef()
    base: GitHubRef = GitHubRef()
    user: GitHubAccount = GitHubAccount()
    draft: bool = False


class PullRequestEvent(_Loose):
    """The subset of a ``pull_request`` webhook payload we act on."""

    action: str = ""
    number: int = 0
    pull_request: GitHubPullRequest = GitHubPullRequest()
    repository: GitHubRepository = GitHubRepository()
    installation: GitHubInstallation = GitHubInstallation()

    @property
    def should_review(self) -> bool:
        """True when this event should kick off a review.

        Draft PRs are skipped — reviewing work-in-progress is noise. Adopters
        who want draft reviews can flip this later via config.
        """
        return self.action in ACTIONS_THAT_TRIGGER_REVIEW and not self.pull_request.draft

    @property
    def review_target(self) -> dict[str, str | int]:
        """Compact identity of what to review — used as the audit resource_id
        source and the handle the github tools resolve against."""
        return {
            "repo": self.repository.full_name,
            "pr_number": self.pull_request.number,
            "head_sha": self.pull_request.head.sha,
            "installation_id": self.installation.id,
        }
