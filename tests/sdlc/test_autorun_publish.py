"""`sdlc autorun` publishes the reviewed change, and its spend cap is real (B13, B14).

Before this, a live run opened its pull request inside the build stage and *then* reviewed the
change — fixing what it found in the worktree, uncommitted, so the PR never carried the fixes. A
review that ended with unresolved findings still ended the run `done`, exit 0. And the documented
per-run cap, ``SDLC_RUN_BUDGET_USD``, was read only by the Temporal worker: autorun capped only with
``--max-cost``, only around the build stage, and from $0 again on a resume.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.codereview.verifiers import Finding, Severity
from orchestrator.sdlc.autorun import autorun
from orchestrator.sdlc.reviewloop import LoopResult, Round
from orchestrator.sdlc.runstate import RunRecord, RunStore


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_CACHE_DIR", str(tmp_path / "intake-cache"))
    monkeypatch.setenv("SDLC_TEST_ISOLATION", "local")
    monkeypatch.delenv("SDLC_RUN_BUDGET_USD", raising=False)
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)


SPEC = {"title": "Add CSV export", "intent_id": "intent-a", "summary": "s", "acceptance_criteria": ["c"]}


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout


def _worktree(tmp_path: Path) -> Path:
    """A real branch with the build already committed — what `run_feature(publish=False)` leaves."""
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q")
    _git(wt, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    (wt / "x.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "-m", "SSPN-42: Add CSV export")
    return wt


class _Published:
    """The publishing step `run_feature` hands back, recording what it was asked to do."""

    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *, draft: bool, note: str) -> str:
        self.calls.append(
            {
                "draft": draft,
                "note": note,
                "log": _git(self.worktree, "log", "--format=%s"),
                "dirty": _git(self.worktree, "status", "--porcelain"),
            }
        )
        return "https://github.com/x/y/pull/9"


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    outcome: LoopResult,
    edit: bool = True,
) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    wt = _worktree(tmp_path)
    published = _Published(wt)
    seen["published"] = published

    async def _feature(source: str, **kwargs: Any) -> Any:  # noqa: ARG001
        seen["feature_kwargs"] = kwargs
        budget = kwargs.get("budget")
        seen["spent_at_build"] = budget.spent() if budget is not None else None
        return SimpleNamespace(
            passed=True,
            issue_key="SSPN-42",
            branch="feat/SSPN-42",
            worktree=str(wt),
            files=["x.py"],
            iterations=1,
            pr_url=None,
            codegen=None,
            tests=None,
            coverage_withdrawn=[],
            publish=published,
        )

    async def _review(**kwargs: Any) -> LoopResult:
        from orchestrator.core.llm.budget import _active_run

        seen["review_charged_to"] = _active_run.get()
        if edit:  # the fixer's edit, left in the worktree exactly as the real loop leaves it
            (Path(kwargs["path"]) / "x.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        return outcome

    monkeypatch.setattr("orchestrator.sdlc.feature_runner.run_feature", _feature)
    monkeypatch.setattr("orchestrator.sdlc.reviewloop.review_and_fix", _review)
    return seen


def _run(
    tmp_path: Path, *, live: bool = True, store: Any = None, resume: str | None = None, **kw: Any
) -> Any:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "mod.py").write_text("def export_csv(rows):\n    return rows\n", encoding="utf-8")
    return asyncio.run(
        autorun(
            "file://./spec.md",
            root=repo,
            live=live,
            repo="https://x/widget" if live else None,
            spec=SPEC,
            plan_gate=False,
            artifacts_dir=tmp_path / "artifacts",
            store=store or RunStore(root=tmp_path / "state"),
            approvals_dir=tmp_path / "approvals",
            resume=resume,
            **kw,
        )
    )


CLEAN = LoopResult(
    rounds=[Round(number=1, findings=1, fixed_files=("x.py",), tests_passed=True)],
    stopped="review clean",
    clean=True,
)
UNCLEAN = LoopResult(
    rounds=[Round(number=1, findings=1, fixed_files=("x.py",), tests_passed=True)],
    remaining=[
        Finding(
            verifier_id="lint",
            rule="no-bare-except",
            severity=Severity.BLOCKER,
            path="x.py",
            line=2,
            message="bare except",
        )
    ],
    stopped="review budget spent after 2 round(s)",
    clean=False,
)


def test_the_pr_opens_after_the_review_with_its_fixes_committed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _install(monkeypatch, tmp_path, outcome=CLEAN)

    ctx = _run(tmp_path)

    assert seen["feature_kwargs"]["publish"] is False  # the build does not open the PR itself
    [call] = seen["published"].calls
    assert call["draft"] is False
    assert call["log"].splitlines()[:2] == ["SSPN-42: review fixes", "SSPN-42: Add CSV export"]
    assert call["dirty"] == ""  # nothing the review changed is left behind
    assert ctx.pr_url == "https://github.com/x/y/pull/9"
    names = [s.name for s in ctx.stages]
    assert names.index("review") < names.index("publish")
    assert ctx.passed


def test_a_review_that_changed_nothing_adds_no_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean_untouched = LoopResult(
        rounds=[Round(number=1, findings=0, note="nothing to fix")], stopped="review clean", clean=True
    )
    seen = _install(monkeypatch, tmp_path, outcome=clean_untouched, edit=False)

    _run(tmp_path)

    assert seen["published"].calls[0]["log"].splitlines()[0] == "SSPN-42: Add CSV export"


def test_an_unclean_review_opens_a_draft_and_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _install(monkeypatch, tmp_path, outcome=UNCLEAN)
    store = RunStore(root=tmp_path / "state")

    ctx = _run(tmp_path, store=store)

    [call] = seen["published"].calls
    assert call["draft"] is True
    assert "bare except" in call["note"]  # the unresolved findings travel with the PR
    assert not ctx.passed
    assert [r.status for r in store.all()] == ["failed"]


def test_a_safe_run_commits_the_fixes_and_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _install(monkeypatch, tmp_path, outcome=CLEAN)

    ctx = _run(tmp_path, live=False)

    assert seen["published"].calls == []
    assert _git(tmp_path / "wt", "log", "--format=%s").splitlines()[0] == "SSPN-42: review fixes"
    publish = next(s for s in ctx.stages if s.name == "publish")
    assert publish.status == "skipped"


def test_the_cli_exits_non_zero_when_a_stage_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from orchestrator.cli import app
    from orchestrator.sdlc.autorun import RunContext, StageResult

    async def _autorun(*a: Any, **k: Any) -> RunContext:
        ctx = RunContext(run_id="r1", source="s", live=False, root=tmp_path, artifacts_dir=tmp_path / "a")
        ctx.stages.append(StageResult(name="review", status="failed", detail="1 unresolved"))
        return ctx

    monkeypatch.setattr("orchestrator.sdlc.autorun.autorun", _autorun)
    result = CliRunner().invoke(app, ["sdlc", "autorun", "--source", "file://./spec.md", "--no-plan-gate"])

    assert result.exit_code == 1, result.output


# ---- B14: the cap is the documented one, and it covers the whole build ------------------------


def test_the_cap_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SDLC_RUN_BUDGET_USD", "3")
    seen = _install(monkeypatch, tmp_path, outcome=CLEAN)

    _run(tmp_path, live=False)

    assert seen["feature_kwargs"]["budget"].max_cost_usd == 3.0


def test_without_the_variable_the_documented_default_applies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _install(monkeypatch, tmp_path, outcome=CLEAN)

    _run(tmp_path, live=False)

    assert seen["feature_kwargs"]["budget"].max_cost_usd == 25.0


def test_max_cost_overrides_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SDLC_RUN_BUDGET_USD", "3")
    seen = _install(monkeypatch, tmp_path, outcome=CLEAN)

    _run(tmp_path, live=False, max_cost_usd=7.5)

    assert seen["feature_kwargs"]["budget"].max_cost_usd == 7.5


def test_the_review_is_charged_to_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = _install(monkeypatch, tmp_path, outcome=CLEAN)

    ctx = _run(tmp_path, live=False)

    assert seen["review_charged_to"] == ctx.run_id  # not the shared "unscoped" bucket


def test_a_resumed_run_starts_from_what_it_already_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = RunStore(root=tmp_path / "state")
    store.save(
        RunRecord(run_id="r-old", source="file://./spec.md", live=False, status="failed", spent_usd=2.5)
    )
    seen = _install(monkeypatch, tmp_path, outcome=CLEAN)

    _run(tmp_path, live=False, store=store, resume="r-old")

    assert seen["spent_at_build"] == 2.5


def test_sdlc_feature_reads_the_cap_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.cli.sdlc import _run_sdlc_feature

    monkeypatch.setenv("SDLC_RUN_BUDGET_USD", "4")
    seen: dict[str, Any] = {}

    async def _feature(source: str, **kwargs: Any) -> Any:  # noqa: ARG001
        seen.update(kwargs)
        return SimpleNamespace(
            passed=True,
            branch="b",
            worktree=".",
            files=[],
            iterations=1,
            pr_url=None,
            issue_key="K-1",
            title="t",
        )

    monkeypatch.setattr("orchestrator.sdlc.feature_runner.run_feature", _feature)
    # the summary printer may want more of the fake; the budget is what matters
    with contextlib.suppress(Exception):
        asyncio.run(
            _run_sdlc_feature(
                "file://./spec.md",
                intent_id=None,
                repo=None,
                model=None,
                max_refine=1,
                live=False,
                issue=None,
                base=None,
                layout_mode="auto",
                package_name=None,
                refresh=False,
                language="auto",
            )
        )
    assert seen["budget"].max_cost_usd == 4.0
