"""run_feature early-exit paths (offline — before any LLM/git/Jira call)."""

from __future__ import annotations

import re
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


class _PassingRequiredBehavior:
    """A manifest whose checks are green on the first look."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(passed=True, items=[], output="rb passed")


class _FailingRequiredBehavior:
    """A manifest that never goes green — exhausts its own refine budget."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(
            passed=False,
            items=[SimpleNamespace(environment_blocked=False)],
            output="rb failed",
        )


class _EventuallyPassingRequiredBehavior:
    """Fails the first look, passes every one after — proves refine -> re-check -> pass."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        passed = self.calls > 1
        items = [] if passed else [SimpleNamespace(environment_blocked=False)]
        return SimpleNamespace(passed=passed, items=items, output="" if passed else "rb failed once")


class _EnvironmentBlockedRequiredBehavior:
    """A manifest requirement whose command can't even run — not a code problem."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            passed=False,
            items=[SimpleNamespace(environment_blocked=True)],
            output="command not found",
        )


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
        criteria: list[Any] | None = None,
    ) -> None:
        self.verdicts = [verdict] if isinstance(verdict, str) else list(verdict)
        self.blockers = blockers or []
        self.unreviewed = unreviewed
        self.criteria = criteria
        self.calls = 0

    def __call__(self, llm: Any, **kwargs: Any) -> Any:
        return self

    async def review(self, *, path: str, issue_key: str, spec: Any = None) -> Any:
        verdict = self.verdicts[min(self.calls, len(self.verdicts) - 1)]
        self.calls += 1
        blockers = self.blockers if verdict == "request_changes" else []
        extra = {"criteria": self.criteria} if self.criteria is not None else {}
        return SimpleNamespace(
            verdict=verdict,
            blockers=blockers,
            summary="judged",
            uncertain=[],
            unreviewed=self.unreviewed,
            **extra,
        )


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    runner: type,
    codegen: type[_StubCodegen] = _StubCodegen,
    jira: _FakeJira | None = None,
    workspaces: list[str] | None = None,
    required_behavior: type | None = None,
) -> list[_StubCodegen]:
    """Stub everything downstream of spec resolution so run_feature drives the
    real layout/scaffold/preflight + test-loop logic against a tmp worktree.
    Returns the list that captures each constructed stub codegen.

    ``required_behavior`` is left unpatched by default: the real
    ``SubprocessRequiredBehaviorRunner`` self-skips without a `.spine/required-behavior.yaml`
    in ``tmp_path``, which none of these fixtures create — so every existing test here
    exercises the new kind as an inert no-op, same as before SSPN-119.
    """
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    monkeypatch.delenv("SDLC_REPO_URL", raising=False)
    # A developer who turned the stall check off must not change what these tests mean (B80).
    monkeypatch.delenv("SDLC_REFINE_PROGRESS", raising=False)
    # Nor may a developer who turned the proof bundle off (proof-bundle D7).
    monkeypatch.delenv("SDLC_PROOF_BUNDLE", raising=False)
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
    if required_behavior is not None:
        monkeypatch.setattr(
            "orchestrator.sdlc.required_behavior.SubprocessRequiredBehaviorRunner", required_behavior
        )
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


class _ChangingFailureRunner:
    """Tests never go green, and the failure is different on every run."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(passed=False, returncode=1, output=f"E   assert {self.calls} == 99")


class _TimingOnlyRunner:
    """The same failure every run; only the reported duration differs."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            passed=False, returncode=1, output=f"E   assert 1 == 2\n1 failed in 0.{self.calls:02d}s"
        )


class _NamedChangingFailureRunner:
    """A runner that names its failing test (as pytest's does) while the assertion keeps moving."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            passed=False,
            returncode=1,
            output=f"E   assert {self.calls} == 99",
            problems=("tests/test_x.py::test_a",),
        )


async def test_refine_that_changes_the_failure_still_uses_the_whole_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The early stop must key on *progress*, not on failure: a refine whose edit changes what
    fails has earned another run, however red the suite still is. (B80 narrowed this from
    "a refine that edits code has earned another run" — an edit that leaves the failure exactly
    as it was has not; see the next tests.)"""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_ChangingFailureRunner, codegen=_EditingCodegen)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)

    # max_refine is an allowance of *correction attempts*, per kind of problem: three
    # refines, each followed by a run, then a fourth run that finds the budget spent.
    assert created and created[0].refine_calls == 3


async def test_two_edits_that_leave_the_failure_unchanged_stop_the_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B80: refine edits files, the next run fails with exactly the same output, twice in a row.
    Another edit cannot be expected to change that, so the loop stops instead of spending the
    rest of its allowance: three identical failing runs, two edits that changed nothing."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, codegen=_EditingCodegen)
    lines: list[str] = []

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5, log=lines.append)

    assert exc.value.code == 1  # a failed run, exactly like an exhausted allowance
    assert created and created[0].refine_calls == 2
    stop = [ln for ln in lines if ln.startswith("[refine] stopping")]
    assert stop == [
        "[refine] stopping — the last 3 test runs failed identically "
        "after 2 edits that did not change the failure"
    ], lines
    assert not any("out of tests attempts" in ln for ln in lines)  # said as a stall, once
    # The failing line comes from the output, and nothing in it names a test this run wrote, so the
    # stop says nothing about authorship and lists no criteria.
    assert any(ln.startswith("[refine]   failing:") and "assert 1 == 2" in ln for ln in lines), lines
    assert not any("written by this run" in ln or "criteria" in ln for ln in lines), lines


async def test_a_duration_that_differs_does_not_make_the_failure_different(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Runners print how long they took. That is the part of the output that is never the same
    twice, so it cannot be what tells two failures apart."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_TimingOnlyRunner, codegen=_EditingCodegen)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert created and created[0].refine_calls == 2


async def test_a_named_failure_whose_assertion_keeps_changing_uses_the_whole_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same test id failing is not "the same failure": the output is compared too, so a
    refine that is moving the assertion toward green is not stopped for failing the same test."""
    created = _install_pipeline(
        monkeypatch, tmp_path, runner=_NamedChangingFailureRunner, codegen=_EditingCodegen
    )

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)

    assert created and created[0].refine_calls == 3


async def test_the_off_switch_restores_the_whole_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, codegen=_EditingCodegen)
    monkeypatch.setenv("SDLC_REFINE_PROGRESS", "0")  # after the helper, which clears it
    lines: list[str] = []

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5, log=lines.append)

    assert created and created[0].refine_calls == 5
    assert not any("failed identically" in ln for ln in lines)


async def test_a_stalled_required_behavior_check_still_uses_its_whole_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the tests pool watches for a stall (B80). The required-behavior pool gets the whole
    allowance even when its output never changes: nothing here has measured that case."""
    created = _install_pipeline(
        monkeypatch,
        tmp_path,
        runner=_PassingRunner,
        codegen=_EditingCodegen,
        required_behavior=_FailingRequiredBehavior,
    )

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://github.com/x/widget", max_refine=5
        )

    assert created and created[0].refine_calls == 5


class _TimedOutRunner:
    """Every run hangs: the runners report that with a fixed message and returncode -1."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(passed=False, returncode=-1, output="test run timed out")


class _ClippedRunner:
    """A long report the runner clipped to its last 4,000 characters; the tail never changes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(passed=False, returncode=1, output="E   assert 1 == 2\n" * 400)


class _RedHangRedRunner:
    """The same failure every run, except the second, which hangs."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        if self.calls == 2:
            return SimpleNamespace(passed=False, returncode=-1, output="test run timed out")
        return SimpleNamespace(passed=False, returncode=1, output="E   assert 1 == 2")


async def test_a_hang_between_two_identical_failures_starts_the_count_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run that cannot show whether anything changed is neither a repeat nor the start of one: the count
    begins again after it. Red, hang, red, red, red stops at the fifth run (4 refines); if the hang were
    skipped without resetting, the second red would count as a repeat and it would stop a run sooner."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_RedHangRedRunner, codegen=_EditingCodegen)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert created and created[0].refine_calls == 4


async def test_a_run_that_hangs_is_not_a_stall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two timeouts read alike whatever the edits did, and a hang is something refine can fix."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_TimedOutRunner, codegen=_EditingCodegen)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert created and created[0].refine_calls == 5


async def test_an_output_clipped_to_the_runner_limit_is_not_a_stall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the tail of a long report survives, so an earlier failure may have been fixed out of sight."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_ClippedRunner, codegen=_EditingCodegen)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert created and created[0].refine_calls == 5


async def test_the_stop_does_not_depend_on_the_evidence_being_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The evidence lines are advice. If building them raised, the loop must still stop."""
    from orchestrator.sdlc import feature_runner as fr

    def _boom(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("no evidence today")

    created = _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, codegen=_EditingCodegen)
    monkeypatch.setattr(fr, "_stall_lines", _boom)
    lines: list[str] = []

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5, log=lines.append)

    assert created and created[0].refine_calls == 2
    assert any("failed identically" in ln for ln in lines), lines


def test_a_long_list_of_failing_tests_says_how_many_more_there_are() -> None:
    from orchestrator.sdlc.feature_runner import _stall_lines

    result = SimpleNamespace(problems=tuple(f"tests/t.py::test_{n}" for n in range(7)))

    lines = _stall_lines(2, result, "FAILED tests/t.py::test_0", set(), {})

    failing = next(ln for ln in lines if ln.startswith("[refine]   failing:"))
    assert "test_0" in failing and "test_2" in failing and "test_3" not in failing
    assert failing.endswith("… (4 more)")


def test_the_proposed_criteria_are_listed_beside_the_stated_ones() -> None:
    from orchestrator.sdlc.feature_runner import _stall_lines

    spec = {"acceptance_criteria": ["exports a csv"], "proposed_criteria": ["also exports xml"]}

    lines = _stall_lines(2, SimpleNamespace(), "FAILED tests/test_x.py::t", {"tests/test_x.py"}, spec)

    assert "[refine]   written by this run: tests/test_x.py" in lines
    assert "[refine]   stated criteria: exports a csv" in lines
    assert "[refine]   proposed criteria: also exports xml" in lines


def test_a_test_the_run_did_not_write_is_not_called_its_own() -> None:
    from orchestrator.sdlc.feature_runner import _stall_lines

    lines = _stall_lines(
        2,
        SimpleNamespace(),
        "FAILED tests/old_test.py::t",
        {"tests/test_x.py", "src/x.py"},
        {"acceptance_criteria": ["c"]},
    )

    assert not any("written by this run" in ln or "criteria" in ln for ln in lines)


class _GreenOnceRunner:
    """Red, red, green, then red again with the very same failure."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        if self.calls == 3:
            return SimpleNamespace(passed=True, returncode=0, output="1 passed")
        return SimpleNamespace(passed=False, returncode=1, output="E   assert 1 == 2")


class _CoveringCodegen(_EditingCodegen):
    async def author_tests(self, **kwargs: Any) -> CodeChange:
        return CodeChange(files=["tests/test_cover.py"], summary="cover")


async def test_a_failure_that_comes_back_after_a_green_run_is_not_a_repeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A green run ends the episode: when the cover stage then adds a test that fails the same way,
    that is a new failure and its count starts again, so it gets its own two repeats."""
    from orchestrator.sdlc import feature_runner as fr

    created = _install_pipeline(monkeypatch, tmp_path, runner=_GreenOnceRunner, codegen=_CoveringCodegen)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    # red, red: two refines, then green; the same failure again counts from scratch: two more refines.
    assert created and created[0].refine_calls == 4


async def test_a_stalled_type_error_still_uses_its_whole_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_EditingCodegen)
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult("x.py:1: error: boom"))

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert created and created[0].refine_calls == 5


# ---- the attempt record (B72, SSPN-155) --------------------------------------------


class _RecordingCodegen(_EditingCodegen):
    """Keeps the keyword arguments the adapter was constructed with."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.kwargs = kwargs


async def test_the_record_dir_reaches_the_adapter_and_every_test_run_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The adapter writes the attempts; the runner writes the test runs: both need the directory."""
    monkeypatch.delenv("SDLC_ATTEMPT_RECORD", raising=False)
    created = _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, codegen=_RecordingCodegen)
    record_dir = tmp_path / "rec"
    lines: list[str] = []

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", max_refine=2, record_dir=record_dir, log=lines.append
        )

    assert isinstance(created[0], _RecordingCodegen)
    assert created[0].kwargs["record_dir"] == record_dir
    kept = sorted(p.name for p in record_dir.glob("run-tests-*.txt"))
    assert kept == ["run-tests-1.txt", "run-tests-2.txt", "run-tests-3.txt"]  # one per [run_tests #n]
    assert "E   assert 1 == 2" in (record_dir / "run-tests-1.txt").read_text(encoding="utf-8")
    assert [ln for ln in lines if ln.startswith("[record]")] == [
        f"[record] attempts and test runs in {record_dir}"
    ]


async def test_without_a_record_dir_nothing_is_kept_and_nothing_is_said(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SDLC_ATTEMPT_RECORD", raising=False)
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, codegen=_RecordingCodegen)
    lines: list[str] = []

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=1, log=lines.append)

    assert not list(tmp_path.glob("**/run-tests-*.txt"))
    assert not [ln for ln in lines if ln.startswith("[record]")]


async def test_the_kill_switch_keeps_no_test_run_and_says_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SDLC_ATTEMPT_RECORD", "0")
    _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner, codegen=_RecordingCodegen)
    record_dir = tmp_path / "rec"
    lines: list[str] = []

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", max_refine=1, record_dir=record_dir, log=lines.append
        )

    assert not record_dir.exists()
    assert not [ln for ln in lines if ln.startswith("[record]")]


# ---- required_behavior: the fourth gate (SSPN-118/119) ---------------------------


async def test_required_behavior_pass_completes_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A green manifest doesn't change the happy path at all."""
    _install_pipeline(
        monkeypatch, tmp_path, runner=_PassingRunner, required_behavior=_PassingRequiredBehavior
    )

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://github.com/x/widget")

    assert result.passed


