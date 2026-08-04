"""The walking skeleton: stages in order, state carried, and honest about where it stops.

These tests stub the stages' *entry points*, not their internals — the skeleton's job is
sequencing and state, and that is what should break a test when it regresses.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.sdlc.autorun import (
    STAGES,
    AutorunError,
    RunContext,
    autorun,
    default_artifacts_dir,
    render_summary,
)
from orchestrator.sdlc.runstate import RunRecord, RunStore


@pytest.fixture(autouse=True)
def _isolate_intake_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The intake plan is cached by source uri, and every test here uses the same uri —
    without this, the second test reads the first one's specs."""
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_CACHE_DIR", str(tmp_path / "intake-cache"))
    monkeypatch.setenv("SDLC_TEST_ISOLATION", "local")


class _Spec:
    def __init__(self, intent_id: str = "intent-a") -> None:
        self.intent_id = intent_id

    def model_dump(self) -> dict[str, Any]:
        return {
            "title": "Add CSV export",
            "intent_id": self.intent_id,
            "summary": "Users need their data out",
            "acceptance_criteria": ["exports a csv"],
        }


class _Plan:
    def __init__(self, specs: list[_Spec]) -> None:
        self.specs = specs
        self.documents: list[Any] = []
        self.intents: list[Any] = []
        self.gaps: list[Any] = []
        self.blocked = False
        self.truncated = False


class _Service:
    def __init__(self, specs: list[_Spec]) -> None:
        self._specs = specs

    async def analyze(self, root_id: str) -> _Plan:
        return _Plan(self._specs)


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    specs: list[_Spec] | None = None,
    feature: Any = None,
    calls: list[str] | None = None,
) -> dict[str, Any]:
    """Stub every stage boundary; record what the implement stage was handed."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    monkeypatch.setattr(
        "orchestrator.intake.factory.build_service_for",
        lambda *a, **k: _Service(specs if specs is not None else [_Spec()]),
    )

    async def _feature(source: str, **kwargs: Any) -> Any:
        seen["feature_kwargs"] = kwargs
        if calls is not None:
            calls.append("implement")
        if feature is not None:
            raise feature
        return SimpleNamespace(
            passed=True,
            issue_key="SSPN-42",
            branch="feat/abc/SSPN-42",
            worktree="/tmp/ws",
            files=["src/x.py"],
            iterations=1,
            pr_url=None,
        )

    monkeypatch.setattr("orchestrator.sdlc.feature_runner.run_feature", _feature)
    return seen


def test_stages_run_in_order_and_record_themselves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch)

    ctx = _run(tmp_path)

    assert [s.name for s in ctx.stages] == list(STAGES)
    assert [s.status for s in ctx.stages] == ["ok", "ok", "ok", "ok", "ok", "skipped"]
    assert ctx.verdict == "PROCEED"
    assert ctx.passed


def test_one_spec_is_resolved_once_and_injected_downstream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Intake happens here, not again inside the feature runner — otherwise the brief and
    design describe a spec the implementation might re-derive differently."""
    seen = _install(monkeypatch)

    ctx = _run(tmp_path)

    assert ctx.spec is not None and ctx.spec["title"] == "Add CSV export"
    assert seen["feature_kwargs"]["spec"] == ctx.spec


def test_the_design_is_handed_to_the_implement_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Chaining commands is not connecting them: before this the design was written to disk
    and codegen never saw it, so the agent researched, designed, then implemented as if
    neither had happened."""
    seen = _install(monkeypatch)

    ctx = _run(tmp_path)

    assert ctx.plan.startswith("# Design")
    assert seen["feature_kwargs"]["design"] == ctx.plan
    # ...and it is the same text the artifact holds, so what a human reads is what the model got.
    design_artifact = next(s.artifact for s in ctx.stages if s.name == "design")
    assert Path(design_artifact).read_text(encoding="utf-8") == ctx.plan


def test_artifacts_are_written_outside_the_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``understand`` ingests markdown from disk regardless of git, so a brief written into
    the working tree would become a Doc node and change the graph the next stage reads."""
    _install(monkeypatch)

    ctx = _run(tmp_path)

    written = [Path(s.artifact) for s in ctx.stages if s.artifact]
    assert {p.name for p in written} == {"investigation.md", "validity.md", "design.md"}
    repo = tmp_path / "repo"
    for path in written:
        assert path.is_file()
        assert repo not in path.parents  # never inside the repo under analysis


def test_the_default_artifact_dir_is_not_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPINE_RUN_ARTIFACTS", raising=False)
    assert Path.cwd() not in default_artifacts_dir("abc123").parents


def test_safe_mode_opens_no_pr_and_says_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The skeleton must be honest about where it stops rather than quietly doing nothing."""
    seen = _install(monkeypatch)

    ctx = _run(tmp_path)

    assert seen["feature_kwargs"]["live"] is False
    review = next(s for s in ctx.stages if s.name == "review")
    assert review.status == "skipped"
    assert "safe mode" in review.detail
    assert ctx.pr_url is None


def test_a_failing_stage_stops_the_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No stage runs on the back of a failed one, and the failure is recorded before raising."""
    from orchestrator.sdlc.feature_runner import FeatureRunError

    calls: list[str] = []
    _install(monkeypatch, feature=FeatureRunError("VERDICT: FAILED", code=1), calls=calls)

    with pytest.raises(AutorunError, match="VERDICT: FAILED") as exc:
        _run(tmp_path)

    assert exc.value.code == 1
    assert calls == ["implement"]


