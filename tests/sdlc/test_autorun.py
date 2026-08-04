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
    assert [s.status for s in ctx.stages] == ["ok", "ok", "ok", "ok", "skipped"]
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


def test_artifacts_are_written_outside_the_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``understand`` ingests markdown from disk regardless of git, so a brief written into
    the working tree would become a Doc node and change the graph the next stage reads."""
    _install(monkeypatch)

    ctx = _run(tmp_path)

    written = [Path(s.artifact) for s in ctx.stages if s.artifact]
    assert {p.name for p in written} == {"investigation.md", "design.md"}
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


# ---- helper ----------------------------------------------------------------


def _run(tmp_path: Path, *, intent_id: str | None = None, live: bool = False) -> RunContext:
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
        )
    )
