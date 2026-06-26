"""Block B.6 unit tests: backlog service pipeline + URI parsing + rendering.

Uses in-memory fakes for the source + tracker so the whole pipeline runs
without network. The LLM stages use MockLLMClient-backed extractor/writer.
"""

from __future__ import annotations

import json as jsonlib

import pytest

from orchestrator.core.llm import CompletionResult, Message, MockLLMClient
from orchestrator.intake.gaps import GapAnalyzer, GapRule, GapSeverity
from orchestrator.intake.intents import IntentExtractor
from orchestrator.intake.jira import CreatedIssue, IssueLink, IssueRequest
from orchestrator.intake.service import (
    BacklogService,
    SourceUriError,
    parse_source_uri,
    spec_to_issue_request,
)
from orchestrator.intake.source import FetchTreeResult, SourceDocument, SourceRef
from orchestrator.intake.specs import FeatureSpec, SpecWriter

# ---- URI parsing ----------------------------------------------------------


def test_parse_source_uri_variants() -> None:
    assert parse_source_uri("confluence://123") == ("confluence", "123")
    assert parse_source_uri("confluence://page/456") == ("confluence", "456")


def test_parse_source_uri_keeps_file_paths_verbatim() -> None:
    # file:// roots are filesystem paths, not ids: relative paths resolve from
    # CWD, absolute paths keep their leading slash, a trailing slash is dropped.
    assert parse_source_uri("file://./spec.md") == ("file", "./spec.md")
    assert parse_source_uri("file://docs/specs") == ("file", "docs/specs")
    assert parse_source_uri("file:///abs/x.md") == ("file", "/abs/x.md")
    assert parse_source_uri("file://dir/") == ("file", "dir")
    with pytest.raises(SourceUriError, match="file source needs a path"):
        parse_source_uri("file://")


def test_parse_source_uri_rejects_space_and_garbage() -> None:
    with pytest.raises(SourceUriError, match="space-key"):
        parse_source_uri("confluence://space/ENG")
    with pytest.raises(SourceUriError):
        parse_source_uri("no-scheme")


# ---- spec rendering -------------------------------------------------------


def test_spec_to_issue_request_renders_body() -> None:
    spec = FeatureSpec(
        intent_id="intent-x",
        title="Add CSV export",
        summary="Export grid to CSV.",
        user_story="As an analyst, I want CSV.",
        acceptance_criteria=["Downloads <5s for 10k rows."],
        technical_notes="Stream the response.",
        nfrs=["p95 <5s"],
        dependencies=["data layer"],
        estimate="M",
    )
    req = spec_to_issue_request(spec, labels=("sdlc-intake",))
    assert req.summary == "Add CSV export"
    assert req.labels == ("sdlc-intake",)
    assert "Acceptance criteria:" in req.description
    assert "- Downloads <5s for 10k rows." in req.description
    assert "Source intent: intent-x" in req.description
    assert "Estimate: M" in req.description


# ---- fakes ----------------------------------------------------------------


class _FakeSource:
    source_kind = "confluence"

    def __init__(self, docs: list[SourceDocument], *, truncated: bool = False) -> None:
        self._docs = docs
        self._truncated = truncated

    async def fetch_document(self, doc_id: str) -> SourceDocument:  # pragma: no cover - unused
        return self._docs[0]

    async def list_children(self, doc_id: str) -> list[SourceRef]:  # pragma: no cover - unused
        return []

    async def fetch_tree(self, root_id: str, *, max_depth: int = 3, max_docs: int = 100) -> FetchTreeResult:
        return FetchTreeResult(documents=list(self._docs), truncated=self._truncated)


class _FakeTracker:
    tracker_kind = "fake"

    def __init__(self, *, dry_run: bool) -> None:
        self._dry_run = dry_run
        self.created: list[IssueRequest] = []
        self.links: list[IssueLink] = []
        self._n = 0

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    async def create_issue(self, request: IssueRequest) -> CreatedIssue:
        self.created.append(request)
        self._n += 1
        return CreatedIssue(key=f"ENG-{self._n}", dry_run=self._dry_run)

    async def link_issues(self, link: IssueLink) -> None:
        self.links.append(link)


def _llm(text: str) -> MockLLMClient:
    client = MockLLMClient()

    async def stub(messages: list[Message], **kwargs: object) -> CompletionResult:
        _ = messages, kwargs
        return CompletionResult(
            text=text, model="m", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=0.0
        )

    client.complete = stub  # type: ignore[method-assign]
    return client