async def test_required_behavior_failure_drives_refine_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A manifest that fails once and passes after is exactly the roadmap's own
    fail -> refine -> pass demonstration — and proves required_behavior is re-checked
    after the refine, not just run once."""
    created = _install_pipeline(
        monkeypatch,
        tmp_path,
        runner=_PassingRunner,
        codegen=_EditingCodegen,
        required_behavior=_EventuallyPassingRequiredBehavior,
    )

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://github.com/x/widget")

    assert result.passed
    assert created and created[0].refine_calls == 1


async def test_required_behavior_failure_exhausts_its_own_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """required_behavior has its own refine pool — proven by it alone exhausting the
    budget while tests/types stayed green the whole time."""
    created = _install_pipeline(
        monkeypatch,
        tmp_path,
        runner=_PassingRunner,
        codegen=_EditingCodegen,
        required_behavior=_FailingRequiredBehavior,
    )

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://github.com/x/widget", max_refine=2
        )

    assert created and created[0].refine_calls == 2


async def test_required_behavior_environment_blocked_stops_without_spending_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing fixture/command is refine's problem to diagnose, not to edit around —
    the loop must stop immediately rather than burn its refine budget on it."""
    created = _install_pipeline(
        monkeypatch,
        tmp_path,
        runner=_PassingRunner,
        codegen=_EditingCodegen,
        required_behavior=_EnvironmentBlockedRequiredBehavior,
    )

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://github.com/x/widget", max_refine=3
        )

    assert created and created[0].refine_calls == 0


# ---- three-outcome reporting: initial candidate vs needed-refinement (SSPN-121/D28) -----


async def test_worklog_reports_initial_candidate_when_no_refine_was_needed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    jira = _FakeJira()
    _install_pipeline(
        monkeypatch, tmp_path, runner=_PassingRunner, jira=jira, required_behavior=_PassingRequiredBehavior
    )

    await run_feature(
        "file://./spec.md",
        intent_id="intent-a",
        repo="https://github.com/x/widget",
        live=True,
        issue="SSPN-1",
    )

    assert len(jira.worklogs) == 1
    body = jira.worklogs[0][2]
    assert "**Outcome:** initial generated candidate passed with no correction needed" in body
    # required_behavior ran (zero LLM calls, real wall-clock time) and gets its own row,
    # not an omitted one -- SSPN-121/D27.
    assert "| required_behavior |" in body


async def test_worklog_reports_autonomous_completion_after_a_refine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    jira = _FakeJira()
    _install_pipeline(
        monkeypatch,
        tmp_path,
        runner=_PassingRunner,
        codegen=_EditingCodegen,
        jira=jira,
        required_behavior=_EventuallyPassingRequiredBehavior,
    )

    await run_feature(
        "file://./spec.md",
        intent_id="intent-a",
        repo="https://github.com/x/widget",
        live=True,
        issue="SSPN-1",
    )

    body = jira.worklogs[0][2]
    assert "**Outcome:** first completed autonomous workflow after 1 refine(s)" in body
    assert "required_behavior" in body


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
    benchmark_tmp = tmp_path / ".benchmark-tmp" / "pytest-of-user" / "case"
    benchmark_tmp.mkdir(parents=True)
    (benchmark_tmp / "Ext.kt").write_text("fun String.slugify() = this\n")
    files = await _changed_files(tmp_path)
    assert files == ["src/App.java"]


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


def test_only_environment_codes_are_filtered() -> None:
    """The worktree venv carries runtime deps only, so a changed import line can fail here
    and resolve fine in CI. Nothing else belongs in the filter: it used to hold
    `unused-ignore` and `misc` as "hygiene", and a live run shipped a PR that CI rejected
    with `Unused "type: ignore"` — the pipeline had seen it, discarded it, and printed
    "clean on the changed lines"."""
    from orchestrator.sdlc.feature_runner import _is_typing_hygiene

    # The sandbox, not the change.
    assert _is_typing_hygiene("t.py:1: error: Cannot find implementation for x  [import-not-found]")
    assert _is_typing_hygiene("t.py:1: error: Library stubs not installed  [import-untyped]")

    # Everything else is the target repo's policy, and CI enforces it.
    assert not _is_typing_hygiene('t.py:1: error: Unused "type: ignore" comment  [unused-ignore]')
    assert not _is_typing_hygiene("t.py:1: error: Untyped decorator makes f untyped  [misc]")
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


async def test_an_unverified_criterion_does_not_ship(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The live PR: one criterion of seven came back uncertain — "all existing output
    fields remain unchanged in format" — on a change that had rewritten `inputs` from
    `["name"]` to `["name (type)"]`. `comment` is not `request_changes`, so the mapping
    sent it straight to a real PR. The doubt was the finding."""

    class _UncertainJudge(_Judge):
        async def review(self, *, path: str, issue_key: str, spec: Any = None) -> Any:
            self.calls += 1
            return SimpleNamespace(
                verdict="comment",
                blockers=["acceptance criterion unverified: output format unchanged"],
                summary="judged",
                uncertain=["output format unchanged"],
                unreviewed=False,
            )

    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _UncertainJudge())

    with pytest.raises(FeatureRunError, match="does not satisfy the ticket"):
        await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    # It went to revise like any other blocker, rather than through to a PR.
    assert created[0].revise_calls == 1


async def test_a_fully_met_verdict_still_ships(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The gate only tightened for doubt. An approving verdict is untouched."""
    judge = _Judge("approve")
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)

    result = await run_feature("file://./spec.md", intent_id="intent-a", repo="https://x/widget")

    assert result.passed and judge.calls == 1


async def test_language_php_requires_toolchain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testenv.php_toolchain_available", lambda: False)
    with pytest.raises(FeatureRunError, match="PHP codegen needs") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", language="php")
    assert exc.value.code == 2
    assert not list(tmp_path.rglob("pyproject.toml"))


class _DispatchReadyError(Exception):
    """Stop after real layout/scaffold/availability dispatch, before dependency installs."""


async def _dispatch_ready(self: Any, worktree: Path | str) -> None:
    raise _DispatchReadyError


async def test_csharp_dispatch_scaffolds_the_installed_target_framework(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scaffold must target the installed runtime, not the template's net8.0 default."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testenv.detect_dotnet_tfm", lambda: "net91.0")
    monkeypatch.setattr("orchestrator.sdlc.testenv.dotnet_toolchain_available", lambda: True)
    monkeypatch.setattr("orchestrator.sdlc.testenv.DotnetToolEnvironment.ensure", _dispatch_ready)

    with pytest.raises(_DispatchReadyError):
        await run_feature("file://./spec.md", language="csharp", package_name="Widget")

    projects = list(tmp_path.rglob("*.csproj"))
    assert len(projects) == 2
    for project in projects:
        assert "<TargetFramework>net91.0</TargetFramework>" in project.read_text()


@pytest.mark.parametrize("language", ["c", "cpp"])
@pytest.mark.parametrize("available", [False, True])
async def test_native_dispatch_probes_the_selected_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str, available: bool
) -> None:
    """A C compiler cannot satisfy C++'s guard, nor can a C++ compiler satisfy C's."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.c_toolchain_available",
        lambda: available if language == "c" else not available,
    )
    monkeypatch.setattr(
        "orchestrator.sdlc.testenv.cpp_toolchain_available",
        lambda: available if language == "cpp" else not available,
    )
    monkeypatch.setattr("orchestrator.sdlc.testenv.CToolEnvironment.ensure", _dispatch_ready)

    if available:
        with pytest.raises(_DispatchReadyError):
            await run_feature("file://./spec.md", language=language)
    else:
        with pytest.raises(FeatureRunError) as exc:
            await run_feature("file://./spec.md", language=language)
        label = "C++" if language == "cpp" else "C"
        assert exc.value.code == 2
        assert f"{label} codegen needs CMake + a {label} compiler" in str(exc.value)


@pytest.mark.parametrize("language", ["c", "cpp"])
@pytest.mark.parametrize("available", [False, True])
async def test_native_dispatch_preserves_meson_brownfield_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str, available: bool
) -> None:
    """The existing manifest controls the guard even when the other toolchain is available."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    (tmp_path / "meson.build").write_text(f"project('widget', '{language}')\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / f"widget.{language}").write_text("int value;\n")
    monkeypatch.setattr("orchestrator.sdlc.testenv.meson_toolchain_available", lambda: available)
    monkeypatch.setattr("orchestrator.sdlc.testenv.c_toolchain_available", lambda: not available)
    monkeypatch.setattr("orchestrator.sdlc.testenv.cpp_toolchain_available", lambda: not available)
    monkeypatch.setattr("orchestrator.sdlc.testenv.CToolEnvironment.ensure", _dispatch_ready)

    if available:
        with pytest.raises(_DispatchReadyError):
            await run_feature("file://./spec.md", language=language)
    else:
        with pytest.raises(FeatureRunError) as exc:
            await run_feature("file://./spec.md", language=language)
        assert exc.value.code == 2
        assert "needs meson + ninja + a compiler" in str(exc.value)
    assert not (tmp_path / "CMakeLists.txt").exists()


async def test_unknown_direct_language_retains_python_fallback_without_pytest_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testrunner.pytest_available", lambda python: _aresult(False))
    await run_feature("file://./spec.md", intent_id="intent-a", language="unknown")
    assert created[0].layout is not None
    assert created[0].layout.language == "python"


async def test_perl_missing_toolchain_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testenv.perl_toolchain_available", lambda: False)
    with pytest.raises(FeatureRunError, match="needs `perl` and `prove`") as exc:
        await run_feature("file://./spec.md", language="perl")
    assert exc.value.code == 2


# ---- what a test can exercise (CB-686 / CB-760: the scaffold files failed every greenfield run) --


def test_scaffold_and_config_files_are_never_probed(tmp_path: Path) -> None:
    from orchestrator.sdlc.feature_runner import _testable_production

    (tmp_path / "src" / "cb_686").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / ".gitignore").write_text(".venv\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "src" / "cb_686" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "cb_686" / "account.py").write_text(
        "def delete(reason: str) -> str:\n    return reason\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_account.py").write_text(
        "def test_it() -> None:\n    pass\n", encoding="utf-8"
    )
    files = [
        ".gitignore",
        "pyproject.toml",
        "src/cb_686/__init__.py",
        "src/cb_686/account.py",
        "tests/test_account.py",
    ]

    probe, excluded = _testable_production(tmp_path, files)

    assert probe == ["src/cb_686/account.py"]
    assert excluded == [".gitignore (not source)", "pyproject.toml (not source)", "__init__.py (empty)"]


def test_a_non_empty_init_is_still_probed(tmp_path: Path) -> None:
    from orchestrator.sdlc.feature_runner import _testable_production

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("from .core import run\n", encoding="utf-8")
    probe, excluded = _testable_production(tmp_path, ["pkg/__init__.py", "tests/test_core.py"])
    assert probe == ["pkg/__init__.py"] and excluded == []


# ---- a change that is only comments is not one a test can fail on (SSPN-193 / B91) ----------


