"""run_feature early-exit paths (offline — before any LLM/git/Jira call)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.intake.factory import IntakeNotConfiguredError
from orchestrator.intake.jira import IssueTrackerError
from orchestrator.sdlc.codegen import CodeChange
from orchestrator.sdlc.feature_runner import FeatureRunError, _changed_files, run_feature


class _Spec:
    def __init__(self, intent_id: str) -> None:
        self.intent_id = intent_id

    def model_dump(self) -> dict[str, Any]:
        return {"title": "T", "intent_id": self.intent_id, "summary": "S", "acceptance_criteria": ["c"]}


class _Plan:
    def __init__(self, specs: list[_Spec]) -> None:
        self.specs = specs
        self.documents: list[Any] = []
        self.intents: list[Any] = []
        self.gaps: list[Any] = []
        self.blocked = False
        self.truncated = False


@pytest.fixture(autouse=True)
def _isolate_intake_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the intake cache + backlog ledger at per-test tmp paths so runs never
    read/write the real ~/.cache or a stray ./BACKLOG.md, and the stub is exercised."""
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_CACHE_DIR", str(tmp_path / "intake-cache"))
    monkeypatch.setenv("ORCHESTRATOR_BACKLOG_PATH", str(tmp_path / "BACKLOG.md"))
    # Use the in-process test env in unit tests — never spin up a real venv.
    monkeypatch.setenv("SDLC_TEST_ISOLATION", "local")


class _Service:
    def __init__(self, specs: list[_Spec]) -> None:
        self._specs = specs

    async def analyze(self, root_id: str) -> _Plan:
        return _Plan(self._specs)


def _patch_service(monkeypatch: pytest.MonkeyPatch, specs: list[_Spec]) -> None:
    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *a, **k: _Service(specs))