def test_no_specs_fails_before_any_graph_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, specs=[])

    with pytest.raises(AutorunError, match="No specs") as exc:
        _run(tmp_path)

    assert exc.value.code == 3


def test_an_unknown_intent_names_what_is_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, specs=[_Spec("intent-a"), _Spec("intent-b")])

    with pytest.raises(AutorunError, match="intent-a, intent-b") as exc:
        _run(tmp_path, intent_id="nope")

    assert exc.value.code == 3


def test_the_summary_reports_every_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch)

    summary = render_summary(_run(tmp_path))

    for stage in STAGES:
        assert f"| {stage} |" in summary
    assert "SSPN-42" in summary


# ---- supervisor: state, resume, one-run-per-ticket, budget (SSPN-22) --------


def test_a_run_records_itself_as_it_goes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A kill at any point must leave a findable run, not a mystery worktree."""
    _install(monkeypatch)
    store = RunStore(root=tmp_path / "state")

    ctx = _run(tmp_path, store=store)

    saved = store.load(ctx.run_id)
    assert saved is not None
    assert saved.status == "done" and saved.phase == "done"
    assert saved.issue_key == "SSPN-42"
    assert saved.branch == "feat/abc/SSPN-42" and saved.worktree == "/tmp/ws"


def test_resume_adopts_the_issue_the_previous_attempt_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The half that must never happen twice. A crashed run that already created a ticket is
    exactly how a duplicate got minted before `--issue` adoption existed."""
    seen = _install(monkeypatch)
    store = RunStore(root=tmp_path / "state")
    store.save(RunRecord(run_id="prev", source="file://./spec.md", issue_key="SSPN-77", status="running"))

    ctx = _run(tmp_path, store=store, resume="prev")

    assert ctx.run_id == "prev"
    assert seen["feature_kwargs"]["issue"] == "SSPN-77"


def test_resuming_a_run_that_does_not_exist_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch)

    with pytest.raises(AutorunError, match="no run 'nope'") as exc:
        _run(tmp_path, store=RunStore(root=tmp_path / "state"), resume="nope")

    assert exc.value.code == 2


def test_a_second_run_cannot_take_a_held_ticket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two live runs on one ticket race for the same branch and the same issue."""
    import os

    _install(monkeypatch)
    store = RunStore(root=tmp_path / "state")
    store.save(RunRecord(run_id="held", source="s", issue_key="SSPN-5", status="running", pid=os.getpid()))

    with pytest.raises(AutorunError, match="already held by run held") as exc:
        _run(tmp_path, store=store, issue="SSPN-5")

    assert exc.value.code == 2
    assert "--resume held" in str(exc.value)  # says how to continue it


def test_an_abandoned_run_does_not_block_the_ticket(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A dead process must not hold a ticket hostage."""
    _install(monkeypatch)
    store = RunStore(root=tmp_path / "state")
    store.save(RunRecord(run_id="dead", source="s", issue_key="SSPN-5", status="running", pid=999_999))

    ctx = _run(tmp_path, store=store, issue="SSPN-5")

    assert ctx.passed


def test_budget_exhaustion_parks_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Out of money mid-change: park it with the work on a branch. Shipping half a change
    because the wallet ran dry is worse than stopping."""
    from orchestrator.core.llm import BudgetExceededError

    _install(monkeypatch, feature=BudgetExceededError("run cap $1.00 exhausted"))
    store = RunStore(root=tmp_path / "state")

    with pytest.raises(AutorunError, match="budget exhausted") as exc:
        _run(tmp_path, store=store, max_cost_usd=1.0)

    assert exc.value.code == 4
    parked = [r for r in store.all() if r.status == "parked"]
    assert len(parked) == 1
    assert "exhausted" in parked[0].parked_reason


def test_a_failed_run_is_recorded_as_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from orchestrator.sdlc.feature_runner import FeatureRunError

    _install(monkeypatch, feature=FeatureRunError("VERDICT: FAILED", code=1))
    store = RunStore(root=tmp_path / "state")

    with pytest.raises(AutorunError):
        _run(tmp_path, store=store)

    assert [r.status for r in store.all()] == ["failed"]


# ---- helper ----------------------------------------------------------------


def _run(
    tmp_path: Path,
    *,
    intent_id: str | None = None,
    live: bool = False,
    store: Any = None,
    resume: str | None = None,
    issue: str | None = None,
    max_cost_usd: float | None = None,
) -> RunContext:
    """Run the skeleton against a tiny real repo, so the graph stages do real work."""
    import asyncio

    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "mod.py").write_text("def export_csv(rows):\n    return rows\n", encoding="utf-8")

    return asyncio.run(
        autorun(
            "file://./spec.md",
            intent_id=intent_id,
            root=repo,
            live=live,
            artifacts_dir=tmp_path / "artifacts",
            store=store or RunStore(root=tmp_path / "state"),
            resume=resume,
            issue=issue,
            max_cost_usd=max_cost_usd,
        )
    )