def _repo_with(root: Path, files: dict[str, str]) -> Path:
    """A git repo whose HEAD holds `files`, with nothing changed yet."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
    return root


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param(
            "def f(x):\n    return x\n", "# note\ndef f(x):\n    # why\n    return x\n", id="comments"
        ),
        pytest.param(
            "def f(x):\n    return x\n",
            'def f(x):\n    """Return x."""\n    return x\n',
            id="docstring-added",
        ),
        pytest.param(
            'def f(x):\n    """Old."""\n    return x\n',
            'def f(x):\n    """New."""\n    return x\n',
            id="docstring-reworded",
        ),
        pytest.param('"""Module."""\nX = 1\n', '"""Module, reworded."""\nX = 1\n', id="module-docstring"),
        pytest.param("def f():\n    pass\n", 'def f():\n    """Do nothing."""\n', id="docstring-only-body"),
        pytest.param("X = [1,2]\n", "X = [1, 2]\n", id="formatting-only"),
    ],
)
def test_a_python_file_changed_only_in_comments_or_docstrings_is_not_probed(
    tmp_path: Path, before: str, after: str
) -> None:
    """django-14404: the model added two comment lines to `request.py`. Reverting them cannot
    redden a suite, so the probe reported "nothing exercises the change" and the run ended
    FAILED while the real fix, in another file, was tested."""
    from orchestrator.sdlc.feature_runner import _testable_production

    root = _repo_with(tmp_path, {"src/mod.py": before})
    (root / "src/mod.py").write_text(after, encoding="utf-8")

    probe, excluded = _testable_production(root, ["src/mod.py", "tests/test_mod.py"])

    assert probe == []
    assert excluded == ["mod.py (comment-only)"]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param("x = 1\n", "x = 2\n", id="value"),
        pytest.param('MSG = "a"\n', 'MSG = "b"\n', id="string-that-is-not-a-docstring"),
        pytest.param(
            "def f():\n    return 1\n",
            "def f():\n    return 1\n\n\ndef g():\n    return 2\n",
            id="new-function",
        ),
        pytest.param("def f():\n    pass\n", "def f():\n    return 1\n", id="body"),
        pytest.param(
            "def f(x):\n    return x\n", "def f(x):\n    # kept\n    return x + 1\n", id="comment-and-code"
        ),
    ],
)
def test_a_python_file_with_a_real_change_is_still_probed(tmp_path: Path, before: str, after: str) -> None:
    from orchestrator.sdlc.feature_runner import _testable_production

    root = _repo_with(tmp_path, {"src/mod.py": before})
    (root / "src/mod.py").write_text(after, encoding="utf-8")

    probe, excluded = _testable_production(root, ["src/mod.py", "tests/test_mod.py"])

    assert probe == ["src/mod.py"] and excluded == []


def test_a_file_with_no_committed_version_is_still_probed(tmp_path: Path) -> None:
    """A new file has no HEAD to compare against, so nothing says it is comments only."""
    from orchestrator.sdlc.feature_runner import _testable_production

    root = _repo_with(tmp_path, {"README.md": "seed\n"})
    (root / "src").mkdir()
    (root / "src/new.py").write_text("# nothing but a comment\n", encoding="utf-8")

    probe, excluded = _testable_production(root, ["src/new.py"])

    assert probe == ["src/new.py"] and excluded == []


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param("x = 1\n", "def (:\n", id="unparsable-now"),
        pytest.param("print 'hi'\n", "print 'hi'  # c\n", id="unparsable-at-head"),
    ],
)
def test_a_file_that_does_not_parse_is_still_probed(tmp_path: Path, before: str, after: str) -> None:
    """When the comparison cannot be made the file is probed as before — never waved through."""
    from orchestrator.sdlc.feature_runner import _testable_production

    root = _repo_with(tmp_path, {"src/mod.py": before})
    (root / "src/mod.py").write_text(after, encoding="utf-8")

    probe, excluded = _testable_production(root, ["src/mod.py"])

    assert probe == ["src/mod.py"] and excluded == []


def test_another_language_is_not_waved_through_as_comment_only(tmp_path: Path) -> None:
    """Only Python can be compared exactly. A Kotlin file with a comment added is probed."""
    from orchestrator.sdlc.feature_runner import _testable_production

    root = _repo_with(tmp_path, {"src/Account.kt": "class Account\n"})
    (root / "src/Account.kt").write_text("// note\nclass Account\n", encoding="utf-8")

    probe, excluded = _testable_production(root, ["src/Account.kt"])

    assert probe == ["src/Account.kt"] and excluded == []


class _RecordingRunner:
    """Passes only while `src/sites.py` still holds the fix; records every run."""

    runs = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        _RecordingRunner.runs += 1
        fixed = "return request.path\n" in (Path(path) / "src/sites.py").read_text(encoding="utf-8")
        return SimpleNamespace(passed=fixed, returncode=0 if fixed else 1, output="")


async def test_the_probe_ignores_a_comment_only_file_beside_a_tested_fix(tmp_path: Path) -> None:
    """The django-14404 shape end to end: the fix is tested, a second file gained a comment.
    The probe must report every file exercised, and must not spend a test run on the comment."""
    from orchestrator.sdlc.feature_runner import _files_no_test_exercises

    root = _repo_with(
        tmp_path,
        {
            "src/sites.py": "def view(request):\n    return request.path_info\n",
            "src/request.py": "def full_path(self):\n    return self.path\n",
            "tests/test_sites.py": "def test_it():\n    pass\n",
        },
    )
    (root / "src/sites.py").write_text("def view(request):\n    return request.path\n", encoding="utf-8")
    (root / "src/request.py").write_text(
        "def full_path(self):\n    # self.path keeps the script-name prefix.\n    return self.path\n",
        encoding="utf-8",
    )
    messages: list[str] = []
    _RecordingRunner.runs = 0

    gaps = await _files_no_test_exercises(
        root, ["src/sites.py", "src/request.py", "tests/test_sites.py"], _RecordingRunner(), messages.append
    )

    assert gaps == []
    assert _RecordingRunner.runs == 1  # sites.py only; the comment-only file is never reverted
    assert "[coverage] excluded (not testable): request.py (comment-only)" in messages
    assert "[coverage] every changed file is exercised by a test" in messages


# ---- CB-760: the cover stage wrote a test no production edit could satisfy -------------------


class _CoverCodegen(_StubCodegen):
    """`author_tests` for a coverage gap writes CB-760's `test_main_stdout` — a test asserting
    `__main__`-guarded output under a monkeypatch — and `refine` only ever edits the module."""

    async def implement(self, **kwargs: Any) -> CodeChange:
        root = Path(kwargs["path"])
        (root / "src").mkdir(exist_ok=True)
        (root / "src" / "x.py").write_text("def main() -> None:\n    print('ok')\n", encoding="utf-8")
        return CodeChange(files=["src/x.py"], summary="impl")

    async def author_tests(self, **kwargs: Any) -> CodeChange:
        root = Path(kwargs["path"])
        (root / "tests").mkdir(exist_ok=True)
        if kwargs.get("gaps"):
            self.gaps_seen.append(list(kwargs["gaps"]))
            (root / "tests" / "test_main_stdout.py").write_text(
                "import runpy\n\n\ndef test_main_stdout(monkeypatch, capsys):\n"
                "    runpy.run_module('src.x', run_name='__main__')\n"
                "    assert capsys.readouterr().out == 'ok'\n",
                encoding="utf-8",
            )
            return CodeChange(files=["tests/test_main_stdout.py"], summary="cover main()")
        (root / "tests" / "test_x.py").write_text("def test_x() -> None:\n    pass\n", encoding="utf-8")
        return CodeChange(files=["tests/test_x.py"], summary="tests")

    async def refine(self, **kwargs: Any) -> CodeChange:
        self.refine_calls += 1
        return CodeChange(files=["src/x.py"], summary="fix main() entrypoint logic")


class _CoverAwareRunner:
    """Red exactly while the cover-authored test exists; green otherwise."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        if (Path(path) / "tests" / "test_main_stdout.py").exists():
            return SimpleNamespace(
                passed=False, returncode=1, output="FAILED tests/test_main_stdout.py::test_main_stdout"
            )
        return SimpleNamespace(passed=True, returncode=0, output="1 passed")


def _one_gap_then_clean() -> Any:
    probes: list[int] = []

    async def _probe(path: Path, files: list[str], runner: Any, emit: Any) -> list[str]:
        probes.append(1)
        return ["src/x.py"] if len(probes) == 1 else []

    return _probe


async def test_a_cover_authored_test_no_refine_can_satisfy_is_withdrawn_not_chased(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CB-760 ended FAILED after nine test runs, five of them refines editing the module to
    satisfy a test the run itself had written. The run's own guess at coverage is not the
    ticket's contract: once the budget is spent on it, the test is withdrawn and said so."""
    from orchestrator.sdlc import feature_runner as fr

    created = _install_pipeline(monkeypatch, tmp_path, runner=_CoverAwareRunner, codegen=_CoverCodegen)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))
    log: list[str] = []

    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3, log=log.append)

    assert result.passed
    assert result.coverage_withdrawn == ["tests/test_main_stdout.py"]
    assert not (tmp_path / "tests" / "test_main_stdout.py").exists()
    assert (tmp_path / "tests" / "test_x.py").exists()  # the run's own author_tests test stands
    # Stopped at the second identical failure (B80), which is earlier than the budget but
    # the same exit: the withdrawal still happens, one refine sooner.
    assert created[0].refine_calls == 2
    assert any(
        "[cover] withdrawn: test_main_stdout.py" in line and "coverage not proven" in line for line in log
    ), log


class _CoverAwareChangingRunner:
    """Red exactly while the cover-authored test exists, and the failure moves every run."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        if (Path(path) / "tests" / "test_main_stdout.py").exists():
            return SimpleNamespace(
                passed=False,
                returncode=1,
                output=f"FAILED tests/test_main_stdout.py::test_main_stdout\nE   assert {self.calls} == 0",
            )
        return SimpleNamespace(passed=True, returncode=0, output="1 passed")


async def test_a_cover_test_that_keeps_failing_differently_is_withdrawn_when_the_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CB-760 behaviour on its original path: the failure changes every run, so nothing stalls, the
    allowance is spent on it (3 refines), and only then is the test withdrawn."""
    from orchestrator.sdlc import feature_runner as fr

    created = _install_pipeline(
        monkeypatch, tmp_path, runner=_CoverAwareChangingRunner, codegen=_CoverCodegen
    )
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))
    log: list[str] = []

    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3, log=log.append)

    assert result.passed
    assert result.coverage_withdrawn == ["tests/test_main_stdout.py"]
    assert created[0].refine_calls == 3  # the budget was spent before anything was withdrawn
    assert not any("failed identically" in line for line in log), log


async def test_a_stall_on_a_test_this_run_wrote_shows_the_evidence_and_claims_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B80 / D5: the stop names the failing test, says this run wrote it, and lists the spec's
    stated criteria so a person can judge. It does not say the test is or is not covered."""
    from orchestrator.sdlc import feature_runner as fr

    _install_pipeline(monkeypatch, tmp_path, runner=_CoverAwareRunner, codegen=_CoverCodegen)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))
    log: list[str] = []

    await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5, log=log.append)

    refine_lines = [ln for ln in log if ln.startswith("[refine]")]
    assert any("failed identically" in ln for ln in refine_lines), log
    assert any(ln.startswith("[refine]   failing:") and "test_main_stdout" in ln for ln in refine_lines), log
    assert "[refine]   written by this run: tests/test_main_stdout.py" in refine_lines, log
    assert "[refine]   stated criteria: c" in refine_lines, log
    assert not any("proposed criteria" in ln for ln in refine_lines), log  # this spec proposed none
    assert not any("not covered" in ln or "unrequested" in ln or "covers" in ln for ln in refine_lines), log


async def test_every_announced_test_run_is_kept_including_the_one_after_a_withdrawal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The re-run after a cover test is withdrawn is a `[run_tests #n]` like any other (B72)."""
    from orchestrator.sdlc import feature_runner as fr

    monkeypatch.delenv("SDLC_ATTEMPT_RECORD", raising=False)
    _install_pipeline(monkeypatch, tmp_path, runner=_CoverAwareRunner, codegen=_CoverCodegen)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))
    log: list[str] = []
    record_dir = tmp_path / "rec"

    result = await run_feature(
        "file://./spec.md", intent_id="intent-a", max_refine=3, record_dir=record_dir, log=log.append
    )

    assert result.coverage_withdrawn  # the withdrawal path was taken
    announced = {int(n) for line in log for n in re.findall(r"\[run_tests #(\d+)\]", line)}
    assert len(announced) >= 2
    assert {p.name for p in record_dir.glob("run-tests-*.txt")} == {f"run-tests-{n}.txt" for n in announced}


async def test_a_red_test_the_cover_stage_did_not_write_is_still_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Withdrawal is narrow: a failure the output does not attribute to a cover-authored file
    ends the run as it always did, and nothing is removed."""
    from orchestrator.sdlc import feature_runner as fr

    class _RedElsewhere(_CoverAwareRunner):
        async def run(self, *, path: str) -> SimpleNamespace:
            if (Path(path) / "tests" / "test_main_stdout.py").exists():
                return SimpleNamespace(passed=False, returncode=1, output="FAILED tests/test_x.py::test_x")
            return SimpleNamespace(passed=True, returncode=0, output="1 passed")

    _install_pipeline(monkeypatch, tmp_path, runner=_RedElsewhere, codegen=_CoverCodegen)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)
    assert (tmp_path / "tests" / "test_main_stdout.py").exists()  # nothing was withdrawn


class _CoverIntoAuthoredFile(_CoverCodegen):
    """The cover stage answers the gap by appending to the file `author_tests` already wrote —
    the natural place for "a test that reaches src/x.py" to land."""

    async def author_tests(self, **kwargs: Any) -> CodeChange:
        root = Path(kwargs["path"])
        (root / "tests").mkdir(exist_ok=True)
        target = root / "tests" / "test_x.py"
        if kwargs.get("gaps"):
            self.gaps_seen.append(list(kwargs["gaps"]))
            target.write_text(
                target.read_text(encoding="utf-8") + "\n\ndef test_main_stdout() -> None:\n"
                "    import runpy\n    runpy.run_module('src.x', run_name='__main__')\n",
                encoding="utf-8",
            )
            return CodeChange(files=["tests/test_x.py"], summary="cover main()")
        target.write_text("def test_x() -> None:\n    pass\n", encoding="utf-8")
        return CodeChange(files=["tests/test_x.py"], summary="tests")


class _RedWhileCoverMarkerPresent(_CoverAwareRunner):
    async def run(self, *, path: str) -> SimpleNamespace:
        target = Path(path) / "tests" / "test_x.py"
        if target.exists() and "runpy" in target.read_text(encoding="utf-8"):
            return SimpleNamespace(
                passed=False, returncode=1, output="FAILED tests/test_x.py::test_main_stdout"
            )
        return SimpleNamespace(passed=True, returncode=0, output="1 passed")


async def test_withdrawal_never_deletes_a_test_an_earlier_stage_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing is committed until the run ends, so a generated test is untracked and cannot be
    restored. Withdrawing the file `author_tests` created would delete the ticket's own tests
    and open a PR with none of them — so only a file the cover stage *created* may be withdrawn."""
    from orchestrator.sdlc import feature_runner as fr

    _install_pipeline(
        monkeypatch, tmp_path, runner=_RedWhileCoverMarkerPresent, codegen=_CoverIntoAuthoredFile
    )
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)

    body = (tmp_path / "tests" / "test_x.py").read_text(encoding="utf-8")
    assert "def test_x()" in body  # the run's own test survived
    assert "runpy" in body  # and nothing was silently rewritten