async def test_no_specs_raises_code_3(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_service(monkeypatch, [])
    with pytest.raises(FeatureRunError) as exc:
        await run_feature("file://./spec.md")
    assert exc.value.code == 3


async def test_intent_not_found_raises_code_3(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_service(monkeypatch, [_Spec("intent-a")])
    with pytest.raises(FeatureRunError, match="not found") as exc:
        await run_feature("file://./spec.md", intent_id="intent-missing")
    assert exc.value.code == 3


async def test_live_without_repo_fails_before_creating_a_jira_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    # Don't let run_feature reload SDLC_REPO_URL from a real .env, so the
    # no-repo path is exercised deterministically.
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    monkeypatch.delenv("SDLC_REPO_URL", raising=False)
    _patch_service(monkeypatch, [_Spec("intent-a")])
    # Fail-fast: raised before any Jira call, so no orphaned issue.
    with pytest.raises(FeatureRunError, match="needs a repo") as exc:
        await run_feature("file://./spec.md", live=True)
    assert exc.value.code == 2


async def test_unconfigured_source_raises_code_2(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: Any, **k: Any) -> Any:
        raise IntakeNotConfiguredError("not configured")

    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", _raise)
    with pytest.raises(FeatureRunError) as exc:
        await run_feature("confluence://x")
    assert exc.value.code == 2


class _StubCodegen:
    """Records the ``layout`` it was constructed with; implements/authors empty
    changes; refine is a no-op (empty change) — the exact shape the real adapter
    now returns when the model has nothing to edit. ``refine_calls`` lets a test
    prove the loop iterated rather than aborting on the first empty refine."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.refine_calls = 0
        self.revise_calls = 0
        self.blockers_seen: list[list[str]] = []
        self.gaps_seen: list[list[str]] = []
        self.layout = kwargs.get("layout")

    async def implement(self, **kwargs: Any) -> CodeChange:
        return CodeChange(files=[], summary="impl")

    async def author_tests(self, **kwargs: Any) -> CodeChange:
        return CodeChange(files=[], summary="tests")

    async def refine(self, **kwargs: Any) -> CodeChange:
        self.refine_calls += 1
        return CodeChange()  # no-op refine — must NOT crash the loop

    async def revise(self, **kwargs: Any) -> CodeChange:
        self.revise_calls += 1
        self.blockers_seen.append(list(kwargs.get("blockers") or []))
        return CodeChange()  # answers nothing — the judge loop must stop, not spin


class _FailingRunner:
    """Tests never go green, so the loop exhausts its refine budget."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(passed=False, returncode=1, output="E   assert 1 == 2")


class _PassingRunner:
    """Tests go green on the first run — exercises the happy path to commit."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(passed=True, returncode=0, output="1 passed")


def _aresult(value: Any) -> Any:
    """Wrap a value in an awaitable so a lambda can stand in for an async method."""

    async def _coro() -> Any:
        return value

    return _coro()


class _FakeJira:
    """Records whether the run created an issue or adopted one. ``missing``
    makes ``get_issue`` fail the way an unknown key does."""

    def __init__(
        self, *, missing: bool = False, worklog_fails: bool = False, transition_fails: bool = False
    ) -> None:
        self._transition_fails = transition_fails
        self.created: list[Any] = []
        self.adopted: list[str] = []
        self.worklogs: list[tuple[str, str, str]] = []
        # Ordered, because *when* a ticket moves is the thing being fixed.
        self.transitions: list[str] = []
        self._missing = missing
        self._worklog_fails = worklog_fails

    async def create_issue(self, request: Any) -> SimpleNamespace:
        self.created.append(request)
        return SimpleNamespace(key="DRY-1", url="")

    async def get_issue(self, issue_key: str) -> SimpleNamespace:
        if self._missing:
            raise IssueTrackerError(f"GET /issue/{issue_key} failed: HTTP 404")
        self.adopted.append(issue_key)
        return SimpleNamespace(key=issue_key, url=f"https://acme.atlassian.net/browse/{issue_key}")

    async def add_worklog(self, issue_key: str, *, time_spent: str, comment: str) -> None:
        if self._worklog_fails:
            raise IssueTrackerError("POST /worklog failed: HTTP 403")
        self.worklogs.append((issue_key, time_spent, comment))

    async def comment_issue(self, issue_key: str, body: str) -> None:
        return None

    async def transition_issue(self, issue_key: str, target: str) -> str:
        if self._transition_fails:
            raise IssueTrackerError(f"no transition to {target!r} available for {issue_key}")
        self.transitions.append(target)
        return target


class _Judge:
    """Stands in for the semantic judge.

    ``verdict`` may be a single verdict held for every call, or a list read in order —
    the latter is how a reviewer that rejects once and approves the correction is
    expressed. A list shorter than the number of calls holds on its last entry.
    """

    def __init__(
        self,
        verdict: str | list[str] = "approve",
        blockers: list[str] | None = None,
        *,
        unreviewed: bool = False,
    ) -> None:
        self.verdicts = [verdict] if isinstance(verdict, str) else list(verdict)
        self.blockers = blockers or []
        self.unreviewed = unreviewed
        self.calls = 0

    def __call__(self, llm: Any, **kwargs: Any) -> Any:
        return self

    async def review(self, *, path: str, issue_key: str, spec: Any = None) -> Any:
        verdict = self.verdicts[min(self.calls, len(self.verdicts) - 1)]
        self.calls += 1
        blockers = self.blockers if verdict == "request_changes" else []
        return SimpleNamespace(
            verdict=verdict,
            blockers=blockers,
            summary="judged",
            uncertain=[],
            unreviewed=self.unreviewed,
        )


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    runner: type,
    codegen: type[_StubCodegen] = _StubCodegen,
    jira: _FakeJira | None = None,
    workspaces: list[str] | None = None,
) -> list[_StubCodegen]:
    """Stub everything downstream of spec resolution so run_feature drives the
    real layout/scaffold/preflight + test-loop logic against a tmp worktree.
    Returns the list that captures each constructed stub codegen."""
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    monkeypatch.delenv("SDLC_REPO_URL", raising=False)
    _patch_service(monkeypatch, [_Spec("intent-a")])
    monkeypatch.setattr("orchestrator.core.llm.LiteLLMClient", lambda *a, **k: object())

    created: list[_StubCodegen] = []

    def _make_codegen(*a: Any, **k: Any) -> _StubCodegen:
        stub = codegen(*a, **k)
        created.append(stub)
        return stub

    monkeypatch.setattr("orchestrator.sdlc.codegen.LLMCodegenAdapter", _make_codegen)
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda *a, **k: None)
    monkeypatch.setattr("orchestrator.sdlc.testrunner.SubprocessTestRunner", runner)
    monkeypatch.setattr("orchestrator.intake.jira.JiraAdapter", lambda *a, **k: jira or _FakeJira())
    # The semantic judge now runs on every green change. Approve by default so these tests
    # keep testing what they were written for; the judge's own behaviour is covered where a
    # verdict is installed deliberately.
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve"))

    class _Forge:
        """A PR without a forge: the live path needs one to reach the In Review transition."""

        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def open_pr(self, **kwargs: Any) -> Any:
            return SimpleNamespace(url="https://github.com/x/y/pull/7")

    monkeypatch.setattr("orchestrator.sdlc.forge.GhPRAdapter", _Forge)
    monkeypatch.setattr(
        "orchestrator.sdlc.grounding.PKGCodegenGrounder",
        SimpleNamespace(from_repo=lambda path: SimpleNamespace(context_for_spec=lambda spec: "")),
    )

    async def _create(self_ws: Any, sdlc_id: str, issue_key: str) -> Path:
        if workspaces is not None:
            workspaces.append(issue_key)
        return tmp_path

    monkeypatch.setattr("orchestrator.sdlc.workspace.WorkspaceManager.create", _create)
    return created


async def test_empty_refine_ends_in_graceful_failed_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When refine yields no edits and tests stay red, the loop must raise the clean
    ``VERDICT: FAILED`` (exit 1) — never an unhandled CodegenError. Locks in the
    integration half of the no-op-refine fix; ``test_refine_tolerates_a_no_op_response``
    covers the adapter half.

    It now stops at the *first* empty refine rather than exhausting ``--max-refine``:
    with no file changed, the next run is byte-identical to the one that just failed,
    so further iterations only spend LLM calls to watch the same error.
    """
    created = _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)

    assert exc.value.code == 1
    assert created and created[0].refine_calls == 1


class _EditingCodegen(_StubCodegen):
    """Refine that actually edits something — the loop must keep going."""

    async def refine(self, **kwargs: Any) -> CodeChange:
        self.refine_calls += 1
        return CodeChange(files=["src/x.py"], summary="edited")


async def test_refine_that_changes_files_still_uses_the_whole_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The early stop must key on *no edits*, not on failure — a refine that edits
    code has earned another run, however red the suite still is."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, codegen=_EditingCodegen)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)

    # max_refine is an allowance of *correction attempts*, per kind of problem: three
    # refines, each followed by a run, then a fourth run that finds the budget spent.
    assert created and created[0].refine_calls == 3


async def test_greenfield_run_scaffolds_and_passes_layout_to_codegen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty worktree (auto) scaffolds a src/<pkg>/ skeleton and hands codegen
    a layout pinned to it — so generated paths stop being invented."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)

    result = await run_feature(
        "file://./spec.md",
        intent_id="intent-a",
        repo="https://github.com/x/Example-Service.",
    )

    assert result.passed
    # scaffold ran (greenfield → new) and wrote a runnable skeleton
    assert (tmp_path / "pyproject.toml").is_file()
    assert (tmp_path / "src" / "example_service" / "__init__.py").is_file()
    # the local backlog ledger is written during the run (isolated via the fixture)
    assert (tmp_path / "BACKLOG.md").is_file()
    # codegen received the pinned layout
    layout = created[0].layout
    assert layout is not None
    assert layout.mode == "new"
    assert layout.package_name == "example_service"
    assert layout.source_dir == "src/example_service"


# ---- run-cost telemetry (worklog) ------------------------------------------


async def test_a_failed_live_run_still_logs_what_it_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case the worklog exists for: the run bought no PR, so the spend needs explaining.
    This path also runs before the forge, so it exercises the poster without opening a PR."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, jira=jira)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
        )

    assert len(jira.worklogs) == 1
    issue_key, time_spent, body = jira.worklogs[0]
    assert issue_key == "SSPN-1"
    assert time_spent.endswith("m")  # a real duration, never zero — Jira rejects zero
    assert "**Verdict:** FAILED" in body