def _service(
    *,
    docs: list[SourceDocument],
    intents_json: str,
    spec_json: str,
    tracker: _FakeTracker,
    rules: list[GapRule] | None = None,
) -> BacklogService:
    # Each LLM stage gets its own mock client so the extractor sees
    # intents_json and the writer sees spec_json.
    return BacklogService(
        source=_FakeSource(docs),
        extractor=IntentExtractor(_llm(intents_json)),
        analyzer=GapAnalyzer(rules),
        spec_writer=SpecWriter(_llm(spec_json)),
        tracker=tracker,
    )


# ---- pipeline -------------------------------------------------------------


async def test_analyze_then_create_dry_run() -> None:
    docs = [SourceDocument(id="p1", title="Reqs", body="Need CSV export.")]
    intents = jsonlib.dumps(
        {"intents": [{"title": "Add CSV export", "description": "Download data as CSV file."}]}
    )
    spec = jsonlib.dumps({"summary": "Export to CSV.", "acceptance_criteria": ["<5s"]})
    tracker = _FakeTracker(dry_run=True)
    svc = _service(docs=docs, intents_json=intents, spec_json=spec, tracker=tracker)

    plan = await svc.analyze("p1")
    assert [i.title for i in plan.intents] == ["Add CSV export"]
    assert plan.blocked is False  # description long enough, no open questions
    assert len(plan.specs) == 1

    issues = await svc.create_issues(plan)
    assert [i.key for i in issues] == ["ENG-1"]
    assert all(i.dry_run for i in issues)
    assert len(tracker.created) == 1
    assert tracker.created[0].summary == "Add CSV export"


async def test_blocked_when_open_questions_present() -> None:
    docs = [SourceDocument(id="p1", title="Reqs", body="ambiguous stuff")]
    intents = jsonlib.dumps(
        {
            "intents": [
                {
                    "title": "Mystery feature",
                    "description": "A long-enough description here.",
                    "open_questions": ["What exactly?"],
                }
            ]
        }
    )
    spec = jsonlib.dumps({"summary": "tbd"})
    tracker = _FakeTracker(dry_run=True)
    svc = _service(docs=docs, intents_json=intents, spec_json=spec, tracker=tracker)
    plan = await svc.analyze("p1")
    assert plan.blocked is True  # open questions → needs_input gates approval


async def test_run_convenience_creates_and_reports_dry_run_flag() -> None:
    docs = [SourceDocument(id="p1", title="Reqs", body="Need export.")]
    intents = jsonlib.dumps({"intents": [{"title": "Export", "description": "Export the data set."}]})
    spec = jsonlib.dumps({"summary": "Export."})
    tracker = _FakeTracker(dry_run=False)
    svc = _service(docs=docs, intents_json=intents, spec_json=spec, tracker=tracker)
    result = await svc.run("p1")
    assert result.dry_run is False
    assert [i.key for i in result.issues] == ["ENG-1"]


async def test_link_dependencies_when_live() -> None:
    docs = [SourceDocument(id="p1", title="Reqs", body="two features")]
    intents = jsonlib.dumps(
        {
            "intents": [
                {"title": "Base", "description": "The base capability here."},
                {"title": "Addon", "description": "Builds on base.", "dependencies": ["Base"]},
            ]
        }
    )
    spec = jsonlib.dumps({"summary": "s"})
    tracker = _FakeTracker(dry_run=False)
    svc = _service(docs=docs, intents_json=intents, spec_json=spec, tracker=tracker)
    plan = await svc.analyze("p1")
    await svc.create_issues(plan, link_dependencies=True)
    # Addon (ENG-2) depends on Base (ENG-1) → one Blocks link
    assert len(tracker.links) == 1
    assert tracker.links[0].link_type == "Blocks"


async def test_custom_blocker_rule_gates() -> None:
    docs = [SourceDocument(id="p1", title="Reqs", body="x")]
    intents = jsonlib.dumps({"intents": [{"title": "Thing", "description": "Long enough desc."}]})
    spec = jsonlib.dumps({"summary": "s"})
    rules = [
        GapRule(
            id="needs_two_nfrs",
            description="Need >=2 NFRs.",
            severity=GapSeverity.BLOCKER,
            check="min_items",
            field="nfrs",
            count=2,
        )
    ]
    tracker = _FakeTracker(dry_run=True)
    svc = _service(docs=docs, intents_json=intents, spec_json=spec, tracker=tracker, rules=rules)
    plan = await svc.analyze("p1")
    assert plan.blocked is True