async def test_a_failure_in_another_file_of_the_same_name_withdraws_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`FAILED tests/integration/test_models.py::…` contains the basename of a cover-written
    `tests/unit/test_models.py`. Matching the name alone withdrew a test that was green and
    claimed, in the log, that its coverage had failed."""
    from orchestrator.sdlc import feature_runner as fr

    class _CoverElsewhere(_CoverCodegen):
        async def author_tests(self, **kwargs: Any) -> CodeChange:
            root = Path(kwargs["path"])
            if kwargs.get("gaps"):
                (root / "tests" / "unit").mkdir(parents=True, exist_ok=True)
                (root / "tests" / "unit" / "test_models.py").write_text(
                    "def test_models() -> None:\n    pass\n", encoding="utf-8"
                )
                return CodeChange(files=["tests/unit/test_models.py"], summary="cover models")
            (root / "tests" / "integration").mkdir(parents=True, exist_ok=True)
            (root / "tests" / "integration" / "test_models.py").write_text(
                "def test_models() -> None:\n    pass\n", encoding="utf-8"
            )
            return CodeChange(files=["tests/integration/test_models.py"], summary="tests")

    class _RedInIntegration(_CoverAwareRunner):
        async def run(self, *, path: str) -> SimpleNamespace:
            if (Path(path) / "tests" / "unit" / "test_models.py").exists():
                return SimpleNamespace(
                    passed=False,
                    returncode=1,
                    output="FAILED tests/integration/test_models.py::test_models",
                )
            return SimpleNamespace(passed=True, returncode=0, output="1 passed")

    _install_pipeline(monkeypatch, tmp_path, runner=_RedInIntegration, codegen=_CoverElsewhere)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    log: list[str] = []

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3, log=log.append)

    assert (tmp_path / "tests" / "unit" / "test_models.py").exists()
    assert not any("withdrawn" in line for line in log)


def test_a_kotlin_source_file_is_probed_not_dismissed_as_unsourceable(tmp_path: Path) -> None:
    """Kotlin is a full codegen toolchain. Missing from the testable set, every probe answered
    "a test could not reach this" for a whole Kotlin run — and said `(not source)`, which is false."""
    from orchestrator.sdlc.feature_runner import _testable_production

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Account.kt").write_text("class Account { fun id() = 1 }\n", encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text('plugins { kotlin("jvm") }\n', encoding="utf-8")

    probe, excluded = _testable_production(tmp_path, ["src/Account.kt", "build.gradle.kts"])

    assert probe == ["src/Account.kt"]
    # The build script is not source a test can exercise: stashing it stops Gradle resolving,
    # and the red suite that follows would be recorded as proof that a test reaches it.
    assert excluded == ["build.gradle.kts (not source)"]


def test_the_pr_body_says_when_the_run_withdrew_its_own_coverage_test() -> None:
    """The log and the journey both record a withdrawal; the reviewer reads neither. A green
    PR whose coverage was withdrawn has to say so where the review happens."""
    from orchestrator.sdlc.feature_runner import _pr_body

    spec = {"summary": "Add a thing.", "acceptance_criteria": ["It works."], "intent_id": "CB-760"}
    assert "Coverage withdrawn" not in _pr_body(spec, [])
    body = _pr_body(spec, ["tests/test_main_stdout.py"])
    assert "**Coverage withdrawn:** `tests/test_main_stdout.py`" in body
    assert "That coverage is not proven." in body


def test_a_windows_runner_path_is_attributed_to_the_file_that_failed() -> None:
    """pytest on Windows prints `FAILED tests\\unit\\test_models.py`. Matching only the posix
    spelling meant D3 never fired there — the run reported FAILED instead of withdrawing."""
    from orchestrator.sdlc.feature_runner import _named_in_failures

    assert _named_in_failures("tests/unit/test_models.py", r"FAILED tests\unit\test_models.py::test_a")
    assert _named_in_failures("tests/unit/test_models.py", "FAILED tests/unit/test_models.py::test_a")
    assert not _named_in_failures(
        "tests/unit/test_models.py", "FAILED tests/integration/test_models.py::test_a"
    )
    # A passing file named in a warnings summary is not a failure.
    assert not _named_in_failures("tests/unit/test_models.py", "warnings summary: tests/unit/test_models.py")


async def test_the_runner_tells_the_layout_which_files_the_ticket_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The plan named five files under `WebApp/`; the layout picked `ApiClient` because it sorts
    first, and codegen could not compile against a project that holds none of the ticket's types.
    The runner now hands the design's files to the resolver that has to choose."""
    import orchestrator.sdlc.layout as layout_mod
    from orchestrator.sdlc import feature_runner as fr

    seen: dict[str, Any] = {}
    real = layout_mod.resolve_layout

    def _capture(root: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return real(root, **kwargs)

    monkeypatch.setattr(layout_mod, "resolve_layout", _capture)
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)

    class _NamesAFile(_Spec):
        def model_dump(self) -> dict[str, Any]:
            spec = super().model_dump()
            spec["description"] = "Hide lost coils — see WebApp/AuctionCoilsUi.razor."
            return spec

    _patch_service(monkeypatch, [_NamesAFile("intent-a")])
    monkeypatch.setattr(fr, "_files_no_test_exercises", lambda *a, **k: _aresult([]))
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))
    (tmp_path / "WebApp").mkdir()
    (tmp_path / "WebApp" / "AuctionCoilsUi.razor").write_text("<div/>\n", encoding="utf-8")

    # A `.razor` page with no `.csproj` anywhere is C# source `--layout auto` cannot place, so
    # the run stops rather than scaffold a solution beside it — after resolving the layout.
    with pytest.raises(FeatureRunError, match="will not scaffold"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=1)

    # The ticket names `AuctionCoilsUi.razor`; the resolver is told, not left to sort names.
    assert seen.get("prefer_paths") == ["WebApp/AuctionCoilsUi.razor"]


# ---- B13: a supervisor can review before publishing ---------------------------------------------


class _RecordingForge:
    opened: list[dict[str, Any]] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def open_pr(self, **kwargs: Any) -> Any:
        _RecordingForge.opened.append(kwargs)
        return SimpleNamespace(url="https://github.com/x/y/pull/8")


async def test_publish_false_builds_and_hands_back_the_publishing_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """autorun reviews the change before anyone is asked to look at it, so the build must not
    open the PR itself — it hands back the step that does, unchanged."""
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, jira=jira)
    _RecordingForge.opened = []
    monkeypatch.setattr("orchestrator.sdlc.forge.GhPRAdapter", _RecordingForge)

    result = await run_feature(
        "file://./spec.md",
        intent_id="intent-a",
        repo="https://x/widget",
        live=True,
        issue="SSPN-1",
        publish=False,
    )

    assert _RecordingForge.opened == [] and result.pr_url is None
    assert jira.transitions == ["In Progress"]
    assert result.publish is not None
    url = await result.publish(draft=False, note="")
    assert url == "https://github.com/x/y/pull/8"
    assert _RecordingForge.opened[0]["draft"] is False
    assert jira.transitions == ["In Progress", "In Review"]


async def test_a_draft_says_why_and_leaves_the_ticket_in_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    jira = _FakeJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, jira=jira)
    _RecordingForge.opened = []
    monkeypatch.setattr("orchestrator.sdlc.forge.GhPRAdapter", _RecordingForge)

    result = await run_feature(
        "file://./spec.md",
        intent_id="intent-a",
        repo="https://x/widget",
        live=True,
        issue="SSPN-1",
        publish=False,
    )
    assert result.publish is not None
    await result.publish(draft=True, note="Unresolved: x.py:2 bare except")

    [opened] = _RecordingForge.opened
    assert opened["draft"] is True
    assert "Unresolved: x.py:2 bare except" in opened["body"]
    assert jira.transitions == ["In Progress"]  # a draft is not ready for review


async def test_a_safe_run_has_nothing_to_publish(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)

    result = await run_feature("file://./spec.md", intent_id="intent-a", publish=False)

    assert result.publish is None


class _CommentingJira(_FakeJira):
    def __init__(self) -> None:
        super().__init__()
        self.comments: list[str] = []

    async def comment_issue(self, issue_key: str, body: str) -> None:
        self.comments.append(body)


async def test_the_default_path_publishes_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`publish=True` (every caller but autorun) must be the old live block, unchanged: a ready
    PR whose body is the spec's own, the same comment text, one move to In Review."""
    from orchestrator.sdlc import feature_runner

    bodies: list[str] = []
    real_body = feature_runner._pr_body

    def _body(
        spec: dict[str, Any], withdrawn: list[str], withdrawn_production: list[str] | None = None
    ) -> str:
        bodies.append(real_body(spec, withdrawn, withdrawn_production))
        return bodies[-1]

    jira = _CommentingJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, jira=jira)
    # This test is about the publishing block itself. The proof bundle appends to the body by
    # design, so it is switched off here; its own tests cover the body with it on.
    monkeypatch.setenv("SDLC_PROOF_BUNDLE", "0")
    _RecordingForge.opened = []
    monkeypatch.setattr("orchestrator.sdlc.forge.GhPRAdapter", _RecordingForge)
    monkeypatch.setattr("orchestrator.sdlc.feature_runner._pr_body", _body)

    await run_feature(
        "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
    )

    [opened] = _RecordingForge.opened
    assert opened["draft"] is False
    assert opened["title"].startswith("SSPN-1: ")
    assert opened["body"] == bodies[-1]  # exactly the spec's body — no note appended
    assert jira.comments == ["PR opened for this story: https://github.com/x/y/pull/8"]
    assert jira.transitions == ["In Progress", "In Review"]


async def test_publish_false_commits_the_build_before_handing_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The review diffs against the build commit, so the build must be committed first."""
    commits: list[str] = []

    async def _commit(path: Path, message: str) -> None:
        commits.append(message)

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.forge.GhPRAdapter", _RecordingForge)
    monkeypatch.setattr("orchestrator.sdlc.feature_runner._local_commit", _commit)

    await run_feature(
        "file://./spec.md",
        intent_id="intent-a",
        repo="https://x/widget",
        live=True,
        issue="SSPN-1",
        publish=False,
    )

    assert len(commits) == 1 and commits[0].startswith("SSPN-1: ")


# ---- CB-764: failures that predate the change are not the change's ------------------------------


class _PreexistingFailureRunner:
    """A suite with one old file that never imports — before the change and after it."""

    names_problems = True
    calls = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        type(self).calls += 1
        return SimpleNamespace(
            passed=False,
            returncode=1,
            output="E   No module named 'boto3'\nERROR tests/test_api.py\n4 passed, 1 error",
            problems=("tests/test_api.py",),
        )


async def test_a_failure_the_baseline_already_had_is_not_refined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    _PreexistingFailureRunner.calls = 0
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PreexistingFailureRunner)
    monkeypatch.setattr(fr, "_files_no_test_exercises", lambda *a, **k: _aresult([]))
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))

    await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert created and created[0].refine_calls == 0
    assert _PreexistingFailureRunner.calls >= 2  # the baseline, then the judged run


async def test_without_a_baseline_a_missing_dependency_stops_instead_of_refining(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SDLC_TEST_BASELINE", "0")
    created = _install_pipeline(monkeypatch, tmp_path, runner=_PreexistingFailureRunner)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert created and created[0].refine_calls == 0


# ---- B73: a C# run stops before it spends anything when the repository's build is red ----------


class _ScriptedDotnetRunner:
    """A .NET-shaped runner: the first call is the baseline, the rest are the run's own."""

    names_problems = True
    script: list[SimpleNamespace] = []
    calls = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        scripted = type(self).script
        result = scripted[min(type(self).calls, len(scripted) - 1)]
        type(self).calls += 1
        return result


def _outcome(passed: bool, failure: str = "", output: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        passed=passed, returncode=0 if passed else 1, output=output, problems=(), failure=failure
    )


class _CountingCodegen(_StubCodegen):
    implemented = 0

    async def implement(self, **kwargs: Any) -> CodeChange:
        type(self).implemented += 1
        return await super().implement(**kwargs)


async def test_a_red_build_before_any_change_blocks_the_run_and_hands_the_ticket_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    _CountingCodegen.implemented = 0
    _ScriptedDotnetRunner.calls = 0
    _ScriptedDotnetRunner.script = [
        _outcome(
            False,
            "build",
            "COMPILER ERRORS:\n  WebApp/A.cs(2,7): error CS1003: Syntax error\n\n"
            "--- end of the build output ---\n",
        )
    ]
    jira = _FakeJira()
    _install_pipeline(
        monkeypatch, tmp_path, runner=_ScriptedDotnetRunner, codegen=_CountingCodegen, jira=jira
    )

    with pytest.raises(FeatureRunError, match="VERDICT: BLOCKED") as caught:
        await run_feature(
            "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
        )

    assert caught.value.code == 2
    assert _CountingCodegen.implemented == 0  # nothing was generated, nothing spent
    assert jira.transitions == ["In Progress", "To Do"]
    assert _ScriptedDotnetRunner.calls == 1  # the baseline, and nothing after it


async def test_switching_the_baseline_off_restores_the_old_behaviour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SDLC_TEST_BASELINE", "0")
    _ScriptedDotnetRunner.calls = 0
    _ScriptedDotnetRunner.script = [_outcome(False, "build", "error CS1003")]
    created = _install_pipeline(monkeypatch, tmp_path, runner=_ScriptedDotnetRunner)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=1)

    assert created  # it ran: implement, test, refine — as before


async def test_a_baseline_that_timed_out_does_not_block_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    _ScriptedDotnetRunner.calls = 0
    _ScriptedDotnetRunner.script = [_outcome(False, "timeout", "dotnet test run timed out"), _outcome(True)]
    created = _install_pipeline(monkeypatch, tmp_path, runner=_ScriptedDotnetRunner)
    monkeypatch.setattr(fr, "_files_no_test_exercises", lambda *a, **k: _aresult([]))
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))

    await run_feature("file://./spec.md", intent_id="intent-a", max_refine=1)

    assert created  # judged as before, not stopped


@pytest.mark.parametrize("failure", ["feed", "sdk", "timeout"])
async def test_an_environment_failure_mid_run_ends_it_without_asking_refine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    _ScriptedDotnetRunner.calls = 0
    _ScriptedDotnetRunner.script = [_outcome(True), _outcome(False, failure, "NU1301 / timed out")]
    created = _install_pipeline(monkeypatch, tmp_path, runner=_ScriptedDotnetRunner)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert created and created[0].refine_calls == 0


@pytest.mark.parametrize("failure", ["build", "restore"])
async def test_a_failure_the_change_may_have_caused_still_goes_to_refine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """A PackageReference the model added can fail to resolve; refine can edit the project file."""
    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    _ScriptedDotnetRunner.calls = 0
    _ScriptedDotnetRunner.script = [_outcome(True), _outcome(False, failure, "error NU1101")]
    created = _install_pipeline(monkeypatch, tmp_path, runner=_ScriptedDotnetRunner)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=2)

    assert created and created[0].refine_calls >= 1


@pytest.mark.parametrize("failure", ["feed", "sdk", "timeout"])
async def test_switching_the_baseline_off_also_switches_the_mid_run_stops_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """`SDLC_TEST_BASELINE=0` restores the old behaviour entirely: refine is asked, as before."""
    monkeypatch.setenv("SDLC_TEST_BASELINE", "0")
    _ScriptedDotnetRunner.calls = 0
    _ScriptedDotnetRunner.script = [_outcome(False, failure, "NU1301 / timed out")]
    created = _install_pipeline(monkeypatch, tmp_path, runner=_ScriptedDotnetRunner)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=2)

    assert created and created[0].refine_calls >= 1