async def test_a_safe_run_logs_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--safe`` makes no external write, and telemetry is no exception."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, jira=jira)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a")

    assert jira.worklogs == []


async def test_a_worklog_failure_never_changes_the_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Telemetry is not the work: a tracker that rejects the worklog must not decide the
    run's outcome, nor replace the real error with its own."""
    jira = _FakeJira(worklog_fails=True)
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, jira=jira)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED") as exc:
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
        )

    assert exc.value.code == 1  # the test verdict, not a tracker error
    assert jira.worklogs == []


# ---- adopting an issue that already exists (--issue) -----------------------


async def test_adopted_issue_is_used_and_nothing_is_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--issue`` points the run at work that is already tracked. Creating
    regardless is what produced duplicate, epic-less issues (SSPN-8), so the
    create path must not run at all — and the branch must carry the real key."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, jira=jira)

    result = await run_feature(
        "file://./spec.md", intent_id="intent-a", repo="https://x/widget", issue="sspn-9"
    )

    assert result.passed
    assert jira.created == []  # the whole point
    assert jira.adopted == ["SSPN-9"]  # normalized, not passed through as typed
    assert result.issue_key == "SSPN-9"
    assert result.branch.endswith("/SSPN-9")


async def test_without_an_issue_the_run_still_creates_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Adoption is opt-in: the default path is unchanged."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, jira=jira)

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert len(jira.created) == 1 and jira.adopted == []
    assert result.issue_key == "DRY-1"


async def test_unknown_issue_key_fails_before_the_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A key that doesn't resolve is a wrong run, not a late surprise: it must
    die before the worktree, codegen and any test run."""
    jira = _FakeJira(missing=True)
    workspaces: list[str] = []
    created = _install_pipeline(
        monkeypatch, tmp_path, runner=_PassingRunner, jira=jira, workspaces=workspaces
    )

    with pytest.raises(FeatureRunError, match="cannot adopt SSPN-404") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget", issue="SSPN-404")

    assert exc.value.code == 2
    assert workspaces == []  # no worktree
    assert created == []  # no codegen adapter constructed


async def test_malformed_issue_key_is_rejected_before_intake() -> None:
    """Nothing is patched here: if the shape check didn't run first, intake would
    raise its own error instead (a different code), so this pins the ordering."""
    with pytest.raises(FeatureRunError, match="is not an issue key") as exc:
        await run_feature("file://./spec.md", issue="the CB epic")
    assert exc.value.code == 2


async def test_existing_package_is_not_scaffolded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A worktree with a recognizable package (auto) is detected as existing and
    left untouched — no scaffold, layout follows the repo."""
    (tmp_path / "src" / "widget").mkdir(parents=True)
    (tmp_path / "src" / "widget" / "__init__.py").write_text("")
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert result.passed
    assert not (tmp_path / "pyproject.toml").exists()  # not scaffolded
    layout = created[0].layout
    assert layout is not None and layout.mode == "existing" and layout.package_name == "widget"


