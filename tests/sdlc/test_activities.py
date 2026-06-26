"""Unit tests for the SDLC activities (no docker, no Temporal worker).

The stage activities are invoked directly. ``intake_analyze`` is driven with
an injected fake ``service_builder`` so it exercises the production code path
while staying offline; the codegen/workspace activities run against a real
git worktree in a tmp dir.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.specs import FeatureSpec
from orchestrator.sdlc.activities import SDLCActivities, SDLCStageError
from orchestrator.sdlc.deps import SDLCDeps
from orchestrator.sdlc.workspace import WorkspaceManager


class _StubSession:
    async def __aenter__(self) -> _StubSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        return None


def _session_factory() -> Any:
    def factory() -> _StubSession:
        return _StubSession()

    return factory


def _deps(*, service_builder: Any = None, root: Path | None = None) -> SDLCDeps:
    return SDLCDeps(
        session_factory=_session_factory(),
        workspace=WorkspaceManager(root=root or Path("/tmp/sdlc-test-ws")),
        service_builder=service_builder,
    )


class _FakeBacklogService:
    """Quacks like BacklogService for the one method intake_analyze calls."""

    def __init__(self, specs: list[FeatureSpec], intents: list[Any] | None = None) -> None:
        self._specs = specs
        self._intents = intents or []

    async def analyze(self, root_id: str) -> BacklogPlan:
        _ = root_id
        return BacklogPlan(intents=self._intents, specs=self._specs, blocked=False, truncated=False)


async def test_intake_analyze_uses_injected_service() -> None:
    specs = [
        FeatureSpec(intent_id="i1", title="Spec A"),
        FeatureSpec(intent_id="i2", title="Spec B"),
    ]
    acts = SDLCActivities(_deps(service_builder=lambda **_: _FakeBacklogService(specs)))

    out = await acts.intake_analyze({"source_uri": "confluence://123", "dry_run_jira": True})

    assert out["intent_count"] == 0  # the fake plan has no intents, only specs
    assert out["blocked"] is False
    assert [s["title"] for s in out["specs"]] == ["Spec A", "Spec B"]


async def test_intake_analyze_surfaces_open_questions() -> None:
    from orchestrator.intake.intents import Intent

    intents = [
        Intent(id="i1", title="A", open_questions=["Which auth backend?", "Sync or async?"]),
        Intent(id="i2", title="B", open_questions=["Which auth backend?", "Page size?"]),
    ]
    acts = SDLCActivities(_deps(service_builder=lambda **_: _FakeBacklogService([], intents=intents)))

    out = await acts.intake_analyze({"source_uri": "confluence://123", "dry_run_jira": True})

    # Deduped across intents, order preserved.
    assert out["open_questions"] == ["Which auth backend?", "Sync or async?", "Page size?"]


async def test_intake_analyze_rejects_unsupported_source_kind() -> None:
    # No injected builder → production path dispatches via build_service_for,
    # which refuses an unsupported kind (confluence/notion are the only two).
    # The IntakeNotConfiguredError is surfaced as a non-retryable SDLCStageError.
    acts = SDLCActivities(_deps())
    with pytest.raises(SDLCStageError):
        await acts.intake_analyze({"source_uri": "github://owner/repo"})


async def test_create_jira_issues_synthesizes_keys() -> None:
    acts = SDLCActivities(_deps())
    out = await acts.create_jira_issues(
        {"specs": [{"title": "A"}, {"title": "B"}, {"title": "C"}], "dry_run": True}
    )
    keys = [p["issue_key"] for p in out["issue_plans"]]
    assert keys == ["SDLC-1", "SDLC-2", "SDLC-3"]


async def test_raise_approval_request_builds_a_valid_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate activity constructs a valid ApprovalRequest and persists it via
    the repo (faked here) so the REST /v1/approvals API can decide it later."""
    created: list[Any] = []

    class _FakeApprovalRepo:
        def __init__(self, session: Any) -> None:
            _ = session

        async def get(self, approval_id: str) -> None:
            _ = approval_id
            return None  # not yet persisted → create path

        async def create(self, request: Any) -> Any:
            created.append(request)
            return request

    class _FakeAuditRepo:
        def __init__(self, session: Any) -> None:
            _ = session

        async def write(self, **kwargs: Any) -> None:
            _ = kwargs

    monkeypatch.setattr("orchestrator.sdlc.activities.ApprovalRequestRepo", _FakeApprovalRepo)
    monkeypatch.setattr("orchestrator.sdlc.activities.AuditLogRepo", _FakeAuditRepo)

    acts = SDLCActivities(_deps())
    out = await acts.raise_approval_request(
        {
            "approval_id": "sdlc-abc12345-1",
            "task_id": "abc12345",  # >= 8 chars per the model
            "before_node_id": "merge",
            "title": "approve merge",
            "risk_classification": "high",
        }
    )

    assert out["state"] == "pending"
    assert out["before_node_id"] == "merge"
    assert len(created) == 1
    assert created[0].risk_classification.value == "high"
    assert created[0].approvers[0].role == "any"


