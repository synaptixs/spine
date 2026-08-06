"""Block B.6: the backlog ingest service.

Ties the Block-B pieces into one flow:

    source.fetch_tree → IntentExtractor → GapAnalyzer
        → (intent-approval bookend) → SpecWriter → IssueTracker.create_issue

The service runs the whole pipeline and reports a ``BacklogResult``; it
does *not* decide policy. Whether issues are written for real is the
tracker's ``dry_run`` flag, which the caller (the CLI) sets — read-only by
default, live only after a human has reviewed the dry-run preview and the
gaps don't gate approval. ``result.blocked`` surfaces the bookend state so
the caller can refuse a live run.

``parse_source_uri`` turns ``confluence://<page_id>`` (or
``confluence://page/<id>``) into a ``(kind, root_id)`` pair. Space-key
resolution is a documented future extension.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from orchestrator.intake.gaps import GapAnalyzer, GapFinding, blocks_approval
from orchestrator.intake.intents import Intent, IntentExtractor, StructuredIntentSource
from orchestrator.intake.jira import CreatedIssue, IssueLink, IssueRequest, IssueTrackerAdapter
from orchestrator.intake.source import FetchTreeResult, SourceAdapter, SourceDocument
from orchestrator.intake.specs import FeatureSpec, SpecWriter

logger = logging.getLogger("orchestrator.intake.service")


class SourceUriError(ValueError):
    """Raised when a --source URI can't be parsed."""


def parse_source_uri(uri: str) -> tuple[str, str]:
    """Parse ``<kind>://<root_id>`` → ``(kind, root_id)``.

    Accepts ``confluence://123``, ``confluence://page/123``. Space-key
    rooting (``confluence://space/ENG``) is not resolved yet — the page id
    is the supported root. ``jira://<root>`` keeps its root verbatim (an issue
    key, a project key, or ``jql/<query>``). For ``file://<path>`` the root is a filesystem
    path, kept verbatim: ``file:///abs/x.md`` preserves its leading slash and
    ``file://./x.md`` resolves from the CWD.
    """
    if "://" not in uri:
        raise SourceUriError(f"source must be '<kind>://<root>', got {uri!r}")
    kind, _, rest = uri.partition("://")
    if not kind:
        raise SourceUriError(f"could not parse source uri {uri!r}")
    if kind == "file":
        # A path, not an id: strip only a trailing slash (so file://dir and
        # file://dir/ are equal) — strip("/") would eat the leading slash of
        # an absolute path.
        root = rest.rstrip("/") or rest
        if not root:
            raise SourceUriError(f"file source needs a path: {uri!r}")
        return kind, root
    if kind == "openspec":
        # openspec://<change-id> selects one change; openspec:// (empty root) = every change.
        return kind, rest.strip("/")
    if kind == "jira":
        # jira://PROJ-123 (issue subtree) | jira://PROJ (project) | jira://jql/<query>.
        # Kept verbatim (bar surrounding slashes) so a JQL's spaces/quotes survive.
        root = rest.strip("/")
        if not root:
            raise SourceUriError(
                "jira source needs an issue key, project key, or 'jql/<query>' (e.g. jira://PROJ-123)."
            )
        return kind, root
    rest = rest.strip("/")
    if rest.startswith("page/"):
        rest = rest[len("page/") :]
    if rest.startswith("space/"):
        raise SourceUriError(
            "space-key rooting is not supported yet; pass a page id (confluence://<page_id>)."
        )
    if not rest:
        raise SourceUriError(f"could not parse source uri {uri!r}")
    return kind, rest


def _spec_str(spec: FeatureSpec | Mapping[str, Any], name: str) -> str:
    value = spec.get(name) if isinstance(spec, Mapping) else getattr(spec, name, None)
    return str(value).strip() if value else ""