async def test_changed_files_excludes_preexisting_repo_content(tmp_path: Path) -> None:
    """The summary lists what THIS run added (scaffold + generated), not pre-existing
    files already committed in the target repo (e.g. loose stack_decision.py)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "stack_decision.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "page.py").write_text("y = 2\n")  # this run's output (untracked)

    files = await _changed_files(tmp_path)
    assert "src/page.py" in files
    assert "stack_decision.py" not in files  # pre-existing, excluded


async def test_changed_files_falls_back_to_py_scan_when_not_git(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("")
    assert await _changed_files(tmp_path) == ["a.py"]


async def test_changed_files_excludes_build_output(tmp_path: Path) -> None:
    """Maven target/, venv, caches — test-run byproducts — stay out of the summary."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text("class App {}\n")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "App.class").write_text("x")
    files = await _changed_files(tmp_path)
    assert "src/App.java" in files
    assert not any(f.startswith("target/") for f in files)


async def test_language_java_requires_toolchain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--language java now runs the Java pipeline (layout/scaffold/Maven runner), but
    preflights the JDK+Maven toolchain — fail fast with a clear message when absent."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testenv.java_toolchain_available", lambda: False)
    with pytest.raises(FeatureRunError, match="JDK \\+ Maven") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", language="java")
    assert exc.value.code == 2


async def test_language_typescript_requires_toolchain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--language typescript runs the TS pipeline (layout/Vitest scaffold/Node runner)
    but preflights Node+npm — fail fast with a clear message when absent."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testenv.node_toolchain_available", lambda *a, **k: False)
    with pytest.raises(FeatureRunError, match="Node.js") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", language="typescript")
    assert exc.value.code == 2


async def test_language_go_requires_toolchain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--language go runs the Go pipeline (root-package scaffold / `go build`+`go test`
    runner) but preflights the `go` toolchain — fail fast with a clear message when absent."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testenv.go_toolchain_available", lambda: False)
    with pytest.raises(FeatureRunError, match="Go toolchain") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", language="go")
    assert exc.value.code == 2


def test_resolve_language_detects_go(tmp_path: Path) -> None:
    from orchestrator.sdlc.feature_runner import _resolve_language

    (tmp_path / "go.mod").write_text("module widget\n")
    (tmp_path / "widget.go").write_text("package widget\n")
    assert _resolve_language(tmp_path, "auto") == "go"


async def test_missing_pytest_fails_fast_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If pytest isn't importable by the orchestrator interpreter, the run fails
    fast (code 2) before the loop, instead of letting refine flail at pyproject."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testrunner.pytest_available", lambda *a, **k: _aresult(False))

    with pytest.raises(FeatureRunError, match="pytest is required") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a")
    assert exc.value.code == 2


# ---- the ticket's status follows the work (SSPN-34) -------------------------


async def test_a_live_run_moves_the_ticket_in_progress_before_writing_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It used to move to In Progress *after* the PR was opened — so a ticket sat in To Do
    through design, codegen and the whole test loop, and a run that died at codegen left no
    sign it had ever been picked up."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, jira=jira)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
        )

    # In Progress before codegen so the board shows the work being picked up — and handed
    # back when the run produces nothing, rather than left claiming someone is on it.
    assert jira.transitions == ["In Progress", "To Do"]


async def test_a_safe_run_moves_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, jira=jira)

    with pytest.raises(FeatureRunError):
        await run_feature("file://./spec.md", intent_id="intent-a")

    assert jira.transitions == []


async def test_a_workflow_without_the_status_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tracker's workflow is not the run's to fail on: a board with different status names
    should still get its code."""
    jira = _FakeJira(transition_fails=True)
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, jira=jira)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):  # the test verdict, not a tracker error
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
        )


async def test_done_is_never_set_by_the_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Done means a human looked at it. `sdlc complete` is where that lives."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, jira=jira)

    with pytest.raises(FeatureRunError):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
        )

    assert "Done" not in jira.transitions


async def test_a_live_run_moves_the_ticket_to_in_review_when_the_pr_opens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The step the workflow described and the code never had: the change is written, tested
    and waiting on a human. Order matters as much as the transitions — In Progress at the
    start, In Review at the end."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, jira=jira)

    result = await run_feature(
        "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
    )

    assert result.pr_url == "https://github.com/x/y/pull/7"
    assert jira.transitions == ["In Progress", "In Review"]


async def test_a_live_run_with_an_injected_spec_reaches_the_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`autorun` always injects a spec — it does intake itself — and the live path then
    reached for `local_backlog` and `plan`, which are only bound when this function does its
    own intake. It fired *after* codegen and the whole test loop succeeded and immediately
    before the PR opened: the worst possible moment, full spend and nothing to show."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, jira=jira)

    result = await run_feature(
        "file://./spec.md",
        repo="https://x/widget",
        live=True,
        issue="SSPN-1",
        spec={
            "title": "Injected",
            "intent_id": "intent-a",
            "summary": "s",
            "acceptance_criteria": ["c"],
        },
    )

    assert result.pr_url == "https://github.com/x/y/pull/7"
    assert jira.transitions == ["In Progress", "In Review"]


async def test_intake_driven_runs_still_write_the_backlog_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guard must not cost the ledger its normal case: when this function does its own
    intake there *is* a plan, and BACKLOG.md is still written in both places."""
    written: list[str] = []
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, jira=jira)
    monkeypatch.setattr(
        "orchestrator.intake.backlog_doc.write_backlog",
        lambda path, *a, **k: written.append(str(path)),
    )

    await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True)

    # Once for the local ledger during intake, then into the worktree and back locally.
    assert len(written) >= 2