async def test_the_blocked_message_is_raised_once_not_also_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI prints the exception; logging it as well printed twenty errors twice."""
    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    _ScriptedDotnetRunner.calls = 0
    _ScriptedDotnetRunner.script = [
        _outcome(False, "build", "COMPILER ERRORS:\n  A.cs(1,1): error CS1003: x\n")
    ]
    _install_pipeline(monkeypatch, tmp_path, runner=_ScriptedDotnetRunner)
    logged: list[str] = []

    with pytest.raises(FeatureRunError, match="VERDICT: BLOCKED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=1, log=logged.append)

    assert not any("VERDICT: BLOCKED" in line for line in logged)


# ---- CB-764: `--layout auto` follows an existing repository or refuses; it never scaffolds beside it


async def test_auto_will_not_scaffold_into_a_repo_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    (tmp_path / "lambdas" / "scraper").mkdir(parents=True)
    (tmp_path / "lambdas" / "scraper" / "handler.py").write_text("def handle(e, c):\n    return 1\n")

    with pytest.raises(FeatureRunError, match="will not scaffold") as caught:
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=1)

    assert caught.value.code == 2
    assert not (tmp_path / "pyproject.toml").exists()
    assert not (tmp_path / "src").exists()


async def test_a_sam_repo_runs_without_a_scaffold(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from orchestrator.sdlc import feature_runner as fr

    created = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr(fr, "_files_no_test_exercises", lambda *a, **k: _aresult([]))
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))
    (tmp_path / "template.yaml").write_text(
        "Resources:\n  F:\n    Type: AWS::Serverless::Function\n"
        "    Properties:\n      CodeUri: src/scraper/\n"
    )
    (tmp_path / "src" / "scraper").mkdir(parents=True)
    (tmp_path / "src" / "scraper" / "app.py").write_text("def lambda_handler(e, c):\n    return {}\n")

    await run_feature("file://./spec.md", intent_id="intent-a", max_refine=1)

    assert created and getattr(created[0].layout, "framework", "") == "aws-sam"
    assert not (tmp_path / "pyproject.toml").exists()


# ---- auto-layout-guard: the same stop for every language whose scaffold writes a build file ------

#: language → (one loose source file, the build file its scaffold writes)
_BESIDE: dict[str, tuple[str, str]] = {
    "csharp": ("Legacy/Program.cs", "*.sln"),
    "typescript": ("lib/index.ts", "package.json"),
    "java": ("Main.java", "pom.xml"),
    "kotlin": ("Main.kt", "settings.gradle.kts"),
    "go": ("main.go", "go.mod"),
    "c": ("include/api.h", "CMakeLists.txt"),
    "cpp": ("engine.cc", "CMakeLists.txt"),
}


class _StoppedAfterLayoutError(Exception):
    """Raised where the run would build its test environment — after the layout and scaffold."""


def _stop_after_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _stop(*a: Any, **k: Any) -> Any:
        raise _StoppedAfterLayoutError

    monkeypatch.setattr("orchestrator.sdlc.testenv.make_test_environment", _stop)


def _tree(root: Path) -> set[str]:
    """Every path under ``root`` but the run's own intake bookkeeping, which is not the scaffold."""
    return {
        rel
        for rel in (p.relative_to(root).as_posix() for p in root.rglob("*"))
        if rel != "BACKLOG.md" and not rel.startswith("intake-cache")
    }


@pytest.mark.parametrize("language", sorted(_BESIDE))
async def test_auto_will_not_scaffold_beside_loose_source_in_any_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str
) -> None:
    """One file, no build file: every language stops with exit 2 and writes nothing — a root
    `go.mod` or `.sln` beside code it does not build is a second project, not the repository's."""
    from orchestrator.sdlc.layout import LOOKED_FOR

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _stop_after_layout(monkeypatch)
    source, _build_file = _BESIDE[language]
    (tmp_path / source).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / source).write_text("// existing\n", encoding="utf-8")
    before = _tree(tmp_path)

    with pytest.raises(FeatureRunError, match="will not scaffold") as caught:
        await run_feature("file://./spec.md", intent_id="intent-a", language=language, max_refine=1)

    assert caught.value.code == 2
    assert LOOKED_FOR[language] in str(caught.value)
    assert "--layout new" in str(caught.value)
    assert _tree(tmp_path) == before  # no build file, no source tree


@pytest.mark.parametrize("language", sorted(_BESIDE))
async def test_auto_still_scaffolds_an_empty_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str
) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _stop_after_layout(monkeypatch)
    (tmp_path / "README.md").write_text("# empty\n", encoding="utf-8")

    with pytest.raises(_StoppedAfterLayoutError):
        await run_feature("file://./spec.md", intent_id="intent-a", language=language, max_refine=1)

    assert list(tmp_path.glob(_BESIDE[language][1]))


@pytest.mark.parametrize("language", sorted(_BESIDE))
async def test_layout_new_still_scaffolds_beside_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str
) -> None:
    """`--layout new` is the one way past the stop (D4) — it scaffolds exactly as before."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _stop_after_layout(monkeypatch)
    source, build_file = _BESIDE[language]
    (tmp_path / source).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / source).write_text("// existing\n", encoding="utf-8")

    with pytest.raises(_StoppedAfterLayoutError):
        await run_feature(
            "file://./spec.md", intent_id="intent-a", language=language, layout_mode="new", max_refine=1
        )

    assert list(tmp_path.glob(build_file))


async def test_sql_still_scaffolds_beside_loose_sql(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """SQL's scaffold is a `migrations/README.md` and a `.gitignore` — nothing that could be built
    instead of the repository's own files — so it is the one language `auto` does not stop (D1)."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _stop_after_layout(monkeypatch)
    (tmp_path / "schema.sql").write_text("create table t (id int);\n", encoding="utf-8")

    with pytest.raises(_StoppedAfterLayoutError):
        await run_feature("file://./spec.md", intent_id="intent-a", language="sql", max_refine=1)

    assert (tmp_path / "migrations" / "README.md").is_file()


async def test_a_lone_sample_does_not_stop_the_scaffold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A snippet under `docs/` or `examples/` is documentation, not a project to extend (D3)."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _stop_after_layout(monkeypatch)
    for rel in ("docs/usage.ts", "examples/hello.ts", "samples/demo.ts", "doc/api.ts"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("export {}\n", encoding="utf-8")

    with pytest.raises(_StoppedAfterLayoutError):
        await run_feature("file://./spec.md", intent_id="intent-a", language="typescript", max_refine=1)

    assert (tmp_path / "package.json").is_file()


async def test_a_nested_project_is_followed_not_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D7: the build file one level down is the project; the stop never sees a monorepo."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    seen: dict[str, Any] = {}

    def _stop(language: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _StoppedAfterLayoutError

    monkeypatch.setattr("orchestrator.sdlc.testenv.make_test_environment", _stop)
    (tmp_path / "svc" / "lib").mkdir(parents=True)
    (tmp_path / "svc" / "go.mod").write_text("module example.com/svc\n", encoding="utf-8")
    (tmp_path / "svc" / "lib" / "lib.go").write_text("package lib\n", encoding="utf-8")

    with pytest.raises(_StoppedAfterLayoutError):
        await run_feature("file://./spec.md", intent_id="intent-a", language="go", max_refine=1)

    assert seen.get("project_dir") == "svc"
    assert not (tmp_path / "go.mod").exists()


# ---- P10: a local checkout's uncommitted work is not in the build --------------------------------


def _checkout(root: Path) -> Path:
    import subprocess

    repo = root / "checkout"
    repo.mkdir()
    git = ["git", "-c", "user.email=t@e", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    (repo / "App.cs").write_text("class App {}\n")
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=repo, check=True)
    return repo


async def test_uncommitted_work_in_a_local_repo_is_named(tmp_path: Path) -> None:
    from orchestrator.sdlc.feature_runner import _uncommitted_in

    repo = _checkout(tmp_path)
    assert await _uncommitted_in(str(repo)) == []

    (repo / "App.cs").write_text("class App { int x; }\n")
    (repo / "New.cs").write_text("class New {}\n")
    # What Spine writes itself is never evidence of missing work.
    (repo / ".spine" / "plans").mkdir(parents=True)
    (repo / ".spine" / "plans" / "x-build.md").write_text("plan\n")
    (repo / "BACKLOG.md").write_text("backlog\n")

    assert sorted(await _uncommitted_in(str(repo))) == ["App.cs", "New.cs"]


@pytest.mark.parametrize("repo", [None, "", "https://github.com/acme/app.git", "git@github.com:acme/app.git"])
async def test_a_remote_or_absent_repo_has_nothing_uncommitted(repo: str | None) -> None:
    from orchestrator.sdlc.feature_runner import _uncommitted_in

    assert await _uncommitted_in(repo) == []


async def test_a_directory_that_is_not_a_checkout_is_not_warned_about(tmp_path: Path) -> None:
    from orchestrator.sdlc.feature_runner import _uncommitted_in

    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "x.cs").write_text("")
    assert await _uncommitted_in(str(tmp_path / "plain")) == []


async def test_the_run_warns_before_building_without_local_edits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    worktree = tmp_path / "wt"
    worktree.mkdir()
    _install_pipeline(monkeypatch, worktree, runner=_PassingRunner)
    monkeypatch.setattr(fr, "_files_no_test_exercises", lambda *a, **k: _aresult([]))
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))
    repo = _checkout(tmp_path)
    (repo / "Draft.cs").write_text("class Draft {}\n")
    said: list[str] = []

    await run_feature("file://./spec.md", intent_id="intent-a", repo=str(repo), max_refine=1, log=said.append)

    [warning] = [line for line in said if "uncommitted file(s)" in line]
    assert "Draft.cs" in warning and "clones committed history only" in warning


# ---- SDLC_TEST_COMMAND at the run_feature level (SSPN-137) ------------------------------


async def test_a_bad_test_command_stops_the_run_before_the_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The helpers are unit-tested; this is the call to them in `run_feature`, which survived a
    mutation that replaced it with `None` when only the helpers were covered."""
    codegens = _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setenv("SDLC_TEST_COMMAND", "{broken")

    with pytest.raises(FeatureRunError, match="SDLC_TEST_COMMAND") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a")

    assert exc.value.code == 2
    assert codegens == []  # no model-backed stage was constructed


async def test_a_test_command_bypasses_the_hosts_pytest_check_and_logs_only_the_program(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    import sys

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.testrunner.pytest_available", lambda *a, **k: _aresult(False))
    monkeypatch.setenv("SDLC_TEST_COMMAND", json.dumps([sys.executable, "-c", "pass", "TOKEN=hunter2"]))
    said: list[str] = []

    result = await run_feature("file://./spec.md", intent_id="intent-a", log=said.append)

    assert result.passed is True  # the host has no pytest, and that no longer matters
    joined = "\n".join(said)
    assert "SDLC_TEST_COMMAND" in joined
    assert "hunter2" not in joined  # an argument can carry a credential; only the program is logged


async def test_the_feature_runner_pins_the_base_before_anything_is_generated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without the explicit pin, a runner first used after the build is committed would take the
    committed build as its starting point and select none of the build's tests."""
    import json
    import sys

    from orchestrator.sdlc.testrunner import CommandTestRunner

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setenv("SDLC_TEST_COMMAND", json.dumps([sys.executable, "-c", "pass"]))
    pinned: list[str] = []

    async def _record(self: CommandTestRunner, path: str) -> None:
        pinned.append(path)

    monkeypatch.setattr(CommandTestRunner, "pin_base", _record)

    result = await run_feature("file://./spec.md", intent_id="intent-a")

    assert pinned == [result.worktree]


# --- intake spend is under the run's budget and in its ledger (B32, SSPN-56) -------------------------


class _CostlyLLM:
    """An LLMClient that costs `cost` dollars a call and counts its calls."""

    def __init__(self, cost: float = 0.30) -> None:
        self.cost = cost
        self.calls = 0

    async def complete(self, messages: Any, *, model: str, **kw: Any) -> Any:  # noqa: ARG002
        from orchestrator.core.llm import CompletionResult

        self.calls += 1
        return CompletionResult(
            text="{}",
            model=model,
            prompt_tokens=100,
            completion_tokens=10,
            cost_usd=self.cost,
            latency_ms=1.0,
        )


class _SpendingService:
    """An intake service whose analysis calls the client it was built with, `n` times."""

    def __init__(self, llm: Any, n: int) -> None:
        self._llm, self._n = llm, n

    async def analyze(self, root_id: str) -> _Plan:
        from orchestrator.core.llm import Message

        for _ in range(self._n):
            await self._llm.complete([Message(role="user", content="extract")], model="m")
        return _Plan([])


def _spending_intake(monkeypatch: pytest.MonkeyPatch, *, cost: float = 0.30, n: int = 1) -> _CostlyLLM:
    inner = _CostlyLLM(cost)
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    monkeypatch.setattr("orchestrator.core.llm.LiteLLMClient", lambda *a, **k: inner)
    monkeypatch.setattr(
        "orchestrator.intake.factory.build_service_for",
        lambda *a, llm=None, **k: _SpendingService(llm, n),
    )
    return inner


async def test_intake_spend_is_charged_to_the_run_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.core.llm import RunBudget

    inner = _spending_intake(monkeypatch)
    budget = RunBudget(max_cost_usd=10.0)
    with pytest.raises(FeatureRunError) as exc:  # no specs: the run ends right after intake
        await run_feature("file://./spec.md", budget=budget)
    assert exc.value.code == 3
    assert inner.calls == 1 and budget.spent() == pytest.approx(0.30)


async def test_intake_is_refused_once_the_cap_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.core.llm import BudgetExceededError, RunBudget

    inner = _spending_intake(monkeypatch, cost=0.30, n=3)
    with pytest.raises(BudgetExceededError):
        await run_feature("file://./spec.md", budget=RunBudget(max_cost_usd=0.25))
    assert inner.calls == 1  # the call that crossed the cap finished; the next was refused unspent


async def test_intake_spend_lands_in_the_runs_ledger_under_its_own_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.core.llm.recording import TokenLedger

    _spending_intake(monkeypatch)
    ledger = TokenLedger()
    with pytest.raises(FeatureRunError):
        await run_feature("file://./spec.md", ledger=ledger)
    assert ledger.total().total_tokens == 110
    assert "intake" in ledger.stages