def _spec_list(spec: FeatureSpec | Mapping[str, Any], name: str) -> list[str]:
    value = spec.get(name) if isinstance(spec, Mapping) else getattr(spec, name, None)
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def spec_to_issue_request(
    spec: FeatureSpec | Mapping[str, Any],
    *,
    labels: tuple[str, ...] = (),
    issue_type: str = "",
    parent_key: str = "",
) -> IssueRequest:
    """Render a FeatureSpec into the issue body the tracker creates.

    The body is **markdown**, not plain text: :mod:`orchestrator.intake.adf` turns it
    into the ADF Jira renders, so headings stay headings and acceptance criteria stay
    a checklist instead of arriving as a wall of hyphen-prefixed paragraphs. Every
    spec field is carried — someone reading the issue should not have to open the
    spec to learn what "done" means.

    Accepts a plain mapping as well as a ``FeatureSpec`` so the SDLC feature runner,
    whose spec is already a dict, emits the *same* body. It used to hand-roll a
    thinner one (summary + criteria only), which meant an identical spec produced a
    poorer issue depending on which command created it.
    """
    lines: list[str] = []
    summary = _spec_str(spec, "summary")
    if summary:
        lines += [summary, ""]
    user_story = _spec_str(spec, "user_story")
    if user_story:
        lines += [f"> {user_story}", ""]

    def section(title: str, items: list[str]) -> None:
        if items:
            lines.extend([f"## {title}", *(f"- {item}" for item in items), ""])

    section("Acceptance criteria", _spec_list(spec, "acceptance_criteria"))
    notes = _spec_str(spec, "technical_notes")
    if notes:
        lines += ["## Technical notes", notes, ""]
    section("Non-functional requirements", _spec_list(spec, "nfrs"))
    section("Dependencies", _spec_list(spec, "dependencies"))

    footer: list[str] = []
    intent_id = _spec_str(spec, "intent_id")
    if intent_id:
        footer.append(f"**Source intent:** `{intent_id}`")
    estimate = _spec_str(spec, "estimate")
    if estimate:
        footer.append(f"**Estimate:** {estimate}")
    if footer:
        lines += ["---", " · ".join(footer)]

    return IssueRequest(
        summary=_spec_str(spec, "title"),
        description="\n".join(lines).strip(),
        issue_type=issue_type,
        labels=labels,
        parent_key=parent_key,
    )


@dataclass
class BacklogPlan:
    """The LLM-derived plan, before any issue is created. Cheap to preview."""

    documents: list[SourceDocument] = field(default_factory=list)
    intents: list[Intent] = field(default_factory=list)
    gaps: list[GapFinding] = field(default_factory=list)
    specs: list[FeatureSpec] = field(default_factory=list)
    blocked: bool = False  # gaps gate the intent-approval bookend
    truncated: bool = False  # source tree walk hit a cap


@dataclass
class BacklogResult(BacklogPlan):
    issues: list[CreatedIssue] = field(default_factory=list)
    dry_run: bool = True