async def test_raise_approval_request_fires_slack_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SLACK_WEBHOOK_URL is set, raising a gate notifies via the existing
    notify.slack notifier — built from the saved row, best-effort."""

    class _FakeApprovalRepo:
        def __init__(self, session: Any) -> None:
            _ = session

        async def get(self, approval_id: str) -> None:
            _ = approval_id
            return None

        async def create(self, request: Any) -> Any:
            return request

    class _FakeAuditRepo:
        def __init__(self, session: Any) -> None:
            _ = session

        async def write(self, **kwargs: Any) -> None:
            _ = kwargs

    sent: list[Any] = []

    class _SpyNotifier:
        def __init__(self, *a: Any, **k: Any) -> None:
            _ = (a, k)

        def notify_approval_raised(self, request: Any) -> bool:
            sent.append(request)
            return True

    monkeypatch.setattr("orchestrator.sdlc.activities.ApprovalRequestRepo", _FakeApprovalRepo)
    monkeypatch.setattr("orchestrator.sdlc.activities.AuditLogRepo", _FakeAuditRepo)
    monkeypatch.setattr("orchestrator.notify.slack.SlackWebhookNotifier", _SpyNotifier)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/xxx")

    acts = SDLCActivities(_deps())
    out = await acts.raise_approval_request(
        {
            "approval_id": "sdlc-abc12345-0",
            "task_id": "abc12345",
            "before_node_id": "intents",
            "title": "approve intents",
            "risk_classification": "medium",
        }
    )

    assert out["state"] == "pending"  # the row is unaffected by the notification
    assert len(sent) == 1
    assert sent[0].approval_id == "sdlc-abc12345-0"
    assert sent[0].risk_classification == "medium"


async def test_raise_approval_request_no_slack_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset SLACK_WEBHOOK_URL → no notifier constructed, gate still succeeds."""

    class _FakeApprovalRepo:
        def __init__(self, session: Any) -> None:
            _ = session

        async def get(self, approval_id: str) -> None:
            _ = approval_id
            return None

        async def create(self, request: Any) -> Any:
            return request

    class _FakeAuditRepo:
        def __init__(self, session: Any) -> None:
            _ = session

        async def write(self, **kwargs: Any) -> None:
            _ = kwargs

    def _boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("notifier must not be constructed when unconfigured")

    monkeypatch.setattr("orchestrator.sdlc.activities.ApprovalRequestRepo", _FakeApprovalRepo)
    monkeypatch.setattr("orchestrator.sdlc.activities.AuditLogRepo", _FakeAuditRepo)
    monkeypatch.setattr("orchestrator.notify.slack.SlackWebhookNotifier", _boom)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    acts = SDLCActivities(_deps())
    out = await acts.raise_approval_request(
        {
            "approval_id": "sdlc-abc12345-2",
            "task_id": "abc12345",
            "before_node_id": "intents",
        }
    )
    assert out["state"] == "pending"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_implement_writes_one_file_into_worktree(tmp_path: Path) -> None:
    deps = _deps(root=tmp_path / "ws")
    acts = SDLCActivities(deps)
    created = await acts.create_workspace({"sdlc_id": "s1", "issue_key": "SDLC-1"})
    path = Path(created["path"])

    impl = await acts.implement({"path": str(path), "issue_key": "SDLC-1", "spec": {}})

    assert impl["files"] == [str(path / "generated.py")]
    assert (path / "generated.py").read_text(encoding="utf-8").count("def feature") == 1

    await acts.cleanup_workspace({"path": str(path)})
    assert not path.exists()


# ---- adapter-backed stage shapes (gaps #1-#2 seams) ----------------------


async def test_code_plan_returns_steps() -> None:
    acts = SDLCActivities(_deps())
    out = await acts.code_plan({"spec": {"title": "A"}, "path": "/tmp/ws/SDLC-1"})
    assert isinstance(out["steps"], list)
    assert out["steps"]  # the stub emits a non-empty plan


async def test_test_author_returns_files_and_summary(tmp_path: Path) -> None:
    acts = SDLCActivities(_deps())
    out = await acts.test_author({"spec": {}, "path": str(tmp_path), "issue_key": "SDLC-1"})
    assert "files" in out
    assert "summary" in out


async def test_refine_returns_files_and_summary(tmp_path: Path) -> None:
    acts = SDLCActivities(_deps())
    out = await acts.refine(
        {
            "spec": {},
            "path": str(tmp_path),
            "issue_key": "SDLC-1",
            "failures": "AssertionError",
        }
    )
    assert "files" in out
    assert "summary" in out


async def test_run_tests_stub_passes_by_default() -> None:
    """The default StubTestRunner reports a pass (returncode 0) shape."""
    acts = SDLCActivities(_deps())
    out = await acts.run_tests({"path": "/tmp/ws/SDLC-1"})
    assert out["passed"] is True
    assert out["returncode"] == 0
    assert "output" in out