# ---- a real change no test exercises is withdrawn, not the whole run (SSPN-195 / B91, part B) ----
#
# sympy-11618: the fix in `point.py` was tested; the model had also added unrequested checks to
# `line.py` and `line3d.py` that no test touched. After the cover attempts the gate ended the run
# FAILED and discarded a patch that resolves when graded. The helper below takes the untested
# files out, proves the rest still stands, and says so. These tests use a real git repo and a
# fake suite: no model, no spend.


def _stash_count(root: Path) -> int:
    out = subprocess.run(["git", "stash", "list"], cwd=root, capture_output=True, text=True, check=True)
    return len(out.stdout.splitlines())


class _NeedsFiles:
    """Passes only while every named file still holds its changed text."""

    def __init__(self, needs: dict[str, str]) -> None:
        self.needs = needs
        self.runs = 0

    async def run(self, *, path: str) -> SimpleNamespace:
        self.runs += 1
        ok = all(
            (Path(path) / rel).exists() and (Path(path) / rel).read_text() == txt
            for rel, txt in self.needs.items()
        )
        return SimpleNamespace(passed=ok, returncode=0 if ok else 1, output="")


def _two_file_change(tmp_path: Path) -> tuple[Path, list[str]]:
    """`src/fix.py` is the tested fix; `src/extra.py` gained an edit nothing tests."""
    root = _repo_with(
        tmp_path,
        {
            "src/fix.py": "X = 1\n",
            "src/extra.py": "Y = 1\n",
            "tests/test_fix.py": "def test_it():\n    pass\n",
        },
    )
    (root / "src/fix.py").write_text("X = 2\n", encoding="utf-8")
    (root / "src/extra.py").write_text("Y = 2\n", encoding="utf-8")
    return root, ["src/fix.py", "src/extra.py", "tests/test_fix.py"]


async def test_an_untested_file_is_withdrawn_when_the_rest_still_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    root, changed = _two_file_change(tmp_path)
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    runner = _NeedsFiles({"src/fix.py": "X = 2\n"})
    log: list[str] = []

    withdrawn = await fr._withdraw_untested_production(
        root, changed, ["src/extra.py"], runner, None, log.append
    )

    assert withdrawn == ["src/extra.py"]
    assert (root / "src/extra.py").read_text() == "Y = 1\n"  # back to HEAD
    assert (root / "src/fix.py").read_text() == "X = 2\n"  # the tested fix is untouched
    assert _stash_count(root) == 0  # nothing left behind
    assert any("[coverage] withdrawn: extra.py" in line for line in log), log


async def test_a_withdrawal_that_reddens_the_suite_is_undone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the untested file was needed after all, the run fails exactly as it did, with the
    model's change back in place."""
    from orchestrator.sdlc import feature_runner as fr

    root, changed = _two_file_change(tmp_path)
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    runner = _NeedsFiles({"src/fix.py": "X = 2\n", "src/extra.py": "Y = 2\n"})
    log: list[str] = []

    withdrawn = await fr._withdraw_untested_production(
        root, changed, ["src/extra.py"], runner, None, log.append
    )

    assert withdrawn == []
    assert (root / "src/extra.py").read_text() == "Y = 2\n"
    assert _stash_count(root) == 0
    assert any("withdrawal did not hold" in line for line in log), log


async def test_a_withdrawal_that_breaks_the_type_check_is_undone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    root, changed = _two_file_change(tmp_path)
    monkeypatch.setattr(
        fr, "_typecheck_the_change", lambda *a, **k: _aresult("TYPE ERRORS introduced by this change")
    )
    runner = _NeedsFiles({"src/fix.py": "X = 2\n"})

    withdrawn = await fr._withdraw_untested_production(
        root, changed, ["src/extra.py"], runner, None, lambda _m: None
    )

    assert withdrawn == []
    assert (root / "src/extra.py").read_text() == "Y = 2\n"
    assert _stash_count(root) == 0


async def test_nothing_is_withdrawn_when_no_tested_production_file_would_remain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    root, changed = _two_file_change(tmp_path)
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    runner = _NeedsFiles({})

    withdrawn = await fr._withdraw_untested_production(
        root, changed, ["src/fix.py", "src/extra.py"], runner, None, lambda _m: None
    )

    assert withdrawn == []
    assert runner.runs == 0  # decided before touching anything
    assert (root / "src/fix.py").read_text() == "X = 2\n" and (root / "src/extra.py").read_text() == "Y = 2\n"


async def test_nothing_is_withdrawn_when_some_production_file_was_never_probed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The probe looks at four files. Past four, "the rest is tested" cannot be claimed."""
    from orchestrator.sdlc import feature_runner as fr

    files = {f"src/m{i}.py": "V = 1\n" for i in range(6)} | {"tests/test_m.py": "def test_it():\n    pass\n"}
    root = _repo_with(tmp_path, files)
    for i in range(6):
        (root / f"src/m{i}.py").write_text("V = 2\n", encoding="utf-8")
    changed = [f"src/m{i}.py" for i in range(6)] + ["tests/test_m.py"]
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    runner = _NeedsFiles({})

    withdrawn = await fr._withdraw_untested_production(
        root, changed, ["src/m5.py"], runner, None, lambda _m: None
    )

    assert withdrawn == [] and runner.runs == 0


async def test_a_new_untracked_file_is_withdrawn_and_comes_back_when_the_attempt_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    root = _repo_with(tmp_path, {"src/fix.py": "X = 1\n", "tests/test_fix.py": "def test_it():\n    pass\n"})
    (root / "src/fix.py").write_text("X = 2\n", encoding="utf-8")
    (root / "src/helper.py").write_text("H = 1\n", encoding="utf-8")  # new, nothing tests it
    changed = ["src/fix.py", "src/helper.py", "tests/test_fix.py"]
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))

    ok = await fr._withdraw_untested_production(
        root, changed, ["src/helper.py"], _NeedsFiles({"src/fix.py": "X = 2\n"}), None, lambda _m: None
    )
    assert ok == ["src/helper.py"] and not (root / "src/helper.py").exists()

    (root / "src/helper.py").write_text("H = 1\n", encoding="utf-8")
    nope = await fr._withdraw_untested_production(
        root,
        changed,
        ["src/helper.py"],
        _NeedsFiles({"src/fix.py": "X = 2\n", "src/helper.py": "H = 1\n"}),
        None,
        lambda _m: None,
    )
    assert nope == [] and (root / "src/helper.py").read_text() == "H = 1\n"
    assert _stash_count(root) == 0


@pytest.mark.parametrize(
    ("value", "enabled"),
    [(None, True), ("1", True), ("", True), ("0", False), ("false", False), ("No", False), ("off", False)],
)
def test_the_withdrawal_switch_defaults_on_and_zero_turns_it_off(
    monkeypatch: pytest.MonkeyPatch, value: str | None, enabled: bool
) -> None:
    from orchestrator.sdlc.feature_runner import coverage_withdraw_enabled

    if value is None:
        monkeypatch.delenv("SDLC_COVERAGE_WITHDRAW", raising=False)
    else:
        monkeypatch.setenv("SDLC_COVERAGE_WITHDRAW", value)

    assert coverage_withdraw_enabled() is enabled


def _gap_for_three_probes_then_clean() -> Any:
    """The first probe and the two cover attempts' re-probes all find `src/x.py`; after a
    withdrawal there is nothing left to find."""
    probes: list[int] = []

    async def _probe(path: Path, files: list[str], runner: Any, emit: Any) -> list[str]:
        probes.append(1)
        return ["src/x.py"] if len(probes) <= 3 else []

    return _probe


class _WritesXCodegen(_StubCodegen):
    async def implement(self, **kwargs: Any) -> CodeChange:
        root = Path(kwargs["path"])
        (root / "src").mkdir(exist_ok=True)
        (root / "src" / "x.py").write_text("def main() -> None:\n    print('ok')\n", encoding="utf-8")
        return CodeChange(files=["src/x.py"], summary="impl")

    async def author_tests(self, **kwargs: Any) -> CodeChange:
        root = Path(kwargs["path"])
        (root / "tests").mkdir(exist_ok=True)
        name = "test_cover.py" if kwargs.get("gaps") else "test_x.py"
        (root / "tests" / name).write_text("def test_it() -> None:\n    pass\n", encoding="utf-8")
        return CodeChange(files=[f"tests/{name}"], summary="tests")


async def test_a_run_whose_gap_survives_the_cover_attempts_submits_what_is_proven(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_WritesXCodegen)
    monkeypatch.delenv("SDLC_COVERAGE_WITHDRAW", raising=False)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _gap_for_three_probes_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))
    seen: dict[str, Any] = {}

    async def _withdraw(
        path: Path, changed: list[str], gaps: list[str], runner: Any, testenv: Any, emit: Any
    ) -> list[str]:
        seen["gaps"] = list(gaps)
        return list(gaps)

    monkeypatch.setattr(fr, "_withdraw_untested_production", _withdraw)

    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3, log=lambda _m: None)

    assert result.passed
    assert result.coverage_withdrawn_production == ["src/x.py"]
    assert result.coverage_withdrawn == []  # that field still names tests only
    assert seen["gaps"] == ["src/x.py"]


async def test_with_the_switch_off_a_surviving_gap_still_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_WritesXCodegen)
    monkeypatch.setenv("SDLC_COVERAGE_WITHDRAW", "0")
    monkeypatch.setattr(fr, "_files_no_test_exercises", _gap_for_three_probes_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))

    async def _never(*a: Any, **k: Any) -> list[str]:
        raise AssertionError("the withdrawal must not run with the switch off")

    monkeypatch.setattr(fr, "_withdraw_untested_production", _never)

    with pytest.raises(FeatureRunError) as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3, log=lambda _m: None)

    assert "FAILED" in str(exc.value)


async def test_a_withdrawal_that_does_not_hold_leaves_the_run_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_WritesXCodegen)
    monkeypatch.delenv("SDLC_COVERAGE_WITHDRAW", raising=False)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _gap_for_three_probes_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_withdraw_untested_production", lambda *a, **k: _aresult([]))
    log: list[str] = []

    with pytest.raises(FeatureRunError):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3, log=log.append)

    assert any("out of coverage attempts" in line for line in log), log


def test_the_pr_body_names_a_withdrawn_production_file() -> None:
    from orchestrator.sdlc.feature_runner import _pr_body

    spec = {"summary": "S", "acceptance_criteria": ["a"], "intent_id": "i"}

    plain = _pr_body(spec, [])
    named = _pr_body(spec, [], ["src/extra.py"])

    assert "withdrawn" not in plain.lower()
    assert "`src/extra.py`" in named and "not proven" in named


def test_the_pr_body_for_plain_ticket_text_is_exactly_what_it_was() -> None:
    from orchestrator.sdlc.feature_runner import _pr_body

    spec = {"summary": "Add `x`.\n\nSecond paragraph.", "acceptance_criteria": ["a", "- b"], "intent_id": "i"}

    assert _pr_body(spec, ["t.py"], ["p.py"]) == (
        "Add `x`.\n\nSecond paragraph.\n\nAcceptance criteria:\n- a\n- - b"
        "\n\n**Coverage withdrawn:** `t.py` \u2014 written by this run to cover a gap, then withdrawn "
        "because no refine could satisfy it. That coverage is not proven."
        "\n\n**Production change withdrawn:** `p.py` \u2014 no test exercised it after the cover "
        "attempts, so the run took it out and kept the rest; the suite and type check pass without it. "
        "What it would have done is not proven."
        "\n\nGenerated by the SDLC orchestrator (intent i)."
    )


def test_the_pr_body_neutralises_the_tickets_mentions_html_and_images() -> None:
    """B97: the summary and criteria are the ticket's words, copied by a model told to keep identifiers
    verbatim. `@dataclass` and `@Override` are real GitHub accounts; raw HTML and images render."""
    from orchestrator.sdlc.feature_runner import _pr_body

    spec = {
        "summary": "Fix the @dataclass handling, cc @octocat. <img src=x> ![i](http://example.invalid/p.png)",
        "acceptance_criteria": ["Given @Override, `@dataclass` stays code <script>x</script>"],
        "intent_id": "i",
    }

    body = _pr_body(spec, [])

    assert not re.search(r"@\w", body.replace("`@dataclass`", ""))
    assert "<img" not in body and "<script" not in body and "![" not in body
    assert "`@dataclass` stays code" in body


def test_the_pr_body_names_a_withdrawn_file_whose_name_has_a_backtick_in_a_working_code_span() -> None:
    from orchestrator.sdlc.feature_runner import _pr_body

    spec = {"summary": "S", "acceptance_criteria": ["a"], "intent_id": "i"}

    body = _pr_body(spec, ["tests/a`b.py"], ["src/c`.py"])

    assert "``tests/a`b.py``" in body
    assert "``src/c`.py``" in body


# ---- proof-bundle P1: the run records what it established --------------------------------------


class _NamingPassingRunner(_PassingRunner):
    """A runner that names its failures, whose suite is green before and after."""

    names_problems = True


class _ManifestPassing:
    """A required-behavior manifest that exists, was run, and is green."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(passed=True, items=[SimpleNamespace(environment_blocked=False)], output="ok")


def _quiet_gates(monkeypatch: pytest.MonkeyPatch, files: list[str]) -> None:
    """Take the worktree out of the picture: the change is exactly ``files``, and no gate that
    needs a real checkout runs."""
    from orchestrator.sdlc import feature_runner as fr

    monkeypatch.setattr(fr, "_changed_files", lambda path: _aresult(list(files)))
    monkeypatch.setattr(fr, "_files_no_test_exercises", lambda *a, **k: _aresult([]))
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))


def _blast(monkeypatch: pytest.MonkeyPatch, answer: tuple[int, str] = (0, "")) -> list[str]:
    """Replace the blast-radius measurement; the returned list records each time it was asked."""
    asked: list[str] = []

    async def _measure(path: str, timeout: float) -> tuple[int, str]:
        asked.append(str(path))
        return answer

    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._measure_blast", _measure)
    return asked