class BacklogService:
    """Runs the Confluence → intents → gaps → specs → Jira pipeline.

    ``analyze`` does all the LLM work (the expensive part) and stops at the
    intent-approval bookend. ``create_issues`` does the tracker writes from
    an already-computed plan. The CLI calls ``analyze`` once for a preview,
    then ``create_issues`` only after the human approves — so flipping from
    dry-run to live never re-pays the LLM cost.
    """

    def __init__(
        self,
        *,
        source: SourceAdapter,
        extractor: IntentExtractor,
        analyzer: GapAnalyzer,
        spec_writer: SpecWriter,
        tracker: IssueTrackerAdapter,
        issue_labels: tuple[str, ...] = ("sdlc-intake",),
        epic_issue_type: str = "Epic",
    ) -> None:
        self._source = source
        self._extractor = extractor
        self._analyzer = analyzer
        self._spec_writer = spec_writer
        self._tracker = tracker
        self._labels = issue_labels
        self._epic_issue_type = epic_issue_type

    async def fetch_source_documents(self, root_id: str) -> FetchTreeResult:
        """Just the source fetch — no LLM, no tracker. For read-only consumers
        (e.g. the investigation brief) that want the raw documents, not a backlog."""
        return await self._source.fetch_tree(root_id)

    async def analyze(self, root_id: str) -> BacklogPlan:
        tree = await self._source.fetch_tree(root_id)
        # A structured source (e.g. OpenSpec) is already intent-shaped — parse it
        # deterministically and skip the LLM extractor (cheaper + lossless on the
        # stated acceptance criteria). Unstructured sources take the LLM path.
        if isinstance(self._source, StructuredIntentSource):
            intents = self._source.structured_intents(tree.documents)
        else:
            intents = await self._extractor.extract(tree.documents)
        gaps = self._analyzer.analyze(intents)
        specs = await self._spec_writer.write_all(intents)
        return BacklogPlan(
            documents=tree.documents,
            intents=intents,
            gaps=gaps,
            specs=specs,
            blocked=blocks_approval(gaps),
            truncated=tree.truncated,
        )

    def _epic_request(self, plan: BacklogPlan) -> IssueRequest:
        """The parent a backlog's stories hang from, named for the source document."""
        doc = plan.documents[0]
        lines = [f"Backlog derived from **{doc.title}**.", "", f"{len(plan.specs)} stories, one per intent."]
        if doc.url:
            lines += ["", f"Source: [{doc.title}]({doc.url})"]
        return IssueRequest(
            summary=doc.title,
            description="\n".join(lines),
            issue_type=self._epic_issue_type,
            labels=self._labels,
        )

    async def create_issues(
        self, plan: BacklogPlan, *, link_dependencies: bool = False, epic: bool = True
    ) -> list[CreatedIssue]:
        """Create one issue per spec, grouped under a shared parent when it earns one.

        The epic is created only when the plan holds **more than one** spec and the
        source named a document to hang them from — a lone story under its own epic is
        ceremony, and an epic with nothing to group is clutter. Every story is then
        parented to it, which is what a team-managed board needs to show the work as one
        body rather than an undifferentiated flat list. The epic is returned alongside
        the stories because it, too, was created.
        """
        issues: list[CreatedIssue] = []
        intent_to_key: dict[str, str] = {}
        parent_key = ""
        if epic and len(plan.specs) > 1 and plan.documents:
            created_epic = await self._tracker.create_issue(self._epic_request(plan))
            issues.append(created_epic)
            parent_key = created_epic.key
        for spec in plan.specs:
            created = await self._tracker.create_issue(
                spec_to_issue_request(spec, labels=self._labels, parent_key=parent_key)
            )
            issues.append(created)
            intent_to_key[spec.intent_id] = created.key
        if link_dependencies and not self._tracker.dry_run:
            await self._link_dependencies(plan.intents, intent_to_key)
        return issues

    async def run(self, root_id: str, *, link_dependencies: bool = False) -> BacklogResult:
        """Convenience: analyze + create in one call (tracker dry_run decides
        whether writes are real)."""
        plan = await self.analyze(root_id)
        issues = await self.create_issues(plan, link_dependencies=link_dependencies)
        return BacklogResult(
            documents=plan.documents,
            intents=plan.intents,
            gaps=plan.gaps,
            specs=plan.specs,
            blocked=plan.blocked,
            truncated=plan.truncated,
            issues=issues,
            dry_run=self._tracker.dry_run,
        )

    async def _link_dependencies(self, intents: list[Intent], intent_to_key: dict[str, str]) -> None:
        """Best-effort: link issues whose intent names another intent's title.

        Dependency strings are free text; we match them against intent ids
        and titles. Unmatched dependencies are left as description text.
        """
        title_to_key = {i.title.lower(): intent_to_key.get(i.id, "") for i in intents}
        id_to_key = intent_to_key
        for intent in intents:
            src_key = id_to_key.get(intent.id, "")
            if not src_key:
                continue
            for dep in intent.dependencies:
                target = id_to_key.get(dep) or title_to_key.get(dep.lower())
                if target and target != src_key:
                    await self._tracker.link_issues(
                        IssueLink(inward_key=src_key, outward_key=target, link_type="Blocks")
                    )