# ---- the change must satisfy the ticket, not just its own tests (SSPN-37) ---


async def test_a_change_that_does_not_satisfy_the_ticket_is_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failure a real run shipped: green tests, clean verifiers, and a change that
    touched the wrong module and left the requested behaviour exactly as it was. Neither the
    suite nor the verifiers ask the only question the person who filed the ticket cares about.
    """
    judge = _Judge("request_changes", ["`mcp contracts` still shows names only"])
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    with pytest.raises(FeatureRunError, match="does not satisfy the ticket") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert exc.value.code == 1
    assert "still shows names only" in str(exc.value)
    # The stub's revise answers nothing, so the loop stops on the first empty revision
    # rather than re-judging identical files.
    assert judge.calls == 1
    assert created[0].revise_calls == 1
    assert created[0].blockers_seen == [["`mcp contracts` still shows names only"]]


async def test_a_satisfying_change_proceeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    judge = _Judge("approve")
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert result.passed and judge.calls == 1


async def test_the_judge_is_not_asked_about_a_failing_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A red suite is already a verdict — paying a model to confirm it would be waste."""
    judge = _Judge("approve")
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED after"):
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert judge.calls == 0


def test_test_paths_are_recognised() -> None:
    """The proof run has to know which half of the change to set aside."""
    from orchestrator.sdlc.feature_runner import _is_test_path

    assert _is_test_path("tests/sdlc/test_thing.py")
    assert _is_test_path("test_thing.py")
    assert _is_test_path("src/pkg/thing_test.py")
    assert not _is_test_path("src/orchestrator/mcp/contract.py")
    assert not _is_test_path("src/latest/protest.py")


# ---- the judge must be able to ask for a correction, not only veto (SSPN-14) ----


class _RevisingCodegen(_StubCodegen):
    """Answers the reviewer with a real edit — a doc, the case that motivated this."""

    async def revise(self, **kwargs: Any) -> CodeChange:
        self.revise_calls += 1
        self.blockers_seen.append(list(kwargs.get("blockers") or []))
        return CodeChange(files=["USER_GUIDE.md"], summary="documented the new output")


async def test_a_rejected_change_gets_a_chance_to_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The live dead end: a ticket asking for documentation could never pass, because the
    judge required a doc and every codegen prompt forbade writing one, with no path from the
    rejection back into codegen. One rejection, one revision, one approval — and it ships."""
    judge = _Judge(["request_changes", "approve"], ["documented in USER_GUIDE step 9"])
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_RevisingCodegen)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert result.passed
    assert judge.calls == 2  # rejected, then asked again about the corrected change
    assert created[0].revise_calls == 1
    assert created[0].blockers_seen == [["documented in USER_GUIDE step 9"]]


async def test_a_judge_that_never_relents_stops_at_its_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reviewer that cannot be satisfied must cost a bounded number of calls, not loop."""
    judge = _Judge("request_changes", ["still unmet"])
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_RevisingCodegen)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    with pytest.raises(FeatureRunError, match="does not satisfy the ticket"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", max_judge_revisions=2
        )

    assert judge.calls == 3  # the first verdict plus one per revision
    assert created[0].revise_calls == 2