async def _live_build(**extra: Any) -> Any:
    return await run_feature(
        "file://./spec.md",
        intent_id="intent-a",
        repo="https://x/widget",
        live=True,
        issue="SSPN-1",
        publish=False,
        **extra,
    )


async def test_the_result_carries_what_the_judge_said_about_each_criterion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc.review import CriterionVerdict

    rows = [CriterionVerdict("exports a CSV file", "met", "export_csv writes rows")]
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", _Judge("approve", criteria=rows))
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build()

    facts = result.facts
    assert facts is not None and facts.judge is not None
    assert facts.judge.verdict == "approve" and facts.judge.revisions == 0
    assert facts.judge.criteria == tuple(rows)
    assert facts.issue_key == "SSPN-1" and facts.intent_id == "intent-a"
    assert facts.files == ("src/a.py",) and tuple(result.files) == facts.files


async def test_the_facts_count_the_revisions_the_judge_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    judge = _Judge(["request_changes", "approve"], ["documented in USER_GUIDE step 9"])
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, codegen=_RevisingCodegen)
    monkeypatch.setattr("orchestrator.sdlc.review.SemanticReviewAdapter", judge)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build()

    assert result.facts.judge.verdict == "approve"
    assert result.facts.judge.revisions == 1  # approved the second time it was asked


async def test_a_runner_that_names_no_failures_takes_no_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build()

    assert result.facts.baseline.state == "not_taken"


async def test_a_green_baseline_is_recorded_as_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    _install_pipeline(monkeypatch, tmp_path, runner=_NamingPassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build()

    assert result.facts.baseline.state == "clean" and result.facts.baseline.failures == ()


async def test_failures_that_predate_the_change_are_recorded_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SDLC_TEST_BASELINE", raising=False)
    _install_pipeline(monkeypatch, tmp_path, runner=_PreexistingFailureRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build(max_refine=5)

    assert result.facts.baseline.state == "preexisting"
    assert result.facts.baseline.failures == ("tests/test_api.py",)


async def test_a_disabled_baseline_is_recorded_as_not_taken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SDLC_TEST_BASELINE", "0")
    _install_pipeline(monkeypatch, tmp_path, runner=_NamingPassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build()

    assert result.facts.baseline.state == "not_taken"


async def test_a_python_change_has_its_risk_assessed_before_the_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    asked = _blast(monkeypatch, (12, "py:pkg.core.run"))

    result = await _live_build()

    risk = result.facts.risk
    assert asked == [str(tmp_path)]
    assert risk.state == "computed" and risk.blast_radius == 12 and risk.escalate is True
    # Annotation only: a risky change is still built and handed back, not stopped.
    assert result.passed and result.publish is not None


async def test_a_change_with_no_python_file_reports_the_radius_as_not_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _quiet_gates(monkeypatch, ["README.md", "src/Main.java"])
    asked = _blast(monkeypatch)

    result = await _live_build()

    assert asked == []
    assert result.facts.risk.state == "computed" and result.facts.risk.blast_radius is None


async def test_a_safe_run_does_not_pay_for_a_risk_measurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No PR is opened in safe mode, so nothing would show the figure — and the measurement
    re-extracts the whole worktree."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    asked = _blast(monkeypatch, (99, "py:x"))

    result = await run_feature("file://./spec.md", intent_id="intent-a")

    assert asked == []
    assert result.facts is not None
    assert result.facts.risk.state == "not_computed" and "safe" in result.facts.risk.detail


async def test_a_risk_measurement_that_fails_never_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])

    async def _boom(path: str, timeout: float) -> Any:
        raise RuntimeError("extractor fell over")

    monkeypatch.setattr("orchestrator.sdlc.proof_bundle._measure_blast", _boom)

    result = await _live_build()

    assert result.passed and result.publish is not None
    assert result.facts.risk.state == "not_computed" and "RuntimeError" in result.facts.risk.detail


async def test_the_off_switch_records_no_facts_and_measures_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setenv("SDLC_PROOF_BUNDLE", "0")
    _quiet_gates(monkeypatch, ["src/a.py"])
    asked = _blast(monkeypatch, (99, "py:x"))

    result = await _live_build()

    assert result.facts is None and asked == []
    assert result.passed and result.publish is not None  # the run itself is unchanged


async def test_a_project_without_a_manifest_is_recorded_as_having_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real runner self-skips with passed=True and no items; "passed" would overstate it."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build()

    assert result.facts.required_behavior == "no_manifest"


async def test_a_manifest_that_ran_green_is_recorded_as_passed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner, required_behavior=_ManifestPassing)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build()

    assert result.facts.required_behavior == "passed"


async def test_the_facts_carry_a_cost_snapshot_and_the_withdrawn_lists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc.proof_bundle import CostFacts

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch)

    result = await _live_build()

    assert isinstance(result.facts.cost, CostFacts)
    assert result.facts.coverage_withdrawn == () and result.facts.coverage_withdrawn_production == ()


async def test_a_run_that_passes_by_withdrawing_a_test_never_reached_the_required_behavior_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Found by the mutation sweep: ``required_behavior_state`` starting as "passed" survived. A
    run can end green on the withdrawal path without the manifest check ever running, and the
    bundle must say "not reached" there, not "passed" and not "no manifest"."""
    from orchestrator.sdlc import feature_runner as fr

    _install_pipeline(monkeypatch, tmp_path, runner=_CoverAwareRunner, codegen=_CoverCodegen)
    monkeypatch.setattr(fr, "_files_no_test_exercises", _one_gap_then_clean())
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))

    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)

    assert result.passed and result.coverage_withdrawn == ["tests/test_main_stdout.py"]
    assert result.facts is not None
    assert result.facts.required_behavior == "not_reached"
    assert result.facts.coverage_withdrawn == ("tests/test_main_stdout.py",)


# ---- proof-bundle P3: the PR carries the bundle -----------------------------------------------


def _init_repo(root: Path) -> str:
    """A real repository with a local identity (CI has none), one commit; returns that commit."""

    def _g(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    _g("init", "-q")
    _g("config", "user.email", "t@e")
    _g("config", "user.name", "t")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _g("add", "-A")
    _g("commit", "-q", "-m", "base")
    return _g("rev-parse", "HEAD")


def _git_out(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


async def _published(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, publish: bool = True, **extra: Any
) -> Any:
    """Run a live build through the recording forge; returns the result."""
    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    _quiet_gates(monkeypatch, ["src/a.py"])
    _blast(monkeypatch, (3, "py:pkg.core.run"))
    _RecordingForge.opened = []
    monkeypatch.setattr("orchestrator.sdlc.forge.GhPRAdapter", _RecordingForge)
    return await run_feature(
        "file://./spec.md",
        intent_id="intent-a",
        repo="https://x/widget",
        live=True,
        issue="SSPN-1",
        publish=publish,
        **extra,
    )


async def test_the_pr_body_carries_the_proof_bundle_after_the_ticket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _published(monkeypatch, tmp_path)

    [opened] = _RecordingForge.opened
    body = opened["body"]
    assert "### Spine proof bundle" in body
    assert body.index("Acceptance criteria:") < body.index("### Spine proof bundle")
    assert "Generated by the SDLC orchestrator (intent intent-a)." in body
    assert "No review-loop fixes followed the judge." in body  # a direct publish has no review loop


async def test_the_off_switch_leaves_the_pr_body_as_it_was(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc.feature_runner import _pr_body

    _install_pipeline(monkeypatch, tmp_path, runner=_PassingRunner)
    monkeypatch.setenv("SDLC_PROOF_BUNDLE", "0")
    _quiet_gates(monkeypatch, ["src/a.py"])
    _RecordingForge.opened = []
    monkeypatch.setattr("orchestrator.sdlc.forge.GhPRAdapter", _RecordingForge)

    await run_feature(
        "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
    )

    [opened] = _RecordingForge.opened
    spec = {"summary": "S", "acceptance_criteria": ["c"], "intent_id": "intent-a"}
    assert opened["body"] == _pr_body(spec, [], [])
    assert "proof bundle" not in opened["body"].lower()


async def test_a_bundle_that_cannot_be_built_still_opens_the_pr_and_says_so_in_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("could not hash")

    monkeypatch.setattr(fr, "build_bundle", _boom)

    result = await _published(monkeypatch, tmp_path)

    [opened] = _RecordingForge.opened
    assert result.pr_url == "https://github.com/x/y/pull/8"
    assert "Spine proof bundle unavailable: RuntimeError" in opened["body"]
    assert "### Spine proof bundle" not in opened["body"]
    assert opened["body"].count("proof bundle unavailable") == 1


async def test_the_note_follows_the_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = await _published(monkeypatch, tmp_path, publish=False)
    await result.publish(draft=True, note="Unresolved: x.py:2 bare except")

    [opened] = _RecordingForge.opened
    body = opened["body"]
    assert body.index("### Spine proof bundle") < body.index("Unresolved: x.py:2 bare except")
    assert opened["draft"] is True


def _loop(*, clean: bool) -> Any:
    from orchestrator.codereview.verifiers import Finding, Severity
    from orchestrator.sdlc.reviewloop import LoopResult, Round

    left = [] if clean else [Finding("v", "r", Severity.WARNING, "src/a.py", 7, "hardcoded credential")]
    return LoopResult(
        rounds=[Round(number=1, findings=len(left))],
        remaining=left,
        stopped="review clean" if clean else "the fixer edited nothing",
        clean=clean,
    )


async def test_an_unclean_review_reaches_the_bundle_json_and_the_pr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D13: the review loop's outcome is what makes a PR a draft; the machine-readable record
    must be able to say so, not only the note."""
    import json

    record = tmp_path / "artifacts"
    result = await _published(monkeypatch, tmp_path, publish=False, record_dir=record)

    await result.publish(draft=True, note="n", review=_loop(clean=False))

    review = json.loads((record / "proof-bundle.json").read_text(encoding="utf-8"))["checks"]["review"]
    assert review["status"] == "checked" and review["outcome"] == "unresolved"
    assert review["unresolved"][0]["path"] == "src/a.py" and review["unresolved_total"] == 1
    [opened] = _RecordingForge.opened
    assert "**Unresolved review findings:** `src/a.py:7`" in opened["body"]


async def test_a_clean_review_reaches_the_bundle_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    record = tmp_path / "artifacts"
    result = await _published(monkeypatch, tmp_path, publish=False, record_dir=record)

    await result.publish(draft=False, note="", review=_loop(clean=True))

    review = json.loads((record / "proof-bundle.json").read_text(encoding="utf-8"))["checks"]["review"]
    assert review["outcome"] == "clean" and review["rounds"] == 1


