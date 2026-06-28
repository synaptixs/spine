"""Code-generation seam.

The feature pipeline plans, implements, authors tests, and (on a failing test
run) refines code through a ``CodegenAdapter``. The default is a deterministic
stub (no LLM, no creds); ``LLMCodegenAdapter`` is the real implementation.

The LLM writes files through two forms: full ``content`` for NEW files, and
anchored ``edits`` (exact find/replace, Track 2.3) for EXISTING files. The
brownfield guard rejects full-content rewrites of pre-existing modules, so
modifying the existing codebase is only possible through edits whose anchors
are verified deterministically against the file's current content.

Implementations run inside a Temporal activity (they touch the filesystem), so
they may do real I/O; the workflow only ever sees the returned dataclasses.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from orchestrator.catalog.skills import skill_guidance, skill_phases
from orchestrator.core.llm import LLMClient, Message
from orchestrator.sdlc.layout import TargetLayout

logger = logging.getLogger("orchestrator.sdlc.codegen")

_GENERATED_MODULE = "generated.py"
_GENERATED_TEST = "test_generated.py"

# Real LLM codegen defaults — kept conservative so a chatty model can't blow up
# the worktree or the prompt context.
_DEFAULT_CODEGEN_MODEL = "claude-sonnet-4-6"


def resolve_codegen_model(override: str | None = None) -> str | None:
    """Resolve which model codegen should use, or ``None`` for the adapter default.

    Precedence: explicit ``override`` (e.g. ``--model``) > ``SDLC_CODEGEN_MODEL``
    > ``ORCHESTRATOR_INTAKE_MODEL``. Falling back to the intake model means a
    developer who set a single ``ORCHESTRATOR_INTAKE_MODEL`` drives the *whole*
    pipeline with it — codegen no longer silently jumps to a different provider
    (and its timeout) than the one the rest of the run is configured for.
    """
    return override or os.getenv("SDLC_CODEGEN_MODEL") or os.getenv("ORCHESTRATOR_INTAKE_MODEL")


_MAX_FILES = 20  # files the model may write in one pass
_MAX_FILE_BYTES = 64_000  # per generated file
_MAX_CONTEXT_BYTES = 40_000  # cap on existing source fed back to the model
_MAX_EDITS_PER_FILE = 20  # anchored find/replace edits per file
_MAX_PATCHED_FILE_BYTES = 256_000  # a patched existing file may be bigger than a generated one
# Without an explicit cap the provider default (~4k tokens) applies, and a
# large generated test file gets truncated mid-JSON — the "model output was
# not a JSON object" failures that sank author_tests in runs #16/#18.
_MAX_COMPLETION_TOKENS = 16_000


@dataclass(frozen=True)
class CodePlan:
    """The planner's output: an ordered list of steps the implementer follows."""

    steps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CodeChange:
    """Files written/changed by an implement or refine pass."""

    files: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class ImplementOutcome:
    """Result of a *governed* implement (Bet 2c-i): either a finished change, or
    a pause awaiting a human decision on an in-loop ``require_approval`` tool call.

    ``checkpoint``/``pending`` are the loop's serialized resumable state and the
    gated call; ``written``/``summary`` carry the codegen session's progress so a
    resume in a fresh activity invocation doesn't lose files written before the
    pause. All fields are JSON-able so the outcome can cross the activity →
    workflow boundary as a Temporal payload.
    """

    change: CodeChange | None = None
    needs_approval: bool = False
    checkpoint: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    written: list[str] = field(default_factory=list)
    summary: str = ""
    # The loop's governance episodes (Phase 2b) — policy denials + human rejects,
    # carried out so the post-merge hook can consolidate them into memory.
    policy_blocks: list[dict[str, str]] = field(default_factory=list)


