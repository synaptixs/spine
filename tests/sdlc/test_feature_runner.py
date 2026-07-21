"""run_feature early-exit paths (offline — before any LLM/git/Jira call)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.intake.factory import IntakeNotConfiguredError
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


def _install_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, runner: type) -> list[_StubCodegen]:
    """Stub everything downstream of spec resolution so run_feature drives the
    real layout/scaffold/preflight + test-loop logic against a tmp worktree.
    Returns the list that captures each constructed stub codegen."""
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    monkeypatch.delenv("SDLC_REPO_URL", raising=False)
    _patch_service(monkeypatch, [_Spec("intent-a")])
    monkeypatch.setattr("orchestrator.core.llm.LiteLLMClient", lambda *a, **k: object())

    created: list[_StubCodegen] = []

    def _make_codegen(*a: Any, **k: Any) -> _StubCodegen:
        stub = _StubCodegen(*a, **k)
        created.append(stub)
        return stub

    monkeypatch.setattr("orchestrator.sdlc.codegen.LLMCodegenAdapter", _make_codegen)
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda *a, **k: None)
    monkeypatch.setattr("orchestrator.sdlc.testrunner.SubprocessTestRunner", runner)
    monkeypatch.setattr(
        "orchestrator.intake.jira.JiraAdapter",
        lambda *a, **k: SimpleNamespace(
            create_issue=lambda req: _aresult(SimpleNamespace(key="DRY-1", url=""))
        ),
    )
    monkeypatch.setattr(
        "orchestrator.sdlc.grounding.PKGCodegenGrounder",
        SimpleNamespace(from_repo=lambda path: SimpleNamespace(context_for_spec=lambda spec: "")),
    )

    async def _create(self_ws: Any, sdlc_id: str, issue_key: str) -> Path:
        return tmp_path

    monkeypatch.setattr("orchestrator.sdlc.workspace.WorkspaceManager.create", _create)
    return created


async def test_empty_refine_ends_in_graceful_failed_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When refine yields no edits and tests stay red, the loop must exhaust
    ``--max-refine`` and raise the clean ``VERDICT: FAILED`` (exit 1) — never an
    unhandled CodegenError. Locks in the integration half of the no-op-refine
    fix; ``test_refine_tolerates_a_no_op_response`` covers the adapter half."""
    created = _install_pipeline(monkeypatch, tmp_path, runner=_FailingRunner)

    with pytest.raises(FeatureRunError, match="VERDICT: FAILED") as exc:
        await run_feature("file://./spec.md", intent_id="intent-a", max_refine=3)

    assert exc.value.code == 1
    # max_refine=3 → run/refine/run/refine/run: two empty refines survived.
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
