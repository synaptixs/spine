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
        self.layout = kwargs.get("layout")

    async def implement(self, **kwargs: Any) -> CodeChange:
        return CodeChange(files=[], summary="impl")

    async def author_tests(self, **kwargs: Any) -> CodeChange:
        return CodeChange(files=[], summary="tests")

    async def refine(self, **kwargs: Any) -> CodeChange:
        self.refine_calls += 1
        return CodeChange()  # no-op refine — must NOT crash the loop


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
    """Stands in for the semantic judge."""

    def __init__(self, verdict: str = "approve", blockers: list[str] | None = None) -> None:
        self.verdict, self.blockers = verdict, blockers or []
        self.calls = 0

    def __call__(self, llm: Any, **kwargs: Any) -> Any:
        return self

    async def review(self, *, path: str, issue_key: str, spec: Any = None) -> Any:
        self.calls += 1
        return SimpleNamespace(verdict=self.verdict, blockers=self.blockers, summary="judged", uncertain=[])


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

    # max_refine=3 → run/refine/run/refine/run.
    assert created and created[0].refine_calls == 2


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

    # The run never reached a PR, and the ticket still shows that work started.
    assert jira.transitions == ["In Progress"]


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
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    with pytest.raises(FeatureRunError, match="does not satisfy the ticket") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert exc.value.code == 1
    assert "still shows names only" in str(exc.value)
    assert judge.calls == 1


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