async def test_review_stub_has_no_blocker() -> None:
    acts = SDLCActivities(_deps())
    out = await acts.review({"path": "/tmp/ws/SDLC-1", "issue_key": "SDLC-1"})
    assert out["has_blocker"] is False
    assert out["verdict"] == "comment"
    assert out["blockers"] == []


async def test_integration_test_stub_passes() -> None:
    acts = SDLCActivities(_deps())
    out = await acts.integration_test({"issue_keys": ["SDLC-1", "SDLC-2"]})
    assert out["verdict"] == "pass"
    assert out["issue_keys"] == ["SDLC-1", "SDLC-2"]


async def test_open_pr_stub_returns_url() -> None:
    acts = SDLCActivities(_deps())
    out = await acts.open_pr({"issue_key": "SDLC-1", "path": "/tmp/ws/SDLC-1", "branch": "feat/s1/SDLC-1"})
    assert out["pr_url"].endswith("SDLC-1")


# ---- per-run budget enforcement (G9) --------------------------------------


class _SpendingCodegen:
    """Quacks like a CodegenAdapter whose implement() spends LLM budget."""

    def __init__(self, budget: Any, cost_per_call: float) -> None:
        self._budget = budget
        self._cost = cost_per_call

    async def implement(
        self,
        *,
        spec: dict[str, Any],
        path: str,
        issue_key: str,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> Any:
        from orchestrator.sdlc.codegen import CodeChange

        self._budget.check()
        self._budget.charge(self._cost)
        return CodeChange(files=[f"{path}/generated.py"], summary="spent")

    async def implement_governed(
        self,
        *,
        spec: dict[str, Any],
        path: str,
        issue_key: str,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> Any:
        from orchestrator.sdlc.codegen import ImplementOutcome

        change = await self.implement(
            spec=spec, path=path, issue_key=issue_key, skills=skills, mcp_servers=mcp_servers
        )
        return ImplementOutcome(change=change)


async def test_implement_charges_and_enforces_the_run_budget() -> None:
    from orchestrator.core.llm import BudgetExceededError, RunBudget

    budget = RunBudget(max_cost_usd=1.5)
    deps = SDLCDeps(
        session_factory=_session_factory(),
        workspace=WorkspaceManager(root=Path("/tmp/unused")),
        codegen=_SpendingCodegen(budget, cost_per_call=1.0),  # type: ignore[arg-type]
        budget=budget,
    )
    acts = SDLCActivities(deps)
    audited: list[dict[str, Any]] = []

    async def _capture_audit(request: dict[str, Any]) -> None:
        audited.append(request)

    acts.record_audit = _capture_audit  # type: ignore[method-assign]
    payload = {"sdlc_id": "run-x", "path": "/tmp/ws/SDLC-1", "issue_key": "SDLC-1", "spec": {}}

    await acts.implement(payload)
    await acts.implement(payload)  # crosses the cap (spent 2.0 of 1.5)
    with pytest.raises(BudgetExceededError, match="run-x"):
        await acts.implement(payload)

    # The trip left a queryable audit row with the dollars and the stage.
    assert [a["action"] for a in audited] == ["sdlc_budget_exhausted"]
    assert audited[0]["after"] == {"stage": "implement", "spent_usd": 2.0, "max_cost_usd": 1.5}
    assert audited[0]["resource_id"] == "run-x"

    # A different run on the same worker deps is unaffected.
    other = dict(payload, sdlc_id="run-y")
    out = await acts.implement(other)
    assert out["summary"] == "spent"
    assert budget.spent("run-x") == 2.0
    assert budget.spent("run-y") == 1.0


async def test_implement_without_budget_is_unrestricted(tmp_path: Path) -> None:
    deps = _deps()
    assert deps.budget is None
    acts = SDLCActivities(deps)
    out = await acts.implement({"path": str(tmp_path), "issue_key": "S-1", "spec": {}})
    assert out["files"]


async def test_profile_and_plan_profiles_and_classifies(tmp_path: Path) -> None:
    # Scratch workspace (no repo_url) → git-init base; a migration intent drives
    # the migration workflow_params from the catalog.
    acts = SDLCActivities(_deps(root=tmp_path))
    out = await acts.profile_and_plan({"intent_text": "Migrate users to the new schema"})
    assert out["profile"]["task_type"] == "migration"
    assert out["plan"]["workflow_params"].get("max_parallel_features") == 4


async def test_profile_and_plan_falls_back_when_profiling_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(root=tmp_path)

    async def _boom() -> Path:
        raise RuntimeError("base unavailable")

    monkeypatch.setattr(deps.workspace, "ensure_base_repo", _boom)
    acts = SDLCActivities(deps)
    out = await acts.profile_and_plan({"intent_text": "Add CSV export"})
    # Degrades to an intent-only profile; a feature still gets PKG grounding.
    assert out["profile"]["task_type"] == "feature"
    assert "repo-pkg-grounding" in out["plan"]["skills"]