async def test_a_revision_that_breaks_the_tests_does_not_ship(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Satisfying a reviewer by breaking the code is not satisfying the ticket."""

    class _GoesRedAfterRevision:
        """Green until a revision happens, red afterwards."""

        runs = 0

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def run(self, *, path: str) -> SimpleNamespace:
            _GoesRedAfterRevision.runs += 1
            green = _GoesRedAfterRevision.runs <= 2  # first suite run + the proof run
            return SimpleNamespace(passed=green, returncode=0 if green else 1, output="E   boom")

    judge = _Judge("request_changes", ["needs a doc"])
    _install_pipeline(monkeypatch, tmp_path, runner=_GoesRedAfterRevision, codegen=_RevisingCodegen)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    with pytest.raises(FeatureRunError, match="broke the test suite"):
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")


# ---- a revision that breaks the suite gets repaired, not abandoned (SSPN-14) ----

_BROKEN = {"yes": False}


class _RepairableCodegen(_StubCodegen):
    """Revises by breaking the build, then repairs it — the live shape exactly: the
    revision added one keyword argument to a model that forbids extras."""

    async def revise(self, **kwargs: Any) -> CodeChange:
        self.revise_calls += 1
        self.blockers_seen.append(list(kwargs.get("blockers") or []))
        _BROKEN["yes"] = True
        return CodeChange(files=["USER_GUIDE.md"], summary="documented it")

    async def refine(self, **kwargs: Any) -> CodeChange:
        self.refine_calls += 1
        _BROKEN["yes"] = False
        return CodeChange(files=["src/orchestrator/mcp/contract.py"], summary="dropped the bad kwarg")


class _UnrepairableCodegen(_RepairableCodegen):
    """Edits on every repair pass and never actually fixes it — the budget must bound it."""

    async def refine(self, **kwargs: Any) -> CodeChange:
        self.refine_calls += 1
        return CodeChange(files=["src/orchestrator/mcp/contract.py"], summary="still broken")


class _RunnerTrackingCodegen:
    """Green unless the codegen fakes have currently broken the tree."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        broken = _BROKEN["yes"]
        return SimpleNamespace(
            passed=not broken,
            returncode=1 if broken else 0,
            output="E   ValidationError: extra_forbidden 'display_type'" if broken else "1 passed",
        )


async def test_a_revision_that_breaks_the_suite_is_repaired_and_ships(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run that motivated this: revision 2 answered the reviewer correctly and passed a
    keyword argument to a Pydantic model with extra='forbid'. Dropping it is a one-line fix
    the run had every tool to make and, until now, no path to making."""
    _BROKEN["yes"] = False
    judge = _Judge(["request_changes", "approve"], ["_type_label is dead code"])
    created = _install_pipeline(
        monkeypatch, tmp_path, runner=_RunnerTrackingCodegen, codegen=_RepairableCodegen
    )
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert result.passed
    assert created[0].revise_calls == 1
    assert created[0].refine_calls == 1  # one repair pass was enough
    assert judge.calls == 2  # and the judge was asked again about the repaired change


async def test_an_unrepairable_break_still_stops_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Repair is bounded: code that stays red must never reach a PR, however many passes."""
    _BROKEN["yes"] = False
    judge = _Judge("request_changes", ["_type_label is dead code"])
    created = _install_pipeline(
        monkeypatch, tmp_path, runner=_RunnerTrackingCodegen, codegen=_UnrepairableCodegen
    )
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    with pytest.raises(FeatureRunError, match="could not be repaired"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", max_revision_repairs=2
        )

    assert created[0].refine_calls == 2  # spent the budget, then stopped


async def test_an_unreviewed_change_never_reaches_the_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run that made this necessary: the judge's reply was unreadable, `_parse` mapped
    it to `comment`, the caller read `comment` as "not a blocker", and the pipeline committed
    a change whose only new function was never called. No verdict is not a pass."""
    judge = _Judge("comment", unreviewed=True)
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_RevisingCodegen)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    with pytest.raises(FeatureRunError, match="unreviewed") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert exc.value.code == 1
    assert judge.calls == 1
    assert created[0].revise_calls == 0  # nothing to revise toward — it stops for a human


# ---- the last gate before any write is a person, not a model (SSPN-14) ----


async def test_a_declined_gate_commits_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every automatic check can be satisfied by a change that does nothing — one run
    committed a helper it never called, with green tests, a passing proof check and an
    unreadable judge. A person reading the diff is the check none of that fools."""
    seen: dict[str, Any] = {}

    async def _decline(path: Path, files: list[str]) -> bool:
        seen["path"], seen["files"] = path, files
        return False

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve"))

    with pytest.raises(FeatureRunError, match="DECLINED at the human gate") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget", gate=_decline)

    assert exc.value.code == 1
    assert seen["path"] == tmp_path  # the gate is shown where the change actually is


async def test_an_approved_gate_lets_the_run_finish(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[int] = []

    async def _approve(path: Path, files: list[str]) -> bool:
        calls.append(1)
        return True

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve"))

    result = await run_feature(
        "file://./spec.md", intent_id="intent-a", repo="https://x/widget", gate=_approve
    )

    assert result.passed
    assert len(calls) == 1  # asked exactly once, not per stage


async def test_the_gate_is_not_reached_by_a_failing_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No point asking a human to approve something the pipeline already rejected."""
    asked = []

    async def _gate(path: Path, files: list[str]) -> bool:
        asked.append(1)
        return True

    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve"))

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED after"):
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget", gate=_gate)

    assert not asked


# ---- green tests are necessary, not sufficient (SSPN-14) ----


async def test_a_type_error_on_a_changed_line_is_not_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Three runs committed code the repo's own `mypy src tests` rejects — a missing
    `from typing import Any` twice, and an attribute the class does not have once. Each was
    one deterministic line of output, and each shipped, because the generated tests exercise
    new helpers directly and never import the module the ticket was about."""
    from orchestrator.sdlc import feature_runner as fr

    calls = {"n": 0}

    async def _typecheck(path: Path, testenv: Any, files: list[str], emit: Any) -> str:
        calls["n"] += 1
        # Clean only once the refine pass has run — the loop must not call it green before.
        return "" if calls["n"] > 1 else 'cli.py:1117: error: "MCPToolHandler" has no attribute'

    monkeypatch.setattr(fr, "_typecheck_the_change", _typecheck)
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_EditingCodegen)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve"))

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert result.passed
    assert created[0].refine_calls == 1  # the type error went back to refine, like a failure


async def test_the_type_errors_are_what_refine_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Refine has to see the type errors, not the green pytest output it would learn nothing from."""
    from orchestrator.sdlc import feature_runner as fr

    seen: list[str] = []

    class _RecordingCodegen(_StubCodegen):
        async def refine(self, **kwargs: Any) -> CodeChange:
            self.refine_calls += 1
            seen.append(str(kwargs.get("failures")))
            return CodeChange(files=["src/x.py"], summary="fixed the type error")

    async def _typecheck(path: Path, testenv: Any, files: list[str], emit: Any) -> str:
        return "" if seen else "TYPE ERRORS introduced by this change (fix these):\ncli.py:1117: error: boom"

    monkeypatch.setattr(fr, "_typecheck_the_change", _typecheck)
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_RecordingCodegen)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve"))

    await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert seen and "cli.py:1117" in seen[0]


def test_typing_hygiene_is_filtered_but_real_defects_are_not() -> None:
    """A run spent two of its three refine passes chasing an unused `type: ignore` in a
    generated test while the `attr-defined` bug that broke the command went unfixed."""
    from orchestrator.sdlc.feature_runner import _is_typing_hygiene

    assert _is_typing_hygiene('t.py:1: error: Unused "type: ignore" comment  [unused-ignore]')
    assert _is_typing_hygiene("t.py:1: error: Untyped decorator makes f untyped  [misc]")
    assert not _is_typing_hygiene(
        'c.py:1126: error: "MCPToolHandler" has no attribute "tool"  [attr-defined]'
    )
    assert not _is_typing_hygiene('c.py:45: error: Name "Any" is not defined  [name-defined]')


def test_only_errors_on_changed_lines_count() -> None:
    """A worktree venv has runtime deps only, so a whole-project run reports hundreds of
    pre-existing errors no generated change caused. A gate that fails every run gets removed."""
    from orchestrator.sdlc.feature_runner import _error_is_on_a_changed_line

    touched = {"src/a.py": {10, 11}}
    assert _error_is_on_a_changed_line("src/a.py:10: error: boom  [attr-defined]", touched)
    assert not _error_is_on_a_changed_line("src/a.py:99: error: pre-existing  [attr-defined]", touched)
    assert not _error_is_on_a_changed_line("src/other.py:10: error: untouched  [attr-defined]", touched)


# ---- a change nothing tests is not a tested change (SSPN-14) ----


class _PerFileRunner:
    """A suite that depends on one file's change and no other.

    Stashing a tracked file reverts its *content*; it does not remove the file. So the
    dependency is expressed the way a real test does — by what the file says.
    """

    depends_on = "src/orchestrator/mcp/contract.py"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        changed = (Path(path) / _PerFileRunner.depends_on).read_text(encoding="utf-8").strip() == "x = 2"
        return SimpleNamespace(passed=changed, returncode=0 if changed else 1, output="E   assert 1 == 2")


async def test_a_file_no_test_reaches_is_reported(tmp_path: Path) -> None:
    """The live miss: the helper in contract.py was correct and exhaustively tested, and the
    cli.py wiring that called it passed `{}` on every call. Reverting the whole change broke
    the helper's import so the suite went red and the old proof check "held" — while the line
    that actually mattered was covered by nothing."""
    from orchestrator.sdlc.feature_runner import _files_no_test_exercises

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    for rel in ("src/orchestrator/mcp/contract.py", "src/orchestrator/cli.py", "tests/test_it.py"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    # Both production files change; only contract.py is depended on by the suite.
    (tmp_path / "src/orchestrator/mcp/contract.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "src/orchestrator/cli.py").write_text("x = 2\n", encoding="utf-8")

    files = ["src/orchestrator/mcp/contract.py", "src/orchestrator/cli.py", "tests/test_it.py"]
    gaps = await _files_no_test_exercises(tmp_path, files, _PerFileRunner(), lambda _m: None)

    assert gaps == ["src/orchestrator/cli.py"]  # the wiring, exactly


async def test_a_coverage_gap_asks_for_tests_not_an_implementation_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A gap means the tests are wrong, not the code — so it goes to author_tests, and
    refine is never asked to change an implementation that is already correct."""
    from orchestrator.sdlc import feature_runner as fr

    probes = {"n": 0}

    async def _probe(path: Path, files: list[str], runner: Any, emit: Any) -> list[str]:
        probes["n"] += 1
        return ["src/orchestrator/cli.py"] if probes["n"] == 1 else []

    class _CoveringCodegen(_StubCodegen):
        async def author_tests(self, **kwargs: Any) -> CodeChange:
            gaps = kwargs.get("gaps")
            if gaps:
                self.gaps_seen.append(list(gaps))
                return CodeChange(files=["tests/test_cli.py"], summary="cover the wiring")
            return CodeChange(files=[], summary="tests")

    monkeypatch.setattr(fr, "_files_no_test_exercises", _probe)
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_CoveringCodegen)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve"))

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert result.passed
    assert created[0].gaps_seen == [["src/orchestrator/cli.py"]]
    assert created[0].refine_calls == 0  # the implementation was never blamed for a test gap


# ---- a check that cannot run must not look like a check that passed (SSPN-14) ----


def _seeded_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


async def test_reading_the_diff_leaves_the_index_alone(tmp_path: Path) -> None:
    """`git add -N -A` made untracked files visible to `git diff` and made every later
    `git stash push` fail with "Entry ... not uptodate. Cannot merge." That silently
    disabled the proof pass for three runs and made the coverage probe skip every file
    while announcing that all of them were exercised."""
    from orchestrator.sdlc.feature_runner import _changed_line_ranges, _git_out

    root = _seeded_repo(tmp_path)
    (root / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    (root / "src" / "brand_new.py").write_text("y = 1\ny = 2\n", encoding="utf-8")

    ranges = await _changed_line_ranges(root)

    assert ranges["src/mod.py"] == {1}
    assert ranges["src/brand_new.py"] == {1, 2}  # a new file has no diff; every line counts

    # The check that regressed: stashing must still work afterwards.
    stashed, why = await _git_out(root, "stash", "push", "--include-untracked", "--quiet", "--", "src/mod.py")
    assert stashed, f"reading the diff broke the stash: {why}"
    await _git_out(root, "stash", "pop", "--quiet")


async def test_git_failures_say_why() -> None:
    """ "could not set the change aside" was the entire diagnosis for three runs, because
    stderr went to DEVNULL. Git names the cause in one line."""
    from orchestrator.sdlc.feature_runner import _git_out

    ok, why = await _git_out(Path("/"), "stash", "push", "--", "nope.py")

    assert not ok
    assert why  # whatever git said, we kept it


async def test_an_unprobed_file_is_not_reported_as_covered(tmp_path: Path) -> None:
    """The bug this check had itself: a failed stash `continue`d, so a run in which nothing
    could be probed printed "every changed file is exercised by a test"."""
    from orchestrator.sdlc.feature_runner import _files_no_test_exercises

    root = _seeded_repo(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_it.py").write_text("def test_x() -> None:\n    assert True\n", encoding="utf-8")
    (root / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    # Make the stash fail the way intent-to-add did.
    subprocess.run(["git", "add", "-N", "-A"], cwd=root, check=True)

    said: list[str] = []
    gaps = await _files_no_test_exercises(
        root, ["src/mod.py", "tests/test_it.py"], _PassingRunner(), said.append
    )

    assert gaps == []  # nothing was proven unexercised...
    assert any("COULD NOT probe" in m for m in said)  # ...because nothing was probed
    assert not any("every changed file is exercised" in m for m in said)


async def test_each_check_gets_its_own_allowance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A live run spent iterations 1-2 on test failures and 3-4 on type errors, so when the
    coverage probe found its gap on iteration 5 there was nothing left to answer it. The
    change was fixable; the run reported FAILED because three checks shared one pool."""
    from orchestrator.sdlc import feature_runner as fr

    types_seen = {"n": 0}
    gaps_seen = {"n": 0}

    async def _typecheck(path: Path, testenv: Any, files: list[str], emit: Any) -> str:
        types_seen["n"] += 1
        return "cli.py:1: error: boom  [attr-defined]" if types_seen["n"] <= 2 else ""

    async def _probe(path: Path, files: list[str], runner: Any, emit: Any) -> list[str]:
        gaps_seen["n"] += 1
        return ["src/orchestrator/cli.py"] if gaps_seen["n"] == 1 else []

    class _Covering(_StubCodegen):
        async def refine(self, **kwargs: Any) -> CodeChange:
            self.refine_calls += 1
            return CodeChange(files=["src/x.py"], summary="fixed the type error")

        async def author_tests(self, **kwargs: Any) -> CodeChange:
            if kwargs.get("gaps"):
                self.gaps_seen.append(list(kwargs["gaps"]))
                return CodeChange(files=["tests/test_cli.py"], summary="cover the wiring")
            return CodeChange(files=[], summary="tests")

    monkeypatch.setattr(fr, "_typecheck_the_change", _typecheck)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _probe)
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_Covering)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve"))

    # Two type errors consume the type allowance; the coverage gap must still be answerable.
    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=2)

    assert result.passed
    assert created[0].refine_calls == 2  # both type errors corrected
    assert created[0].gaps_seen == [["src/orchestrator/cli.py"]]  # and the gap still got its turn


async def test_a_codegen_error_hands_the_ticket_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Releasing the ticket was wired only into the "tests stayed red" path, so a codegen
    error — the thing most likely to end a run early — sailed past it. A live run failed
    at author_tests and left SSPN-14 In Progress with no branch and no PR."""
    from orchestrator.sdlc.codegen import CodegenError

    class _ExplodingCodegen(_StubCodegen):
        async def author_tests(self, **kwargs: Any) -> CodeChange:
            raise CodegenError("model output had no 'files' list")

    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_ExplodingCodegen, jira=jira)

    with pytest.raises(CodegenError):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
        )

    assert jira.transitions == ["In Progress", "To Do"]