@runtime_checkable
class CodegenAdapter(Protocol):
    """Plans, implements, tests, and refines the code for one issue."""

    async def plan(self, *, spec: dict[str, Any], path: str) -> CodePlan: ...

    async def implement(
        self,
        *,
        spec: dict[str, Any],
        path: str,
        issue_key: str,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> CodeChange: ...

    async def author_tests(self, *, spec: dict[str, Any], path: str, issue_key: str) -> CodeChange: ...

    async def refine(
        self, *, spec: dict[str, Any], path: str, issue_key: str, failures: str
    ) -> CodeChange: ...

    async def implement_governed(
        self,
        *,
        spec: dict[str, Any],
        path: str,
        issue_key: str,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> ImplementOutcome:
        """Implement, but surface a mid-loop ``require_approval`` as a pause
        (Bet 2c-i) instead of finishing. Adapters with no agentic loop just wrap
        ``implement`` and return a completed outcome."""
        ...

    async def resume_implement(
        self,
        *,
        path: str,
        checkpoint: dict[str, Any],
        decision: dict[str, Any],
        prior_written: list[str],
        prior_summary: str,
        mcp_servers: list[str] | None = None,
    ) -> ImplementOutcome:
        """Resume a paused governed implement after a human decided the gated
        call. Only reached for adapters that can pause."""
        ...


class StubCodegenAdapter:
    """Deterministic stub: one hardcoded module + test, refine rewrites in place.

    The generated test passes against the generated module on the first run, so
    the stub never drives the refinement loop past iteration 1 — but ``refine``
    is implemented so an injected failing test runner can exercise the loop.
    """

    async def plan(self, *, spec: dict[str, Any], path: str) -> CodePlan:
        _ = (spec, path)
        return CodePlan(steps=["implement the single hardcoded module", "author one test"])

    async def implement(
        self,
        *,
        spec: dict[str, Any],
        path: str,
        issue_key: str,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> CodeChange:
        _ = (spec, skills, mcp_servers)
        module = Path(path) / _GENERATED_MODULE
        module.write_text(
            f'"""Generated stub for {issue_key} (Block C skeleton)."""\n\n\n'
            f"def feature() -> str:\n"
            f'    return "{issue_key}"\n',
            encoding="utf-8",
        )
        return CodeChange(files=[str(module)], summary=f"wrote {_GENERATED_MODULE}")

    async def author_tests(self, *, spec: dict[str, Any], path: str, issue_key: str) -> CodeChange:
        _ = spec
        test = Path(path) / _GENERATED_TEST
        test.write_text(
            "from generated import feature\n\n\n"
            "def test_feature() -> None:\n"
            f'    assert feature() == "{issue_key}"\n',
            encoding="utf-8",
        )
        return CodeChange(files=[str(test)], summary=f"wrote {_GENERATED_TEST}")

    async def refine(self, *, spec: dict[str, Any], path: str, issue_key: str, failures: str) -> CodeChange:
        """Re-run the implementer given the failing-test output.

        The stub just rewrites the same module (it was already correct); a real
        adapter feeds ``failures`` back to the LLM for a corrected patch.
        """
        _ = failures
        return await self.implement(spec=spec, path=path, issue_key=issue_key)

    async def implement_governed(
        self,
        *,
        spec: dict[str, Any],
        path: str,
        issue_key: str,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> ImplementOutcome:
        # The stub has no agentic loop, so it never pauses — always completes.
        change = await self.implement(
            spec=spec, path=path, issue_key=issue_key, skills=skills, mcp_servers=mcp_servers
        )
        return ImplementOutcome(change=change)

    async def resume_implement(
        self,
        *,
        path: str,
        checkpoint: dict[str, Any],
        decision: dict[str, Any],
        prior_written: list[str],
        prior_summary: str,
        mcp_servers: list[str] | None = None,
    ) -> ImplementOutcome:
        # Unreachable: the stub never returns ``needs_approval`` to resume from.
        raise NotImplementedError("StubCodegenAdapter does not pause for approval")


class CodegenError(RuntimeError):
    """The model returned something we can't turn into files.

    ``failed_edit_paths`` names existing files whose anchored edits failed to
    apply — the repair retry shows the model those files' exact current content.
    ``missing_edit_paths`` names files the model tried to change via the ``edits``
    form but that don't exist yet — the repair tells it to create them with the
    ``content`` form instead (a common greenfield slip). Either list being
    non-empty makes the failure *recoverable* (one repair retry).
    """

    def __init__(
        self,
        message: str,
        *,
        failed_edit_paths: list[str] | None = None,
        missing_edit_paths: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.failed_edit_paths = list(failed_edit_paths or [])
        self.missing_edit_paths = list(missing_edit_paths or [])


@runtime_checkable
class CodegenGrounder(Protocol):
    """Supplies existing-codebase context for a spec (see ``grounding.py``)."""

    def context_for_spec(self, spec: dict[str, Any]) -> str: ...


# The two file forms every codegen call may emit. New files carry full
# content; EXISTING files are changed with anchored find/replace edits —
# whole-file rewrites of pre-existing modules are rejected by the guard.
# NOTE: keep this example strictly valid JSON — an illustrative // comment
# here teaches the model to emit comment-laden JSON that fails to parse.
_FILE_FORMS = (
    '{"files": [<entries>], "summary": "<one line>"}\n\n'
    "Each entry in files is EXACTLY ONE of:\n"
    '  for a NEW file:       {"path": "<relative path>", "content": "<full content>"}\n'
    '  for an EXISTING file: {"path": "<relative path>", "edits": [{"find": '
    '"<exact snippet copied verbatim from the current file>", "replace": '
    '"<replacement>"}]}\n\n'
    "Edit rules: each `find` must be copied EXACTLY (whitespace included) from "
    "the file's current content and must occur exactly once in it — include "
    "enough surrounding lines to make it unique. Never use `content` for a "
    "file that already exists, and never use `edits` for a new file. Output "
    "strict JSON: no comments, no trailing commas.\n"
)

_IMPLEMENT_SYSTEM = (
    "You are a senior engineer. Implement the feature described by the SPEC as "
    "runnable Python inside the given git worktree.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: paths are relative to the worktree root — no leading slash, no '..'. "
    "Write source files only (NO test files here). Keep each module importable "
    "by a sibling pytest test via a top-level import (e.g. `from feature import "
    "run`). Prefer the standard library; only add a dependency the SPEC names. "
    "Never name a new top-level module after a Python standard-library module "
    "(statistics, json, types, ...) — it shadows the real one. When the SPEC "
    "names existing files, change THOSE files (with `edits`); do not create a "
    "parallel module instead. Every new file must be complete and "
    "syntactically valid."
)

_AGENTIC_MAX_STEPS = 16
# How many learned facts to prime the agentic task with (Phase 1b).
_MEMORY_PRIMING_TOP_N = 5

# Skill-conditioned prompt fragments (Phase 5c): a capability plan's selected
# skills append guidance to the agentic implement system prompt, keyed by catalog
# capability id. The guidance now lives in first-class Skill artifacts
# (``catalog.skills``) — this is the same id→fragment mapping, resolved from there.
_SKILL_PROMPTS = skill_guidance()
# Which phase(s) each skill conditions (persona-skill measurement P0). A skill's
# guidance is applied to a phase only if that phase is in its declared set, so a
# test-oriented skill reaches author_tests/refine instead of leaking into implement.
_SKILL_PHASES = skill_phases()

_AGENTIC_IMPLEMENT_SYSTEM = (
    "You are a senior engineer implementing the feature in the given git "
    "worktree. Work iteratively using the tools:\n"
    "- Use pkg_relevant_symbols / pkg_api_surface / pkg_callers_of and read_file "
    "to understand existing code before changing it — reuse what exists.\n"
    "- write_files to create new files (content) or change existing ones "
    "(anchored edits — never resend an existing file's full content).\n"
    "- run_tests to check your work; fix and re-write on failures.\n"
    "- submit_changes with a one-line summary when the implementation is "
    "complete. This ends the task.\n"
    "Write source files only (no test files). Paths are relative to the root — "
    "no leading slash, no '..'. Never name a new top-level module after a Python "
    "standard-library module. Keep modules importable by a sibling pytest test."
)

_TESTS_SYSTEM = (
    "You write pytest tests for an already-implemented feature. You are given "
    "the SPEC and the CURRENT SOURCE FILES.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: write test files only (filenames start with `test_`). A NEW test "
    "file uses `content`; adding tests to an EXISTING test file (e.g. when "
    "the SPEC names one) uses `edits` — typically anchoring on the file's "
    "final lines and appending the new test functions. Import the source by "
    "its top-level module name. Each acceptance criterion should map to at "
    "least one assertion. Tests must pass against the given source."
)

_REFINE_SYSTEM = (
    "You are fixing failing tests. You are given the SPEC, the CURRENT FILES, "
    "and the pytest FAILURE OUTPUT.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: files you created earlier this session may be resent in full via "
    "`content`; any other existing file must be changed via `edits`. Make the "
    "smallest change that turns the tests green. Same path rules as before — "
    "relative, no '..'."
)

# Java (Maven/JUnit) variants — selected when the layout's language is "java".
_IMPLEMENT_SYSTEM_JAVA = (
    "You are a senior engineer. Implement the feature described by the SPEC as "
    "runnable Java inside the given Maven project (the worktree).\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: paths are relative to the worktree root — no leading slash, no '..'. "
    "Write source files only (NO test files here). One public class per file; the "
    "file path matches the package and the class name; start every file with the "
    "`package` declaration shown in the layout. Use only the standard library or "
    "deps already in `pom.xml` — to add one, edit `pom.xml`. Every file must be "
    "complete and compilable."
)

_TESTS_SYSTEM_JAVA = (
    "You write JUnit 5 tests for an already-implemented feature. You are given the "
    "SPEC and the CURRENT SOURCE FILES.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: write test files only, under the tests dir shown in the layout, named "
    "`<ClassName>Test.java` in the same package as the code under test. Use "
    "`org.junit.jupiter.api.Test` + `org.junit.jupiter.api.Assertions`. Each "
    "acceptance criterion maps to at least one assertion. Tests must pass against "
    "the given source."
)

_REFINE_SYSTEM_JAVA = (
    "You are fixing a failing Maven build / JUnit tests. You are given the SPEC, "
    "the CURRENT FILES, and the Maven FAILURE OUTPUT.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: files you created earlier this session may be resent in full via "
    "`content`; any other existing file must be changed via `edits` (including "
    "`pom.xml` to add a dependency). Make the smallest change that turns the build "
    "green. Same path rules — relative, no '..'."
)

# TypeScript (Vitest) variants — selected when the layout's language is "typescript".
_IMPLEMENT_SYSTEM_TS = (
    "You are a senior engineer. Implement the feature described by the SPEC as "
    "runnable TypeScript inside the given Node project (the worktree).\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: paths are relative to the worktree root — no leading slash, no '..'. "
    "Write source files only (NO test files here). Use strict, fully-typed "
    "TypeScript (no implicit any) with ES module `import`/`export`; when importing "
    "a sibling module use a relative path with a `.js` extension (NodeNext), e.g. "
    '`import { run } from "./feature.js"`. Export the feature\'s public API so a '
    "sibling Vitest test can import it. Use only the standard library or deps "
    "already in `package.json` — to add one, edit `package.json`. Every file must "
    "be complete and compile under `strict`."
)

_TESTS_SYSTEM_TS = (
    "You write Vitest tests for an already-implemented feature. You are given the "
    "SPEC and the CURRENT SOURCE FILES.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: write test files only, co-located beside the source as `<name>.test.ts`. "
    'Import Vitest helpers (`import { describe, it, expect } from "vitest";`) and '
    "import the source under test by a relative path with a `.js` extension. Each "
    "acceptance criterion maps to at least one assertion. Tests must pass against "
    "the given source."
)

_REFINE_SYSTEM_TS = (
    "You are fixing a failing Vitest run / TypeScript compile error. You are given "
    "the SPEC, the CURRENT FILES, and the FAILURE OUTPUT.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: files you created earlier this session may be resent in full via "
    "`content`; any other existing file must be changed via `edits` (including "
    "`package.json` to add a dependency). Make the smallest change that turns the "
    "run green. Same path rules — relative, no '..'."
)

# C# (.NET/xUnit) variants — selected when the layout's language is "csharp".
_IMPLEMENT_SYSTEM_CSHARP = (
    "You are a senior engineer. Implement the feature described by the SPEC as "
    "runnable C# inside the given .NET project (the worktree).\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: paths are relative to the worktree root — no leading slash, no '..'. "
    "Write source files only (NO test files here). Put each public type in the "
    "source project shown in the layout, one public type per file; the file name "
    "matches the type name; declare the namespace shown in the layout (a "
    "file-scoped `namespace <Name>;` is fine). Target the framework shown in the "
    "layout, with nullable reference types enabled. Use only the BCL or packages "
    "already referenced in "
    "the source `.csproj` — to add one, edit the `.csproj` with a "
    "`<PackageReference>`. Every file must be complete and compilable."
)

_TESTS_SYSTEM_CSHARP = (
    "You write xUnit tests for an already-implemented feature. You are given the "
    "SPEC and the CURRENT SOURCE FILES.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: write test files only, under the test project dir shown in the layout, "
    "named `<TypeName>Tests.cs`. Use xUnit (`using Xunit;` with `[Fact]` / "
    "`[Theory]` methods and `Assert`). The test project already references the "
    "source project — use the source namespace shown in the layout. Each "
    "acceptance criterion maps to at least one assertion. Tests must pass against "
    "the given source."
)

_REFINE_SYSTEM_CSHARP = (
    "You are fixing a failing `dotnet test` run (a compile or xUnit failure). You "
    "are given the SPEC, the CURRENT FILES, and the FAILURE OUTPUT.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: files you created earlier this session may be resent in full via "
    "`content`; any other existing file must be changed via `edits` (including a "
    "`.csproj` to add a `<PackageReference>`). Make the smallest change that turns "
    "the build green. Same path rules — relative, no '..'."
)

# C (CMake/ctest) variants — selected when the layout's language is "c".
_IMPLEMENT_SYSTEM_C = (
    "You are a senior engineer. Implement the feature described by the SPEC as "
    "runnable C inside the given CMake project (the worktree).\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: paths are relative to the worktree root — no leading slash, no '..'. "
    "Write source files only (NO test files here). Put implementation `.c` files "
    "under the source dir shown in the layout and DECLARE the public functions in a "
    "header (`.h`) there, guarded with `#ifndef`/`#define`. Write portable C11 using "
    "only the standard library. Follow the build-file guidance in the LAYOUT block "
    "(CMake globs new files; Meson needs them registered in `meson.build`). Every "
    "file must be complete and compile with no warnings-as-errors."
)

_TESTS_SYSTEM_C = (
    "You write C unit tests for an already-implemented feature. You are given the "
    "SPEC and the CURRENT SOURCE FILES.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: write test files only, under the tests dir shown in the layout, named "
    "`test_<name>.c`. Each test file is a standalone program with an `int main(void)` "
    "that exercises the feature and returns 0 on success, non-zero on failure (use "
    "`assert.h` or explicit `if (...) return 1;` checks). `#include` the public "
    "header from the source dir. Register the test as the LAYOUT describes (CMake "
    "auto-discovers `tests/*.c`; Meson needs an `executable()`+`test()` in `meson.build`). "
    "Each acceptance criterion maps to at least "
    "one assertion; tests must pass against the given source."
)

_REFINE_SYSTEM_C = (
    "You are fixing a failing build / test run (a configure error, a compiler "
    "error, or a failing assertion). You are given the SPEC, the CURRENT FILES, and "
    "the FAILURE OUTPUT.\n\n"
    "Output ONE JSON object, no prose, no code fences:\n"
    f"{_FILE_FORMS}\n"
    "Rules: files you created earlier this session may be resent in full via "
    "`content`; any other existing file must be changed via `edits` (including "
    "the build file — `CMakeLists.txt` or `meson.build`). Make the smallest change that turns the "
    "build + tests green. Same path rules — relative, no '..'."
)

# Phase system prompts keyed by language (default: Python). Adding a language is a
# new column here, not another boolean branch at each call site.
_IMPLEMENT_SYSTEMS = {
    "python": _IMPLEMENT_SYSTEM,
    "java": _IMPLEMENT_SYSTEM_JAVA,
    "typescript": _IMPLEMENT_SYSTEM_TS,
    "csharp": _IMPLEMENT_SYSTEM_CSHARP,
    "c": _IMPLEMENT_SYSTEM_C,
}
_TESTS_SYSTEMS = {
    "python": _TESTS_SYSTEM,
    "java": _TESTS_SYSTEM_JAVA,
    "typescript": _TESTS_SYSTEM_TS,
    "csharp": _TESTS_SYSTEM_CSHARP,
    "c": _TESTS_SYSTEM_C,
}
_REFINE_SYSTEMS = {
    "python": _REFINE_SYSTEM,
    "java": _REFINE_SYSTEM_JAVA,
    "typescript": _REFINE_SYSTEM_TS,
    "csharp": _REFINE_SYSTEM_CSHARP,
    "c": _REFINE_SYSTEM_C,
}


class LLMCodegenAdapter:
    """Real codegen: the LLM writes runnable source + tests into the worktree.

    Drops into the ``CodegenAdapter`` seam in place of ``StubCodegenAdapter``.
    Each method is one structured-JSON LLM call (the same shape Block B's
    ``SpecWriter`` uses); the returned files are written under the worktree with
    path confinement so the model can't escape it. The generated tests are run
    for real by ``SubprocessTestRunner``, and ``refine`` closes the loop by
    feeding the pytest failure back to the model.

    Runs inside a Temporal activity (it does network + filesystem I/O), never in
    workflow code.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        model: str = _DEFAULT_CODEGEN_MODEL,
        grounder: CodegenGrounder | None = None,
        grounder_factory: Callable[[Path], CodegenGrounder] | None = None,
        layout: TargetLayout | None = None,
        agentic: bool = False,
        skills: list[str] | None = None,
        mcp_registry: Any = None,
        mcp_configs: list[Any] | None = None,
        policy: Any = None,
        persona: Any = None,
        skill_scores: Mapping[str, float] | None = None,
        memory_factory: Any = None,
        memory_repo_key: str | None = None,
        memory_tenant_id: str = "default",
    ) -> None:
        self._llm = llm
        self._model = model
        # When set, ``implement`` runs the agentic tool-use loop (Phase 5) instead
        # of the single-shot path. Off by default — single-shot stays the default
        # until the loop proves out live.
        self._agentic = agentic
        # Bet 2c — a Policy gates each in-loop tool call. ``require_approval``
        # rules pause the loop (governed path) or, for non-workflow callers,
        # fall back to 2a deny-with-reason. ``None`` = no governance (no pauses).
        self._policy = policy
        # Phase 5c — the capability plan conditions the loop: ``skills`` add
        # prompt fragments / enable tool groups; an MCP registry exposes the
        # plan's selected servers as governed in-loop tools.
        self._skills = list(skills or [])
        # Phase 2b — when a persona drives the run, the agentic system prompt leads
        # with the persona's role and its skill guidance is resolved through the
        # vetting gate (persona-scoped ∩ plan-selected ∩ approved). ``None`` keeps
        # the prior behavior exactly. ``skill_scores`` gates imported skills.
        self._persona = persona
        self._skill_scores = skill_scores
        self._mcp_registry = mcp_registry
        self._mcp_configs = list(mcp_configs or [])
        # A single explicit grounder (CLI, one worktree) takes precedence; a
        # factory builds one per worktree root, for the worker's fan-out where
        # one shared adapter serves many target clones.
        self._grounder = grounder
        self._grounder_factory = grounder_factory
        self._grounders: dict[Path, CodegenGrounder | None] = {}
        # The target layout pins where generated files go (package + dirs). When
        # set, every phase prompt leads with an authoritative path block so the
        # model stops inventing paths in greenfield repos. None = legacy behavior.
        self._layout = layout
        # Files written per worktree root this session — fed back to the model
        # in author_tests/refine instead of dumping the whole tree.
        self._written: dict[Path, list[Path]] = {}
        # Derived house-style digest per worktree root (G8), computed once.
        self._conventions: dict[Path, str] = {}
        # Phase 1b (cross-run semantic memory) — when a DB session factory + a
        # repo key are supplied AND ORCHESTRATOR_SEMANTIC_MEMORY is on, the
        # agentic loop gets a recall_memory tool and its task is primed with the
        # top learned facts for this repo. All three off → prior behavior exactly.
        self._memory_factory = memory_factory
        self._memory_repo_key = memory_repo_key
        self._memory_tenant_id = memory_tenant_id

    def _memory_enabled(self) -> bool:
        """Cross-run memory is active only with both deps wired and the flag set."""
        if self._memory_factory is None or not self._memory_repo_key:
            return False
        return (os.getenv("ORCHESTRATOR_SEMANTIC_MEMORY") or "").strip().lower() in {"1", "true", "yes", "on"}

    async def _memory_priming(self, query: str) -> str:
        """Top learned facts for this repo as an advisory prompt block (or '').

        Passive priming (docs/specs/cross-run-semantic-memory.md): relevance-
        ranked, falling back to confidence when the query has no usable terms.
        Read-only — does not record hits (that's reserved for active recall)."""
        if not self._memory_enabled():
            return ""
        from orchestrator.registry.repositories import MemoryRepo

        try:
            async with self._memory_factory() as session:
                rows = await MemoryRepo(session).search(
                    query=query,
                    repo_key=str(self._memory_repo_key),
                    tenant_id=self._memory_tenant_id,
                    limit=_MEMORY_PRIMING_TOP_N,
                )
        except Exception as exc:  # noqa: BLE001 — priming is advisory, never fatal
            logger.warning("sdlc.codegen.memory_priming_unavailable", extra={"error": str(exc)[:200]})
            return ""
        if not rows:
            return ""
        lines = "\n".join(f"- [{r.kind}] {r.statement}" for r in rows)
        return (
            "LEARNED FROM PAST RUNS ON THIS PROJECT (cross-run memory; advisory, "
            "verify against the code):\n" + lines + "\n\n"
        )

    def _convention_block(self, root: Path) -> str:
        """The repo's observed conventions as a prompt block (cached, or '')."""
        key = root.resolve()
        if key not in self._conventions:
            from orchestrator.sdlc.conventions import extract_conventions

            block = extract_conventions(root).prompt_block()
            self._conventions[key] = f"\n\n{block}" if block else ""
        return self._conventions[key]

    def _resolve_grounder(self, root: Path) -> CodegenGrounder | None:
        """The grounder for ``root``: explicit one, else factory-built (cached).

        Grounding is an enhancement — a factory that fails to build (e.g. an
        extraction error on an odd repo) must never break codegen, so the
        failure is logged and cached as "no grounding" for that root.
        """
        if self._grounder is not None:
            return self._grounder
        if self._grounder_factory is None:
            return None
        key = root.resolve()
        if key not in self._grounders:
            try:
                self._grounders[key] = self._grounder_factory(root)
            except Exception as exc:  # noqa: BLE001 — grounding must not break codegen
                logger.warning("sdlc.codegen.grounder_build_failed", extra={"error": str(exc)[:200]})
                self._grounders[key] = None
        return self._grounders[key]

    def _layout_block(self) -> str:
        """Authoritative path guidance from the target layout (or '' if unset).

        Leads every phase prompt so it overrides the base prompt's "top-level
        import" default and the model's greenfield path-invention — the fix for
        leaked paths like ``src/orchestrator/pkg/...`` in unrelated repos.
        """
        layout = self._layout
        if layout is None:
            return ""
        if layout.language == "java":
            return (
                "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
                f"- Java package is `{layout.package_name}`. Put each public class at "
                f"`{layout.source_dir}/<ClassName>.java`, one public class per file, starting "
                f"with `package {layout.package_name};`.\n"
                f"- Put JUnit 5 tests at `{layout.tests_dir}/<ClassName>Test.java` in the same package.\n"
                "- Declare any new dependency in `pom.xml` (edit it); don't invent unrelated paths.\n\n"
            )
        if layout.language == "typescript":
            return (
                "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
                f"- Put new modules at `{layout.source_dir}/<name>.ts`. Use ES module "
                "`import`/`export`; import a sibling module by relative path with a `.js` "
                'extension (NodeNext), e.g. `import { x } from "./<name>.js"`.\n'
                f"- Put Vitest tests co-located beside the code as `{layout.tests_dir}/<name>.test.ts`.\n"
                "- Declare any new dependency in `package.json` (edit it); don't invent unrelated paths.\n\n"
            )
        if layout.language == "csharp":
            tfm = layout.target_framework or "net8.0"
            return (
                "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
                f"- C# namespace is `{layout.package_name}`. Put each public type at "
                f"`{layout.source_dir}/<TypeName>.cs`, one public type per file, declaring "
                f"`namespace {layout.package_name};` (target {tfm}, nullable enabled).\n"
                f"- Put xUnit tests at `{layout.tests_dir}/<TypeName>Tests.cs` (the test "
                "project already references the source project).\n"
                f"- Declare any new dependency as a `<PackageReference>` in the source "
                "`.csproj` (edit it); don't invent unrelated paths.\n\n"
            )
        if layout.language == "c":
            if layout.build_tool == "meson":
                build_line = (
                    "- This project uses **Meson** (`meson.build`), which does NOT glob: "
                    "register every new file — add new `.c` sources to the library/target "
                    "source list, and add an `executable(...)` + `test(...)` for each new "
                    f"`{layout.tests_dir}/test_<name>.c`. Edit `meson.build` to do so. "
                    "Prefer extending existing files (no `meson.build` change needed) when you can."
                )
            else:
                build_line = (
                    "- New `src/*.c` and `tests/*.c` are auto-discovered by CMake's glob; "
                    "edit `CMakeLists.txt` only to add an external dependency."
                )
            return (
                "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
                f"- Put implementation at `{layout.source_dir}/<name>.c` and DECLARE its "
                f"public functions in a header `{layout.source_dir}/<name>.h` (with an "
                "`#ifndef`/`#define` guard); C11, standard library only.\n"
                f"- Put tests at `{layout.tests_dir}/test_<name>.c` — each a standalone "
                "`int main(void)` returning non-zero on failure, `#include`-ing the "
                f"header from `{layout.source_dir}/`.\n"
                f"{build_line} Don't invent unrelated paths.\n\n"
            )
        return (
            "PROJECT LAYOUT (authoritative — overrides any default path guidance):\n"
            f"- Source package is `{layout.package_name}` under `{layout.source_dir}/`. "
            f"Put new modules at `{layout.source_dir}/<module>.py`.\n"
            f"- Import source as `from {layout.package_name}.<module> import ...` "
            "(the test runner puts the source root on the path).\n"
            f"- Put tests under `{layout.tests_dir}/` as `{layout.tests_dir}/test_<name>.py`.\n"
            f"- Do NOT create files outside `{layout.source_dir}/` and `{layout.tests_dir}/`, "
            "and do NOT invent unrelated top-level paths.\n\n"
        )

    def _language(self) -> str:
        return self._layout.language if self._layout is not None else "python"

    def _impl_system(self) -> str:
        return _IMPLEMENT_SYSTEMS.get(self._language(), _IMPLEMENT_SYSTEM)

    def _tests_system(self) -> str:
        return _TESTS_SYSTEMS.get(self._language(), _TESTS_SYSTEM)

    def _refine_system(self) -> str:
        return _REFINE_SYSTEMS.get(self._language(), _REFINE_SYSTEM)

    def _grounding(self, spec: dict[str, Any], root: Path) -> str:
        """The PKG context block (with trailing separator), or ''."""
        grounder = self._resolve_grounder(root)
        if grounder is None:
            return ""
        context = grounder.context_for_spec(spec)
        if not context:
            return ""
        # The base system prompt assumes Block C's fresh, empty worktree. When
        # grounded we're extending an existing repo — without this correction a
        # capable model defensively rewrites the modules it was shown.
        brownfield = (
            "BROWNFIELD RULES: the worktree already contains the full existing "
            "repository, including every module shown in the context above. "
            "Create new file(s) inside the existing package structure; to "
            "change an existing module, use the `edits` form — NEVER resend an "
            "existing file's full content (it will be rejected), never copy or "
            "shadow existing modules, and do not create __init__.py files that "
            "already exist. Prefer importing what exists over editing it; edit "
            "only what the feature genuinely requires. CI enforces ruff (no "
            "unused imports, sorted imports) and mypy --strict: fully "
            "type-annotate every function, including test functions (-> None)."
        )
        return f"{context}\n\n{brownfield}\n\n"

    async def plan(self, *, spec: dict[str, Any], path: str) -> CodePlan:
        """Derive steps from the spec's acceptance criteria (no LLM round-trip).

        Planning is low-value next to the actual codegen, so we keep it cheap
        and deterministic: each acceptance criterion becomes a step, with a
        sensible default when the spec has none.
        """
        _ = path
        criteria = _str_list(spec.get("acceptance_criteria"))
        steps = [f"satisfy: {c}" for c in criteria] or [
            "implement the feature from the spec summary",
            "author tests for the acceptance criteria",
        ]
        return CodePlan(steps=steps)

    async def implement(
        self,
        *,
        spec: dict[str, Any],
        path: str,
        issue_key: str,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> CodeChange:
        root = Path(path)
        task = (
            f"{self._layout_block()}{self._grounding(spec, root)}Issue: {issue_key}\n\n"
            f"SPEC:\n{_spec_text(spec)}"
            f"{_named_existing_files(spec, root)}{self._convention_block(root)}"
        )
        if self._agentic:
            # Per-call plan values (from the run's capability plan) take
            # precedence over constructor defaults.
            return await self._agentic_implement(
                task,
                root,
                skills=skills if skills is not None else self._skills,
                mcp_servers=mcp_servers if mcp_servers is not None else None,
            )
        # Single-shot path: condition the system prompt on the persona + selected
        # skills too (no persona + no skills → the historical prompt, unchanged).
        resolved = skills if skills is not None else self._skills
        system = self._condition_system(self._impl_system(), resolved, phase="implement")
        return await self._generate(system, task, root)

    async def _agentic_tools(self, root: Path, mcp_servers: list[str] | None, session: Any) -> list[Any]:
        """Build the loop's toolset bound to ``root`` + ``session`` (read-only +
        codegen + the plan's governed MCP tools). Shared by run and resume so a
        resumed loop sees exactly the same tools."""
        from orchestrator.agentic import build_readonly_tools
        from orchestrator.agentic.codegen_tools import build_codegen_tools
        from orchestrator.sdlc.testrunner import SubprocessTestRunner

        grounded = self._resolve_grounder(root) is not None
        tools = build_readonly_tools(root) + build_codegen_tools(
            root, grounded=grounded, session=session, runner=SubprocessTestRunner()
        )
        # Phase 1b — cross-run semantic memory: let the agent recall facts learned
        # on past runs of this repo (gated; no-op when memory deps/flag are off).
        if self._memory_enabled():
            from orchestrator.agentic.memory_tools import build_memory_tools

            tools += build_memory_tools(
                self._memory_factory,
                repo_key=str(self._memory_repo_key),
                tenant_id=self._memory_tenant_id,
            )
        # Phase 5c — expose the capability plan's selected MCP servers as
        # governed in-loop tools (best-effort: a down/unconfigured registry just
        # means no MCP tools, never a failed implement).
        registry, configs = self._resolve_mcp(mcp_servers)
        if registry is not None:
            from orchestrator.agentic.mcp_tools import build_mcp_loop_tools

            try:
                tools += await build_mcp_loop_tools(registry, configs)
            except Exception as exc:  # noqa: BLE001 — MCP tools are additive, never required
                logger.warning("sdlc.codegen.mcp_tools_unavailable", extra={"error": str(exc)[:200]})
        return tools

    def _build_loop(self, tools: list[Any]) -> Any:
        from orchestrator.agentic import AgentLoop

        return AgentLoop(
            self._llm,
            model=self._model,
            tools=tools,
            max_steps=_AGENTIC_MAX_STEPS,
            max_tokens=_MAX_COMPLETION_TOKENS,
            policy=self._policy,
        )

    async def _agentic_implement(
        self,
        task: str,
        root: Path,
        *,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> CodeChange:
        """Loop-driven implement for non-workflow callers: the agent explores,
        writes, tests, submits.

        Tools route through the same guards as the single-shot path; the terminal
        ``submit_changes`` ends the loop and the accumulated files become the
        CodeChange. There is no durable human-pause harness here, so a
        ``require_approval`` decision falls back to 2a semantics: it is auto-denied
        (the model sees the refusal and adapts) so this entry always finishes.
        """
        from orchestrator.agentic import HumanDecision
        from orchestrator.agentic.codegen_tools import CodegenSession

        session = CodegenSession(tracker=self._written)
        tools = await self._agentic_tools(root, mcp_servers, session)
        loop = self._build_loop(tools)
        task = (await self._memory_priming(task)) + task
        result = await loop.run(self._agentic_system(skills or []), task)
        while result.stopped_reason == "needs_approval":
            logger.info(
                "sdlc.codegen.approval_autodenied",
                extra={"tool": result.pending.tool if result.pending else "?"},
            )
            result = await loop.resume(
                result.checkpoint,
                HumanDecision(action="reject", rationale="no in-loop approver (non-workflow run)"),
            )
        if not session.written:
            raise CodegenError(f"agentic implement wrote no files (stopped: {result.stopped_reason})")
        return CodeChange(files=session.written, summary=session.summary or "agentic implement")

    async def implement_governed(
        self,
        *,
        spec: dict[str, Any],
        path: str,
        issue_key: str,
        skills: list[str] | None = None,
        mcp_servers: list[str] | None = None,
    ) -> ImplementOutcome:
        """The workflow entry: run the loop and surface a ``require_approval``
        pause as an ``ImplementOutcome`` instead of finishing or auto-denying."""
        if not self._agentic:
            change = await self.implement(
                spec=spec, path=path, issue_key=issue_key, skills=skills, mcp_servers=mcp_servers
            )
            return ImplementOutcome(change=change)
        from orchestrator.agentic.codegen_tools import CodegenSession

        root = Path(path)
        task = (
            f"{self._layout_block()}{self._grounding(spec, root)}Issue: {issue_key}\n\n"
            f"SPEC:\n{_spec_text(spec)}"
            f"{_named_existing_files(spec, root)}{self._convention_block(root)}"
        )
        eff_skills = skills if skills is not None else self._skills
        session = CodegenSession(tracker=self._written)
        tools = await self._agentic_tools(root, mcp_servers, session)
        loop = self._build_loop(tools)
        task = (await self._memory_priming(task)) + task
        result = await loop.run(self._agentic_system(eff_skills or []), task)
        return self._outcome(result, session)

    async def resume_implement(
        self,
        *,
        path: str,
        checkpoint: dict[str, Any],
        decision: dict[str, Any],
        prior_written: list[str],
        prior_summary: str,
        mcp_servers: list[str] | None = None,
    ) -> ImplementOutcome:
        """Resume a paused governed implement with the human's decision folded in.

        Seeds a fresh session with the files written before the pause (they're
        already on disk in the worktree, but the tracker is per-invocation) so the
        final CodeChange reflects the whole run."""
        from orchestrator.agentic import HumanDecision, LoopCheckpoint
        from orchestrator.agentic.codegen_tools import CodegenSession

        root = Path(path)
        session = CodegenSession(tracker=self._written, written=list(prior_written), summary=prior_summary)
        tools = await self._agentic_tools(root, mcp_servers, session)
        loop = self._build_loop(tools)
        dec = HumanDecision(
            action=str(decision.get("action", "reject")),
            rationale=decision.get("rationale"),
            modified_input=decision.get("modified_input"),
        )
        result = await loop.resume(LoopCheckpoint.from_dict(checkpoint), dec)
        return self._outcome(result, session)

    def _outcome(self, result: Any, session: Any) -> ImplementOutcome:
        """Map a loop result + session into an ImplementOutcome (pause or done)."""
        if result.stopped_reason == "needs_approval":
            return ImplementOutcome(
                needs_approval=True,
                checkpoint=result.checkpoint.to_dict(),
                pending=result.pending.to_dict(),
                written=list(session.written),
                summary=session.summary,
            )
        if not session.written:
            raise CodegenError(f"agentic implement wrote no files (stopped: {result.stopped_reason})")
        return ImplementOutcome(
            change=CodeChange(files=session.written, summary=session.summary or "agentic implement"),
            policy_blocks=[dict(b) for b in getattr(result, "policy_blocks", [])],
        )

    def _resolve_mcp(self, mcp_servers: list[str] | None) -> tuple[Any, list[Any]]:
        """The MCP registry+configs for this run: constructor-provided, else
        built from the plan's server names (best-effort; ``(None, [])`` if none)."""
        if self._mcp_registry is not None:
            return self._mcp_registry, self._mcp_configs
        if not mcp_servers:
            return None, []
        try:
            from orchestrator.mcp.config import load_mcp_configs
            from orchestrator.mcp.registry import MCPRegistry

            wanted = set(mcp_servers)
            configs = [c for c in load_mcp_configs() if c.name in wanted]
            return (MCPRegistry(configs), configs) if configs else (None, [])
        except Exception as exc:  # noqa: BLE001 — MCP is an optional extra; degrade quietly
            logger.warning("sdlc.codegen.mcp_registry_unavailable", extra={"error": str(exc)[:200]})
            return None, []

    def _condition_system(self, base: str, skills: list[str], *, phase: str) -> str:
        """Apply persona role + skill-conditioned guidance to a ``phase``'s base prompt.

        Skills are first narrowed to those that declare ``phase`` (``Skill.phases``),
        so a test-oriented skill conditions author_tests/refine and never leaks into
        implement (the structural blindness that sank the first A/B).

        With a persona, the prompt leads with its ``role`` and the guidance is the
        persona-endorsed ∩ plan-selected ∩ vetting-approved set (``personas.binding``).
        Without one, it's the prior behavior: raw plan skills → ``_SKILL_PROMPTS``.
        No persona + no phase-relevant skills → ``base`` unchanged (the historical default).
        """
        scoped = [s for s in skills if phase in _SKILL_PHASES.get(s, ("implement",))]
        if self._persona is not None:
            from orchestrator.personas.binding import persona_guidance_for_selection

            role = self._persona.spec.role
            base = f"{role}\n\n{base}" if role else base
            fragments = persona_guidance_for_selection(self._persona, scoped, scores=self._skill_scores)
        else:
            fragments = [_SKILL_PROMPTS[s] for s in scoped if s in _SKILL_PROMPTS]
        if not fragments:
            return base
        return base + "\n\nFor this project:\n" + "\n".join(f"- {f}" for f in fragments)

    def _agentic_system(self, skills: list[str]) -> str:
        """The agentic implement system prompt, persona/skill-conditioned (implement phase)."""
        return self._condition_system(_AGENTIC_IMPLEMENT_SYSTEM, skills, phase="implement")

    async def author_tests(self, *, spec: dict[str, Any], path: str, issue_key: str) -> CodeChange:
        root = Path(path)
        return await self._generate(
            self._condition_system(self._tests_system(), self._skills, phase="author_tests"),
            f"{self._layout_block()}{self._grounding(spec, root)}Issue: {issue_key}\n\n"
            f"SPEC:\n{_spec_text(spec)}\n\n"
            f"CURRENT SOURCE FILES:\n{self._session_files(root, include_tests=False)}"
            f"{self._convention_block(root)}",
            root,
        )

    async def refine(self, *, spec: dict[str, Any], path: str, issue_key: str, failures: str) -> CodeChange:
        root = Path(path)
        return await self._generate(
            self._condition_system(self._refine_system(), self._skills, phase="refine"),
            f"{self._layout_block()}{self._grounding(spec, root)}Issue: {issue_key}\n\n"
            f"SPEC:\n{_spec_text(spec)}\n\n"
            f"CURRENT FILES:\n{self._session_files(root, include_tests=True)}"
            f"{_named_existing_files(spec, root)}{self._convention_block(root)}\n\n"
            f"FAILURE OUTPUT:\n{_truncate(failures, _MAX_CONTEXT_BYTES)}",
            root,
            # A refine pass that yields no applicable edits is a legitimate
            # no-op (the model judged it had nothing to change, or returned a
            # bare explanation), not a hard error: returning an empty change
            # lets the test/refine loop reach its normal FAILED verdict instead
            # of aborting the whole run with an unhandled CodegenError.
            allow_empty=True,
        )

    async def _generate(self, system: str, user: str, root: Path, *, allow_empty: bool = False) -> CodeChange:
        """One LLM call → apply, with a single anchor-repair retry.

        The grounding context shows *snippets* of existing files, so a first
        edit attempt can anchor on text that differs from the file's real
        content — and a plain retry of the same prompt fails identically
        (run #13's live lesson). When every edit fails to anchor, retry ONCE
        with the failure reasons plus the exact current content of the files
        the model tried to edit, so the second attempt copies anchors from
        ground truth instead of from a snippet.

        ``allow_empty`` tolerates a response that carries no files (a no-op
        refine): the caller gets an empty ``CodeChange`` rather than an
        exception. Initial implement/test passes leave it False — producing
        nothing there is a real failure.
        """
        text = await self._complete(system, user)
        try:
            return self._apply(text, root, allow_empty=allow_empty)
        except CodegenError as exc:
            if not exc.failed_edit_paths and not exc.missing_edit_paths:
                raise
            logger.warning(
                "sdlc.codegen.repair_retry",
                extra={"failed": exc.failed_edit_paths, "missing": exc.missing_edit_paths},
            )
            repair = self._repair_block(exc, root)
            text = await self._complete(system, f"{user}{repair}")
            return self._apply(text, root, allow_empty=allow_empty)

    def _repair_block(self, exc: CodegenError, root: Path) -> str:
        """The corrective prompt suffix for a repair retry. Two kinds of fix:

        - ``missing_edit_paths`` — the model used the ``edits`` form for a file
          that doesn't exist; tell it to CREATE each one with the ``content``
          form (a new file carries full content, never edits).
        - ``failed_edit_paths`` — an existing file edited the wrong way; show its
          exact current content so the model re-anchors via ``edits``.
        """
        chunks: list[str] = [f"\n\nYOUR PREVIOUS ATTEMPT FAILED: {exc}\nRe-emit the full JSON object.\n"]
        if exc.missing_edit_paths:
            names = ", ".join(exc.missing_edit_paths)
            chunks.append(
                f"These file(s) DO NOT EXIST yet, so the `edits` form cannot apply: {names}. "
                "Create each as a NEW file using the `content` form (the full file content) — "
                "do NOT use `edits` for a file that does not already exist.\n"
            )
        if exc.failed_edit_paths:
            chunks.append(
                "Below is the CURRENT EXACT content of the existing file(s) involved. Change "
                "these ONLY via the `edits` form, copying every `find` snippet VERBATIM from this "
                "content (whitespace included) — never resend an existing file's full content:\n"
            )
            budget = _MAX_CONTEXT_BYTES
            for rel in exc.failed_edit_paths:
                try:
                    body = (root / rel).read_text(encoding="utf-8")
                except OSError:
                    continue
                block = f"--- {rel} (current content) ---\n{body}\n"
                if len(block) > budget:
                    break
                budget -= len(block)
                chunks.append(block)
        return "".join(chunks)

    def _session_files(self, root: Path, *, include_tests: bool) -> str:
        """The files this adapter wrote under ``root``, as a labeled prompt block.

        In Block C the worktree starts empty, so "everything in the worktree"
        and "what we wrote" coincide. When generating into a *real repo*
        worktree, dumping the whole tree buries the model in unrelated source —
        feed back only this session's files; fall back to the tree scan when
        nothing was tracked (e.g. a fresh adapter resuming a worktree).
        """
        written = [p for p in self._written.get(root.resolve(), []) if p.exists()]
        if not include_tests:
            written = [p for p in written if not p.name.startswith("test_")]
        if not written:
            return _read_worktree(root, include_tests=include_tests)
        chunks: list[str] = []
        budget = _MAX_CONTEXT_BYTES
        for file in written:
            block = f"--- {file.resolve().relative_to(root.resolve())} ---\n"
            block += file.read_text(encoding="utf-8") + "\n"
            if len(block) > budget:
                break
            budget -= len(block)
            chunks.append(block)
        return "".join(chunks)

    async def _complete(self, system: str, user: str) -> str:
        result = await self._llm.complete(
            [Message(role="system", content=system), Message(role="user", content=user)],
            model=self._model,
            json_object=True,  # codegen output is one JSON object — enforce it (Ollama/local reliability)
            max_tokens=_MAX_COMPLETION_TOKENS,
        )
        return result.text

    def _apply(self, text: str, root: Path, *, allow_empty: bool = False) -> CodeChange:
        payload = _loads_json_object(text)
        if payload is None:
            # The tail is the diagnostic fact: a truncated emission ends
            # mid-string; prose ends with words. Without it every parse
            # failure looks identical in the logs (runs #16/#18's lesson).
            logger.warning("sdlc.codegen.unparseable_output len=%d tail=%r", len(text), text[-160:])
            if allow_empty:
                return CodeChange()
            raise CodegenError("model output was not a JSON object")
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            if allow_empty:
                logger.info("sdlc.codegen.empty_refine summary=%r", str(payload.get("summary") or "")[:160])
                return CodeChange(summary=str(payload.get("summary") or "").strip())
            raise CodegenError("model output had no 'files' list")
        return apply_files(
            files,
            root,
            written_tracker=self._written,
            grounded=self._grounder is not None,
            summary=str(payload.get("summary") or "").strip(),
        )


def apply_files(
    files: list[Any],
    root: Path,
    *,
    written_tracker: dict[Path, list[Path]],
    grounded: bool,
    summary: str = "",
) -> CodeChange:
    """Apply a ``files`` list (new ``content`` / existing ``edits``) to a worktree.

    The shared write path for both single-shot codegen (``_apply``) and the
    agentic loop's ``write_files`` tool — every guard (path safety, stdlib
    shadow, brownfield create-only, size caps, ruff-fix, anchor-repair signal)
    lives here so both callers behave identically.
    """
    if len(files) > _MAX_FILES:
        raise CodegenError(f"model returned {len(files)} files (max {_MAX_FILES})")

    written: list[str] = []
    skipped: list[str] = []
    edit_failures: list[str] = []
    edit_failure_paths: list[str] = []
    missing_targets: list[str] = []  # edits aimed at a file that doesn't exist yet
    tracked_now = written_tracker.get(root.resolve(), [])
    for entry in files:
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path") or "").strip()
        content = entry.get("content")
        edits = entry.get("edits")
        if not rel:
            continue
        target = _safe_target(root, rel)

        # ---- edits form: anchored find/replace on an EXISTING file ------
        # Per-file atomic: every edit must anchor (exactly-once match) or
        # the whole file is left untouched and the reason is surfaced so
        # the refine loop can correct itself next pass.
        if isinstance(edits, list):
            if not target.exists():
                # The model picked the edits form for a file that isn't there —
                # common on greenfield. Recoverable: the repair tells it to
                # create the file with the content form (see ``_repair_block``).
                missing_targets.append(rel)
                edit_failures.append(f"{rel}: edits to a file that does not exist")
                continue
            try:
                patched = _apply_edit_list(target.read_text(encoding="utf-8"), edits, rel)
            except CodegenError as exc:
                edit_failures.append(str(exc))
                edit_failure_paths.append(rel)
                logger.warning("sdlc.codegen.edit_failed", extra={"path": rel})
                continue
            if len(patched.encode("utf-8")) > _MAX_PATCHED_FILE_BYTES:
                edit_failures.append(f"{rel}: patched file exceeds {_MAX_PATCHED_FILE_BYTES} bytes")
                continue
            target.write_text(patched, encoding="utf-8")
            written.append(str(target))
            continue

        if not isinstance(content, str):
            continue
        # Stdlib-shadow guard: a new top-level module named like a Python
        # standard-library module (statistics.py, json.py, ...) hijacks
        # every stdlib import in the repo — run #15's model did exactly
        # this when it dodged editing the existing module.
        if not target.exists() and _shadows_stdlib(root, target):
            raise CodegenError(
                f"refusing to create {rel!r}: it would shadow the Python "
                f"standard-library module {target.stem!r}"
            )
        # Brownfield create-only guard (deterministic, not prompt-hope):
        # when grounded, the worktree is a real repo — a model "fix" that
        # rewrites a pre-existing module it didn't create this session
        # breaks the codebase out from under its own tests. Skip it;
        # modifying an existing file must go through the edits form.
        if grounded and target.exists() and target not in tracked_now:
            skipped.append(rel)
            logger.warning("sdlc.codegen.skipped_existing_file", extra={"path": rel})
            continue
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise CodegenError(f"generated file {rel!r} exceeds {_MAX_FILE_BYTES} bytes")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(target))

    # Track what landed BEFORE any raise, so a repair retry doesn't
    # guard-skip the new files this pass just wrote (they'd otherwise look
    # like untracked pre-existing files on the retry).
    if written:
        _ruff_fix(root, written)
        tracked = written_tracker.setdefault(root.resolve(), [])
        for f in written:
            if Path(f) not in tracked:
                tracked.append(Path(f))

    # Existing files the model tried to modify the wrong way (full-content
    # rewrite → guard-skipped, or a bad anchor → edit failed) must trigger
    # the anchor-repair retry EVEN WHEN other files landed — otherwise a
    # create+edit feature silently drops the edit and ships half the change
    # (run #25: doctor.py created, the cli.py registration edit lost). The
    # repair shows the model those files' exact content and says "use edits".
    # Recoverable wrong-form mistakes — an existing file rewritten via content
    # (skipped), an edit that failed to anchor, or edits aimed at a file that
    # doesn't exist yet — all trigger ONE repair retry (even when other files
    # landed), so a create+edit feature never silently ships half the change.
    unmodified_existing = edit_failure_paths + skipped
    if unmodified_existing or missing_targets:
        notes = [
            f"skipped existing (use edits, not full content): {', '.join(skipped)}" if skipped else "",
            f"edit issues: {'; '.join(edit_failures)}" if edit_failures else "",
        ]
        detail = " (" + "; ".join(n for n in notes if n) + ")"
        raise CodegenError(
            f"changes need a repair pass{detail}",
            failed_edit_paths=unmodified_existing,
            missing_edit_paths=missing_targets,
        )
    if not written:
        detail = f" ({'; '.join(edit_failures)})" if edit_failures else ""
        raise CodegenError(f"model output produced no writable files{detail}")
    return CodeChange(files=written, summary=summary)


def _ruff_fix(root: Path, files: list[str]) -> None:
    """Best-effort ruff autofix + format on freshly written files.

    The repo's CI lints everything; a mechanical fix (unused import, sort
    order) must not cost a full CI round-trip. Uses the worktree's own
    config (cwd=root); silently skips when ruff isn't installed — the CI
    gate still has the final word.
    """
    import subprocess
    import sys

    py = [f for f in files if f.endswith(".py")]
    if not py:
        return
    # Module invocation: the worker's PATH may not expose a `ruff` script,
    # but the interpreter's environment has the package installed.
    base = [sys.executable, "-m", "ruff"]
    for args in (
        [*base, "check", "--fix", "--quiet", *py],
        [*base, "format", "--quiet", *py],
    ):
        try:
            subprocess.run(args, cwd=str(root), capture_output=True, timeout=60, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return


def _apply_edit_list(original: str, edits: list[Any], rel: str) -> str:
    """Apply anchored find/replace edits to ``original``, all-or-nothing.

    This is the patch-based editing contract (Track 2.3): the model anchors
    each change to an exact snippet of the file's current content instead of
    resending the whole file (which the brownfield guard rejects) or emitting
    line-numbered diffs (which models reliably get wrong). Deterministic
    checks, every failure mode named so the refine loop can self-correct:

      - a ``find`` that doesn't occur fails (stale anchor — file has moved on)
      - a ``find`` that occurs more than once fails (ambiguous anchor)
      - edits apply sequentially, so a later ``find`` may match text an
        earlier ``replace`` introduced
    """
    if not edits:
        raise CodegenError(f"{rel}: empty edits list")
    if len(edits) > _MAX_EDITS_PER_FILE:
        raise CodegenError(f"{rel}: {len(edits)} edits (max {_MAX_EDITS_PER_FILE})")
    patched = original
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise CodegenError(f"{rel}: edit {i} is not an object")
        find = edit.get("find")
        replace = edit.get("replace")
        if not isinstance(find, str) or not find or not isinstance(replace, str):
            raise CodegenError(f"{rel}: edit {i} needs non-empty 'find' and string 'replace'")
        occurrences = patched.count(find)
        if occurrences == 0:
            raise CodegenError(
                f"{rel}: edit {i} 'find' text not found — copy it exactly from the current file"
            )
        if occurrences > 1:
            raise CodegenError(
                f"{rel}: edit {i} 'find' matches {occurrences} times — add surrounding "
                "lines to make it unique"
            )
        patched = patched.replace(find, replace, 1)
    return patched


def _shadows_stdlib(root: Path, target: Path) -> bool:
    """True when ``target`` is a worktree-root ``<stdlib-name>.py`` module.

    Only root-level files can shadow the stdlib for code running with the
    worktree on ``sys.path``; package-internal modules (``pkg/json.py``) are
    imported by qualified name and are fine.
    """
    import sys

    if target.suffix != ".py" or target.parent != root.resolve():
        return False
    return target.stem in sys.stdlib_module_names


def _safe_target(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``, refusing any path that escapes it.

    The model controls ``rel``, so this is the injection boundary: an absolute
    path or a ``..`` escape must never let it write outside the worktree.
    """
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    if not candidate.is_relative_to(root_resolved):
        raise CodegenError(f"refusing to write outside the worktree: {rel!r}")
    return candidate


_PATH_RE = re.compile(r"\b((?:src/|tests/)[\w./-]+\.py)\b")


def _named_existing_files(spec: dict[str, Any], root: Path) -> str:
    """Full current content of existing repo files the SPEC names by path.

    A surgical edit inside a large file (run #27: raise_approval_request in the
    ~300-line activities.py) fails when the model regenerates the whole file
    from memory — it truncates or malforms the JSON, and a full-content rewrite
    would be guard-skipped anyway. Giving it the file's EXACT content lets it
    anchor small ``edits`` against ground truth — the way an engineer opens the
    file before changing it. Paths are read from the spec text; only existing,
    in-worktree ``.py`` files are included, size-capped.
    """
    blob = " ".join(
        [
            str(spec.get("summary") or ""),
            str(spec.get("technical_notes") or ""),
            *_str_list(spec.get("acceptance_criteria")),
        ]
    )
    seen: list[str] = []
    for rel in _PATH_RE.findall(blob):
        if rel not in seen:
            seen.append(rel)
    chunks: list[str] = []
    budget = _MAX_CONTEXT_BYTES
    for rel in seen:
        target = (root / rel).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            continue
        body = target.read_text(encoding="utf-8")
        block = f"--- {rel} (current content — edit THIS via the edits form) ---\n{body}\n"
        if len(block) > budget:
            break
        budget -= len(block)
        chunks.append(block)
    if not chunks:
        return ""
    return (
        "\n\nEXISTING FILES THE SPEC NAMES — change these with the `edits` form, "
        "anchoring on snippets copied verbatim from the content below. Do NOT "
        "re-emit them as full `content`:\n" + "".join(chunks)
    )


def _spec_text(spec: dict[str, Any]) -> str:
    """Render the spec dict into the prompt's plain-text block."""
    lines: list[str] = []
    for label, key in (
        ("Title", "title"),
        ("Summary", "summary"),
        ("User story", "user_story"),
        ("Technical notes", "technical_notes"),
    ):
        value = str(spec.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    criteria = _str_list(spec.get("acceptance_criteria"))
    if criteria:
        lines.append("Acceptance criteria:")
        lines.extend(f"  - {c}" for c in criteria)
    return "\n".join(lines) if lines else "(no spec details provided)"


def _read_worktree(root: Path, *, include_tests: bool) -> str:
    """Dump the worktree's ``.py`` files as a labeled block for the prompt.

    Skips ``.git`` and (optionally) ``test_*`` files; caps the total size so a
    big worktree can't blow past the model's context.
    """
    chunks: list[str] = []
    budget = _MAX_CONTEXT_BYTES
    for file in sorted(root.rglob("*.py")):
        if ".git" in file.parts:
            continue
        if not include_tests and file.name.startswith("test_"):
            continue
        try:
            body = file.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = file.relative_to(root)
        block = f"--- {rel} ---\n{body}\n"
        if len(block) > budget:
            break
        budget -= len(block)
        chunks.append(block)
    return "".join(chunks) if chunks else "(worktree is empty)"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[-limit:]


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _loads_json_object(text: str) -> dict[str, Any] | None:
    """Parse a single JSON object, tolerating code fences and surrounding prose.

    ``strict=False`` is load-bearing: coding models routinely emit file
    ``content`` with LITERAL newlines/tabs inside the JSON string rather than
    ``\\n``/``\\t`` escapes, which strict ``json.loads`` rejects as "Invalid
    control character" even though the payload is otherwise complete and
    fenced (run #21: a 10k-char fenced object that wasn't truncated). Lenient
    parsing accepts the control characters verbatim — exactly what we want,
    since they become the file's real bytes.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    try:
        loaded = json.loads(stripped, strict=False)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            loaded = json.loads(stripped[start : end + 1], strict=False)
        except json.JSONDecodeError:
            return None
    return loaded if isinstance(loaded, dict) else None


__all__ = [
    "CodeChange",
    "CodePlan",
    "CodegenAdapter",
    "CodegenError",
    "CodegenGrounder",
    "LLMCodegenAdapter",
    "StubCodegenAdapter",
    "resolve_codegen_model",
]