async def test_a_supervised_publish_with_no_review_says_none_ran(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The publish step the supervisor calls expects a review. None given is a gap, not a pass."""
    import json

    record = tmp_path / "artifacts"
    result = await _published(monkeypatch, tmp_path, publish=False, record_dir=record)

    await result.publish(draft=False, note="")

    bundle = json.loads((record / "proof-bundle.json").read_text(encoding="utf-8"))
    assert bundle["checks"]["review"]["status"] == "not_checked"
    assert "review" in bundle["not_recorded"]


async def test_a_direct_publish_has_no_review_loop_and_says_not_applicable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    record = tmp_path / "artifacts"
    await _published(monkeypatch, tmp_path, publish=True, record_dir=record)

    bundle = json.loads((record / "proof-bundle.json").read_text(encoding="utf-8"))
    assert bundle["checks"]["review"]["status"] == "not_applicable"
    assert "review" not in bundle["not_recorded"]


async def test_facts_that_cannot_be_gathered_still_open_the_pr_and_say_so_in_one_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D7: the bundle never fails the work. Gathering the facts runs after the paid build and the
    human gate, so a raise there would have skipped the ticket release and the cost log."""
    from orchestrator.sdlc import feature_runner as fr

    secret = "sk-LIVE-ABCDEF0123456789"
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", secret)

    async def _boom(**kw: Any) -> Any:
        raise RuntimeError(f"could not read {secret}")

    monkeypatch.setattr(fr, "_gather_facts", _boom)
    lines: list[str] = []

    result = await _published(monkeypatch, tmp_path, log=lines.append)

    [opened] = _RecordingForge.opened
    assert result.passed and result.pr_url == "https://github.com/x/y/pull/8"
    assert opened["body"].count("Spine proof bundle unavailable: RuntimeError") == 1
    assert "### Spine proof bundle" not in opened["body"]
    assert any("[proof-bundle] unavailable: RuntimeError" in ln for ln in lines)
    assert secret not in "\n".join(lines) + opened["body"]  # the log line is redacted too


async def test_a_bundle_error_is_redacted_in_the_log_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.sdlc import feature_runner as fr

    secret = "sk-LIVE-ABCDEF0123456789"
    monkeypatch.setenv("FAKE_SERVICE_API_KEY", secret)

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError(f"cannot hash {secret}")

    monkeypatch.setattr(fr, "build_bundle", _boom)
    lines: list[str] = []

    await _published(monkeypatch, tmp_path, log=lines.append)

    assert any("unavailable: RuntimeError" in ln for ln in lines)
    assert secret not in "\n".join(lines)


async def test_the_json_is_written_to_the_run_directory_and_verifies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    from orchestrator.sdlc.proof_bundle import verify_bundle

    record = tmp_path / "artifacts"
    await _published(monkeypatch, tmp_path, record_dir=record)

    bundle = json.loads((record / "proof-bundle.json").read_text(encoding="utf-8"))
    assert bundle["schema"] == "spine.proof-bundle/1" and bundle["issue_key"] == "SSPN-1"
    assert verify_bundle(bundle) is True


async def test_the_cost_is_taken_when_the_pr_opens_not_when_the_build_ended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Autorun's review loop keeps spending after run_feature returns; a snapshot from the end of
    the build would understate what the PR cost."""
    from orchestrator.sdlc.proof_bundle import CostFacts

    taken: list[int] = []

    def _snapshot(ledger: Any) -> CostFacts:
        taken.append(1)
        return CostFacts(calls=len(taken))

    monkeypatch.setattr("orchestrator.sdlc.proof_bundle.CostFacts.from_ledger", staticmethod(_snapshot))
    record = tmp_path / "artifacts"
    result = await _published(monkeypatch, tmp_path, publish=False, record_dir=record)
    assert result.facts.cost.calls == 1  # the build's own snapshot

    await result.publish(draft=False, note="")

    import json

    bundle = json.loads((record / "proof-bundle.json").read_text(encoding="utf-8"))
    assert bundle["cost"]["calls"] == 2  # re-taken at publish


async def test_fixes_committed_after_the_build_are_counted_and_the_diff_is_hashed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hashlib
    import json

    start = _init_repo(tmp_path)
    record = tmp_path.parent / f"{tmp_path.name}-artifacts"
    result = await _published(monkeypatch, tmp_path, publish=False, record_dir=record)
    for n in (1, 2):  # the review loop's fixes: one commit each, after the build commit
        (tmp_path / f"fix{n}.txt").write_text(f"fix {n}\n", encoding="utf-8")
        _git_out(tmp_path, "add", "-A")
        _git_out(tmp_path, "commit", "-q", "-m", f"fix {n}")

    await result.publish(draft=False, note="")

    bundle = json.loads((record / "proof-bundle.json").read_text(encoding="utf-8"))
    assert bundle["checks"]["acceptance"]["fixes_after_judge"] == 2
    [opened] = _RecordingForge.opened
    assert "2 review-loop fix(es) came after it" in opened["body"]
    diff = subprocess.run(
        ["git", "diff", "--cached", "--binary", "--no-color", "--no-ext-diff", start],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    ).stdout
    assert bundle["integrity"]["diff_sha256"] == hashlib.sha256(diff).hexdigest()
    assert b"fix1.txt" in diff and b"fix2.txt" in diff


async def test_outside_a_repository_the_diff_and_the_fixes_are_not_recorded_not_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    record = tmp_path.parent / f"{tmp_path.name}-artifacts"
    result = await _published(monkeypatch, tmp_path, publish=False, record_dir=record)

    await result.publish(draft=False, note="")

    bundle = json.loads((record / "proof-bundle.json").read_text(encoding="utf-8"))
    assert bundle["integrity"]["diff_sha256"] is None
    assert bundle["checks"]["acceptance"]["fixes_after_judge"] is None
    [opened] = _RecordingForge.opened
    assert "whether review-loop fixes followed the judge is not recorded" in opened["body"]


async def test_a_git_failure_is_not_a_hash_of_nothing(tmp_path: Path) -> None:
    """Found by the mutation sweep: when git fails its stdout is empty, and the SHA-256 of empty
    output looks exactly like a real hash of a diff. A failure must be None, which the bundle then
    reports as "diff hash not recorded"."""
    import hashlib

    from orchestrator.sdlc.feature_runner import _commits_since, _diff_sha256, _head_sha

    # Not a repository: nothing can be told.
    assert await _head_sha(tmp_path) is None
    assert await _diff_sha256(tmp_path, "a" * 40) is None
    assert await _commits_since(tmp_path, "a" * 40) is None

    start = _init_repo(tmp_path)
    assert await _head_sha(tmp_path) == start
    # A repository, but a start commit it has never heard of.
    assert await _diff_sha256(tmp_path, "0" * 40) is None
    assert await _commits_since(tmp_path, "0" * 40) is None
    # And the working case, so the failures above are not just a broken helper.
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")
    digest = await _diff_sha256(tmp_path, start)
    expected = _git_out(tmp_path, "diff", "--cached", "--binary", "--no-color", "--no-ext-diff", start)
    assert digest == hashlib.sha256(expected.encode()).hexdigest() and "new.txt" in expected
    assert await _commits_since(tmp_path, start) == 0


# ---- SSPN-227: the loop would give up, and the only red tests are the ones the run wrote ----------------


class _OwnTestCodegen(_StubCodegen):
    """`author_tests` writes a test whose expectation is wrong; `refine` can only edit the module."""

    async def implement(self, **kwargs: Any) -> CodeChange:
        root = Path(kwargs["path"])
        (root / "src").mkdir(exist_ok=True)
        (root / "src" / "x.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
        return CodeChange(files=["src/x.py"], summary="impl")

    async def author_tests(self, **kwargs: Any) -> CodeChange:
        root = Path(kwargs["path"])
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_own.py").write_text(_OWN_TEST_SOURCE, encoding="utf-8")
        return CodeChange(files=["tests/test_own.py"], summary="tests")

    async def refine(self, **kwargs: Any) -> CodeChange:
        self.refine_calls += 1
        return CodeChange(files=["src/x.py"], summary="fix f()")


class _OwnTestCodegenThatEditsNothing(_OwnTestCodegen):
    async def refine(self, **kwargs: Any) -> CodeChange:
        self.refine_calls += 1
        return CodeChange(summary="the test is wrong; no source change needed")


_OWN_TEST_SOURCE = "def test_wrong_expectation() -> None:\n    assert False\n"
_EXISTING_FAIL = "FAIL: test_existing_behaviour (pkg.tests.ExistingTests)\n"
_OTHER_FAIL = "FAIL: test_resolve_a_named_url (urlpatterns_reverse.tests.ResolverTests)\n"
_OWN_FAIL = (
    "FAIL: test_wrong_expectation (test_own.OwnTests)\n"
    "----------------------------------------------------------------------\n"
    "AssertionError: 1 != 2\n\n"
)


class _OwnTestsRedRunner:
    """Red exactly while the run's own test exists. Five tests run: its one and four existing ones."""

    ran_while_red = 5

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls = 0

    def _red(self) -> str:
        return f"{_OWN_FAIL}Ran {self.ran_while_red} tests in 0.01s\n\nFAILED (failures=1)\n"

    async def run(self, *, path: str) -> SimpleNamespace:
        self.calls += 1
        if (Path(path) / "tests" / "test_own.py").exists():
            return SimpleNamespace(passed=False, returncode=1, output=self._red())
        return SimpleNamespace(passed=True, returncode=0, output="Ran 4 tests in 0.01s\n\nOK\n")


class _OwnTestsRedAndMovingRunner(_OwnTestsRedRunner):
    """The same, but the assertion text moves on every run, so nothing stalls (the allowance is spent)."""

    def _red(self) -> str:
        return f"{_OWN_FAIL}AssertionError: {self.calls} != 2\nRan 5 tests in 0.01s\n\nFAILED (failures=1)\n"


class _OnlyOwnTestsRanRunner(_OwnTestsRedRunner):
    ran_while_red = 1


class _ExistingTestAlsoFailsRunner(_OwnTestsRedRunner):
    """Red while the run's test exists, and an existing test is red too — before and after."""

    def _red(self) -> str:
        return f"{_OWN_FAIL}{_EXISTING_FAIL}Ran 5 tests in 0.01s\n\nFAILED (failures=2)\n"

    async def run(self, *, path: str) -> SimpleNamespace:
        out = await super().run(path=path)
        if out.passed:
            return SimpleNamespace(
                passed=False,
                returncode=1,
                output=_EXISTING_FAIL + "Ran 4 tests in 0.01s\n\nFAILED\n",
            )
        return out


class _UnnamedFailureRunner:
    """Runs a suite that does not name what failed (a runner whose output carries no test names)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, *, path: str) -> SimpleNamespace:
        return SimpleNamespace(
            passed=False, returncode=1, output="E   assert 1 == 2\n1 failed, 4 passed in 0.01s"
        )


class _OtherTestFailsRunner(_OwnTestsRedRunner):
    """The failing test is one the run did not write."""

    def _red(self) -> str:
        return _OTHER_FAIL + "Ran 5 tests in 0.01s\n\nFAILED\n"


def _stub_repo_checks(monkeypatch: pytest.MonkeyPatch, *, gaps: list[str] | None = None) -> None:
    """Take the checks that need a real repository out of the picture; the coverage gate may object."""
    from orchestrator.sdlc import feature_runner as fr

    async def _gaps(path: Path, files: list[str], runner: Any, emit: Any) -> list[str]:
        return list(gaps or [])

    monkeypatch.setattr(fr, "_files_no_test_exercises", _gaps)
    monkeypatch.setattr(fr, "_typecheck_the_change", lambda *a, **k: _aresult(""))
    monkeypatch.setattr(fr, "_prove_the_tests_test_something", lambda *a, **k: _aresult(None))


async def test_a_run_stalled_on_its_own_wrong_test_withdraws_it_and_releases_the_change_unproven(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REPLAY1: for 14 of 23 stuck runs the model's first patch already resolved; the loop kept rewriting it
    to satisfy a test the run itself had written. When nothing but that test is red, and existing tests ran
    and passed, the test goes, the change is released, and the label says it is unproven."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_OwnTestsRedRunner, codegen=_OwnTestCodegen)
    _stub_repo_checks(monkeypatch)
    log: list[str] = []

    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5, log=log.append)

    assert result.passed
    assert result.own_tests_withdrawn == ["tests/test_own.py"]
    assert not (tmp_path / "tests" / "test_own.py").exists()
    assert (tmp_path / "src" / "x.py").exists()  # the change stands
    assert created[0].refine_calls == 2  # the stall (B80) is where it would have stopped
    rescue = [ln for ln in log if ln.startswith("[rescue]")]
    assert rescue and "test_own.py" in rescue[0] and "unproven" in rescue[0], log


async def test_a_run_that_spent_its_allowance_on_its_own_wrong_test_is_rescued_the_same_way(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = _install_pipeline(
        monkeypatch, tmp_path, runner=_OwnTestsRedAndMovingRunner, codegen=_OwnTestCodegen
    )
    _stub_repo_checks(monkeypatch)

    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)

    assert result.passed and result.own_tests_withdrawn == ["tests/test_own.py"]
    assert created[0].refine_calls == 3  # the whole allowance, then the rescue


async def test_a_refine_that_says_no_source_change_is_needed_ends_in_the_rescue_not_in_a_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """django-14155, 14349 and scikit-learn-11578: the model said the test was wrong and edited nothing."""
    created = _install_pipeline(
        monkeypatch, tmp_path, runner=_OwnTestsRedRunner, codegen=_OwnTestCodegenThatEditsNothing
    )
    _stub_repo_checks(monkeypatch)

    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert result.passed and result.own_tests_withdrawn == ["tests/test_own.py"]
    assert created[0].refine_calls == 1


async def test_the_coverage_gate_is_waived_on_a_rescued_run_and_the_label_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With the run's test gone, `src/x.py` looks untested. Failing over that would make the rescue empty."""
    _install_pipeline(monkeypatch, tmp_path, runner=_OwnTestsRedRunner, codegen=_OwnTestCodegen)
    _stub_repo_checks(monkeypatch, gaps=["src/x.py"])
    log: list[str] = []

    result = await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5, log=log.append)

    assert result.passed
    assert any("coverage" in ln and "waived" in ln for ln in log), log


@pytest.mark.parametrize(
    "runner",
    [_OtherTestFailsRunner, _OnlyOwnTestsRanRunner, _UnnamedFailureRunner, _ExistingTestAlsoFailsRunner],
    ids=[
        "a-test-the-run-did-not-write-fails",
        "no-existing-test-ran",
        "failures-are-not-named",
        "existing-test-fails",
    ],
)
async def test_every_other_failure_ends_exactly_as_it_does_today(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: type
) -> None:
    """Monotone: a run that fails today and does not meet every condition fails the same way, and the
    run's own test is still on disk, byte for byte, for whoever opens the parked work."""
    _install_pipeline(monkeypatch, tmp_path, runner=runner, codegen=_OwnTestCodegen)
    _stub_repo_checks(monkeypatch)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert exc.value.code == 1
    assert (tmp_path / "tests" / "test_own.py").read_text(encoding="utf-8") == _OWN_TEST_SOURCE


async def test_the_off_switch_restores_the_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_pipeline(monkeypatch, tmp_path, runner=_OwnTestsRedRunner, codegen=_OwnTestCodegen)
    _stub_repo_checks(monkeypatch)
    monkeypatch.setenv("SDLC_OWN_TEST_RESCUE", "0")

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED"):
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert (tmp_path / "tests" / "test_own.py").exists()


class _RedEvenAfterWithdrawalRunner(_OwnTestsRedRunner):
    """Only the run's test is red while it exists; once it is gone an existing test shows red."""

    async def run(self, *, path: str) -> SimpleNamespace:
        out = await super().run(path=path)
        if out.passed:
            return SimpleNamespace(
                passed=False,
                returncode=1,
                output=_EXISTING_FAIL + "Ran 4 tests in 0.01s\n\nFAILED\n",
            )
        return out


async def test_a_rescue_that_still_leaves_a_red_suite_restores_the_runs_test_and_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Withdrawing is tried once. If the suite is red anyway, the withdrawal is undone: the failed run
    parks exactly the state it would have parked without the rescue."""
    _install_pipeline(monkeypatch, tmp_path, runner=_RedEvenAfterWithdrawalRunner, codegen=_OwnTestCodegen)
    _stub_repo_checks(monkeypatch)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=5)

    assert exc.value.code == 1
    assert (tmp_path / "tests" / "test_own.py").read_text(encoding="utf-8") == _OWN_TEST_SOURCE


async def test_a_rescued_change_opens_as_a_draft_whose_body_says_it_is_unproven(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D9: a change none of the run's own tests proves is not offered for merging as it stands."""
    jira = _CommentingJira()
    _install_pipeline(monkeypatch, tmp_path, runner=_OwnTestsRedRunner, codegen=_OwnTestCodegen, jira=jira)
    _stub_repo_checks(monkeypatch)
    monkeypatch.setenv("SDLC_PROOF_BUNDLE", "0")
    _RecordingForge.opened = []
    monkeypatch.setattr("orchestrator.sdlc.forge.GhPRAdapter", _RecordingForge)

    await run_feature(
        "file://./spec.md", intent_id="intent-a", repo="https://x/widget", live=True, issue="SSPN-1"
    )

    [opened] = _RecordingForge.opened
    assert opened["draft"] is True
    assert "Unproven by its own tests" in opened["body"] and "test_own.py" in opened["body"]
    assert jira.transitions == ["In Progress"]  # a draft is not waiting on a reviewer
