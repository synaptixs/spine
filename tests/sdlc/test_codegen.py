"""Unit tests for the real LLM-backed codegen adapter.

A scripted fake ``LLMClient`` returns canned JSON so the tests stay offline and
deterministic, while still exercising the adapter for real: it writes genuine
Python files into a tmp worktree, and ``SubprocessTestRunner`` actually runs the
generated tests with ``pytest``. That's the whole point — spec in, runnable +
tested code out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.codegen import (
    CodegenAdapter,
    CodegenError,
    LLMCodegenAdapter,
    resolve_codegen_model,
)
from orchestrator.sdlc.testrunner import SubprocessTestRunner


class _ScriptedLLM:
    """Returns queued responses in order; quacks like ``LLMClient``."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        response_format: type[BaseModel] | None = None,
        json_object: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: object = None,
    ) -> CompletionResult:
        _ = (model, response_format, json_object, temperature, max_tokens, tools)
        self.calls.append(list(messages))
        text = self._responses.pop(0)
        return CompletionResult(
            text=text,
            model="fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
        )


def _files_response(files: dict[str, str], summary: str = "ok") -> str:
    return json.dumps(
        {
            "files": [{"path": p, "content": c} for p, c in files.items()],
            "summary": summary,
        }
    )


_SPEC: dict[str, Any] = {
    "title": "Add two numbers",
    "summary": "Provide an add(a, b) helper.",
    "acceptance_criteria": ["add(2, 3) returns 5"],
}


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(LLMCodegenAdapter(_ScriptedLLM([])), CodegenAdapter)


class TestResolveCodegenModel:
    """Codegen model precedence: --model > SDLC_CODEGEN_MODEL >
    ORCHESTRATOR_INTAKE_MODEL > adapter default (None)."""

    def test_explicit_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_CODEGEN_MODEL", "from-codegen-env")
        monkeypatch.setenv("ORCHESTRATOR_INTAKE_MODEL", "from-intake-env")
        assert resolve_codegen_model("explicit") == "explicit"

    def test_codegen_env_beats_intake_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_CODEGEN_MODEL", "from-codegen-env")
        monkeypatch.setenv("ORCHESTRATOR_INTAKE_MODEL", "from-intake-env")
        assert resolve_codegen_model() == "from-codegen-env"

    def test_falls_back_to_intake_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The key fix: with only the intake model set, codegen inherits it
        # instead of jumping to a different hardcoded provider default.
        monkeypatch.delenv("SDLC_CODEGEN_MODEL", raising=False)
        monkeypatch.setenv("ORCHESTRATOR_INTAKE_MODEL", "gpt-4o")
        assert resolve_codegen_model() == "gpt-4o"

    def test_none_when_nothing_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SDLC_CODEGEN_MODEL", raising=False)
        monkeypatch.delenv("ORCHESTRATOR_INTAKE_MODEL", raising=False)
        assert resolve_codegen_model() is None  # → adapter default


async def test_plan_derives_steps_from_acceptance_criteria() -> None:
    adapter = LLMCodegenAdapter(_ScriptedLLM([]))  # plan does no LLM call
    plan = await adapter.plan(spec=_SPEC, path="/tmp/ws")
    assert plan.steps == ["satisfy: add(2, 3) returns 5"]


async def test_implement_then_author_tests_runs_green(tmp_path: Path) -> None:
    """The end-to-end slice: implement writes real source, author_tests writes a
    real test, and pytest actually runs them green."""
    llm = _ScriptedLLM(
        [
            _files_response({"calculator.py": "def add(a, b):\n    return a + b\n"}),
            _files_response(
                {
                    "test_calculator.py": (
                        "from calculator import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
                    )
                }
            ),
        ]
    )
    adapter = LLMCodegenAdapter(llm)

    impl = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")
    assert impl.files == [str(tmp_path / "calculator.py")]
    assert (tmp_path / "calculator.py").exists()

    tests = await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")
    assert tests.files == [str(tmp_path / "test_calculator.py")]

    result = await SubprocessTestRunner().run(path=str(tmp_path))
    assert result.passed is True, result.output


async def test_refine_fixes_a_failing_test(tmp_path: Path) -> None:
    """A buggy implementation makes pytest red; refine returns a corrected source
    and the rerun goes green — the refinement loop, end to end."""
    llm = _ScriptedLLM(
        [
            # Buggy: subtracts instead of adds.
            _files_response({"calculator.py": "def add(a, b):\n    return a - b\n"}),
            _files_response(
                {
                    "test_calculator.py": (
                        "from calculator import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
                    )
                }
            ),
            # refine returns the corrected source.
            _files_response({"calculator.py": "def add(a, b):\n    return a + b\n"}),
        ]
    )
    adapter = LLMCodegenAdapter(llm)
    runner = SubprocessTestRunner()

    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")

    red = await runner.run(path=str(tmp_path))
    assert red.passed is False

    refined = await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1", failures=red.output)
    assert refined.files == [str(tmp_path / "calculator.py")]

    green = await runner.run(path=str(tmp_path))
    assert green.passed is True, green.output


async def test_refine_prompt_includes_failure_output(tmp_path: Path) -> None:
    """The pytest failure is fed back to the model so it can correct the code."""
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    llm = _ScriptedLLM([_files_response({"calculator.py": "def add(a, b):\n    return a + b\n"})])
    adapter = LLMCodegenAdapter(llm)

    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1", failures="E   assert 99 == 5")

    user_msg = llm.calls[0][1].content
    assert "FAILURE OUTPUT" in user_msg
    assert "assert 99 == 5" in user_msg


async def test_rejects_path_escape(tmp_path: Path) -> None:
    """The model controls the path, so a `..` escape must be refused."""
    llm = _ScriptedLLM([_files_response({"../evil.py": "print('pwned')\n"})])
    adapter = LLMCodegenAdapter(llm)
    with pytest.raises(CodegenError):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")
    assert not (tmp_path.parent / "evil.py").exists()


async def test_rejects_absolute_path(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_files_response({"/etc/evil.py": "x = 1\n"})])
    adapter = LLMCodegenAdapter(llm)
    with pytest.raises(CodegenError):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")


async def test_rejects_output_with_no_files(tmp_path: Path) -> None:
    llm = _ScriptedLLM(['{"summary": "I did nothing"}'])
    adapter = LLMCodegenAdapter(llm)
    with pytest.raises(CodegenError):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")


async def test_refine_tolerates_a_no_op_response(tmp_path: Path) -> None:
    """A refine pass that returns no files (the model judged nothing to change,
    or replied with a bare explanation) is a legitimate no-op — it yields an
    empty change instead of raising, so the test/refine loop can reach its
    normal FAILED verdict rather than aborting the run."""
    no_files = _ScriptedLLM(['{"summary": "no change needed"}'])
    change = await LLMCodegenAdapter(no_files).refine(
        spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1", failures="E   assert 1 == 2"
    )
    assert change.files == []
    assert change.summary == "no change needed"

    not_json = _ScriptedLLM(["Sorry, I can't fix this."])
    prose = await LLMCodegenAdapter(not_json).refine(
        spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1", failures="E   assert 1 == 2"
    )
    assert prose.files == []


async def test_layout_block_pins_paths_in_every_phase(tmp_path: Path) -> None:
    """When a TargetLayout is set, every phase prompt leads with the authoritative
    path block — the fix for greenfield path invention (e.g. src/orchestrator/...)."""
    from orchestrator.sdlc.layout import TargetLayout

    layout = TargetLayout("aeo", "src/aeo", "tests", src_layout=True, mode="new")
    llm = _ScriptedLLM(
        [
            _files_response({"src/aeo/calc.py": "def add(a, b):\n    return a + b\n"}),  # implement
            _files_response({"tests/test_calc.py": "def test_x():\n    assert True\n"}),  # author_tests
            _files_response({"src/aeo/calc.py": "def add(a, b):\n    return a + b\n"}),  # refine
        ]
    )
    adapter = LLMCodegenAdapter(llm, layout=layout)

    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1", failures="E   boom")

    for call in llm.calls:  # one per phase
        user = call[-1].content
        assert "PROJECT LAYOUT" in user
        assert "src/aeo/" in user
        assert "from aeo.<module> import" in user


async def test_java_layout_selects_java_prompts(tmp_path: Path) -> None:
    """A Java layout switches implement/author_tests to the Java (Maven/JUnit)
    prompts and pins the Java package in the layout block."""
    from orchestrator.sdlc.layout import TargetLayout

    layout = TargetLayout(
        "com.demo",
        "src/main/java/com/demo",
        "src/test/java/com/demo",
        True,
        "new",
        language="java",
        build_tool="maven",
    )
    llm = _ScriptedLLM(
        [
            _files_response(
                {"src/main/java/com/demo/Widget.java": "package com.demo;\npublic class Widget {}\n"}
            ),
            _files_response(
                {"src/test/java/com/demo/WidgetTest.java": "package com.demo;\nclass WidgetTest {}\n"}
            ),
        ]
    )
    adapter = LLMCodegenAdapter(llm, layout=layout)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="J-1")
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="J-1")

    assert "runnable Java" in llm.calls[0][0].content  # implement system prompt
    assert "JUnit 5" in llm.calls[1][0].content  # author_tests system prompt
    assert "package com.demo;" in llm.calls[0][-1].content  # layout block (user)


async def test_typescript_layout_selects_typescript_prompts(tmp_path: Path) -> None:
    """A TypeScript layout switches implement/author_tests/refine to the Vitest
    prompts and pins the TS module/import conventions in the layout block."""
    from orchestrator.sdlc.layout import TargetLayout

    layout = TargetLayout(
        "widgets",
        "src",
        "src",  # co-located *.test.ts
        True,
        "new",
        language="typescript",
        build_tool="npm",
    )
    llm = _ScriptedLLM(
        [
            _files_response(
                {"src/calc.ts": "export function add(a: number, b: number) {\n return a+b;\n}\n"}
            ),
            _files_response({"src/calc.test.ts": 'import { it } from "vitest";\nit("x", () => {});\n'}),
            _files_response(
                {"src/calc.ts": "export function add(a: number, b: number) {\n return a+b;\n}\n"}
            ),
        ]
    )
    adapter = LLMCodegenAdapter(llm, layout=layout)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="T-1")
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="T-1")
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures="FAIL src/calc.test.ts")

    assert "runnable TypeScript" in llm.calls[0][0].content  # implement system prompt
    assert "Vitest" in llm.calls[1][0].content  # author_tests system prompt
    assert "package.json" in llm.calls[2][0].content  # refine system prompt
    assert "<name>.test.ts" in llm.calls[0][-1].content  # layout block (user)
    assert ".js" in llm.calls[0][-1].content  # NodeNext import hint


async def test_csharp_layout_selects_csharp_prompts(tmp_path: Path) -> None:
    """A C# layout switches implement/author_tests/refine to the .NET/xUnit prompts
    and pins the C# namespace + project conventions in the layout block."""
    from orchestrator.sdlc.layout import TargetLayout

    layout = TargetLayout(
        "Widgets",
        "src/Widgets",
        "tests/Widgets.Tests",
        True,
        "new",
        language="csharp",
        build_tool="dotnet",
    )
    llm = _ScriptedLLM(
        [
            _files_response({"src/Widgets/Calc.cs": "namespace Widgets;\npublic class Calc {}\n"}),
            _files_response(
                {"tests/Widgets.Tests/CalcTests.cs": "using Xunit;\npublic class CalcTests {}\n"}
            ),
            _files_response({"src/Widgets/Calc.cs": "namespace Widgets;\npublic class Calc {}\n"}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, layout=layout)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="C-1")
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="C-1")
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="C-1", failures="error CS0103")

    assert "runnable C#" in llm.calls[0][0].content  # implement system prompt
    assert "xUnit" in llm.calls[1][0].content  # author_tests system prompt
    assert ".csproj" in llm.calls[2][0].content  # refine system prompt
    assert "namespace Widgets;" in llm.calls[0][-1].content  # layout block (user)
    assert "<TypeName>Tests.cs" in llm.calls[0][-1].content  # xUnit test path hint


async def test_c_layout_selects_c_prompts(tmp_path: Path) -> None:
    """A C layout switches implement/author_tests/refine to the CMake/ctest prompts and
    pins the header/source + tests-dir conventions in the layout block."""
    from orchestrator.sdlc.layout import TargetLayout

    layout = TargetLayout("calc_lib", "src", "tests", True, "new", language="c", build_tool="cmake")
    llm = _ScriptedLLM(
        [
            _files_response({"src/calc.c": '#include "calc.h"\nint add(int a,int b){return a+b;}\n'}),
            _files_response({"tests/test_calc.c": '#include "calc.h"\nint main(void){return 0;}\n'}),
            _files_response({"src/calc.c": '#include "calc.h"\nint add(int a,int b){return a+b;}\n'}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, layout=layout)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="C-1")
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="C-1")
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="C-1", failures="error: expected ';'")

    assert "runnable C inside" in llm.calls[0][0].content  # implement system prompt
    assert "C unit tests" in llm.calls[1][0].content  # author_tests system prompt
    assert "CMakeLists.txt" in llm.calls[2][0].content  # refine system prompt
    assert "test_<name>.c" in llm.calls[0][-1].content  # layout block (user)
    assert "#ifndef" in llm.calls[0][-1].content  # header-guard hint


async def test_cpp_layout_selects_cpp_prompts(tmp_path: Path) -> None:
    """A C++ layout switches the phases to the CMake/ctest C++ prompts and pins the
    header/source + RAII conventions in the layout block."""
    from orchestrator.sdlc.layout import TargetLayout

    layout = TargetLayout("vec", "src", "tests", True, "new", language="cpp", build_tool="cmake")
    llm = _ScriptedLLM(
        [
            _files_response({"src/vec.cpp": '#include "vec.hpp"\n'}),
            _files_response({"tests/test_vec.cpp": '#include "vec.hpp"\nint main(){return 0;}\n'}),
            _files_response({"src/vec.cpp": '#include "vec.hpp"\n'}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, layout=layout)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="P-1")
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="P-1")
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="P-1", failures="error: expected ';'")

    assert "runnable C++" in llm.calls[0][0].content  # implement system prompt
    assert "C++ unit tests" in llm.calls[1][0].content  # author_tests system prompt
    assert "CMakeLists.txt" in llm.calls[2][0].content  # refine system prompt
    assert "test_<name>.cpp" in llm.calls[0][-1].content  # layout block (user)
    assert ".hpp" in llm.calls[0][-1].content  # header-discipline hint


async def test_no_layout_block_when_layout_unset(tmp_path: Path) -> None:
    """Backward compatible: without a layout, the prompt carries no layout block."""
    llm = _ScriptedLLM([_files_response({"m.py": "x = 1\n"})])
    await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")
    assert "PROJECT LAYOUT" not in llm.calls[0][-1].content


async def test_persona_conditions_single_shot_system_prompt(tmp_path: Path) -> None:
    """Phase 2b: the single-shot (CLI) path also runs as the persona — its role leads
    the implement system prompt and its vetting-gated, plan-selected skills append."""
    from orchestrator.personas.software_engineer import SOFTWARE_ENGINEER

    llm = _ScriptedLLM([_files_response({"m.py": "x = 1\n"})])
    adapter = LLMCodegenAdapter(llm, persona=SOFTWARE_ENGINEER)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1", skills=["python-conventions"])
    system = llm.calls[0][0].content
    assert system.startswith("You are a senior software engineer")
    assert "Python conventions" in system


async def test_single_shot_unchanged_without_persona_or_skills(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_files_response({"m.py": "x = 1\n"})])
    await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
    system = llm.calls[0][0].content
    assert "For this project" not in system  # no persona + no skills → historical prompt


class TestPhaseAwareConditioning:
    """Persona-skill measurement P0: a skill conditions only the phase(s) it declares."""

    async def test_test_strategy_reaches_author_tests_not_implement(self, tmp_path: Path) -> None:
        # test-strategy declares phases=("author_tests", "refine") — it must shape the
        # suite phase, and must NOT leak into implement (the first A/B's blind spot).
        llm = _ScriptedLLM(
            [
                _files_response({"m.py": "def add(a, b):\n    return a + b\n"}),  # implement
                _files_response({"test_m.py": "def test_x() -> None:\n    assert True\n"}),  # author_tests
            ]
        )
        adapter = LLMCodegenAdapter(llm, skills=["test-strategy"])
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
        await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
        implement_system = llm.calls[0][0].content
        author_system = llm.calls[1][0].content
        assert "boundary values" not in implement_system  # the skill does not reach implement
        assert "For this project" not in implement_system  # implement is the historical prompt
        assert "boundary values" in author_system  # but it does shape author_tests

    async def test_implement_skill_does_not_leak_into_author_tests(self, tmp_path: Path) -> None:
        # convention-digest declares phases=("implement", "refine") — present in
        # implement, absent from author_tests.
        llm = _ScriptedLLM(
            [
                _files_response({"m.py": "x = 1\n"}),
                _files_response({"test_m.py": "def test_x() -> None:\n    assert True\n"}),
            ]
        )
        adapter = LLMCodegenAdapter(llm, skills=["convention-digest"])
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
        await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
        assert "infer the repo's conventions" in llm.calls[0][0].content  # implement
        assert "infer the repo's conventions" not in llm.calls[1][0].content  # not author_tests

    async def test_author_tests_unchanged_without_phase_skills(self, tmp_path: Path) -> None:
        # No author_tests-phase skill → the tests system prompt is the historical one.
        llm = _ScriptedLLM(
            [
                _files_response({"m.py": "x = 1\n"}),
                _files_response({"test_m.py": "def test_x() -> None:\n    assert True\n"}),
            ]
        )
        adapter = LLMCodegenAdapter(llm, skills=["convention-digest"])  # implement-phase only
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
        await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
        assert "For this project" not in llm.calls[1][0].content


async def test_tolerates_code_fences(tmp_path: Path) -> None:
    """Models often wrap JSON in ```json fences; we strip them."""
    fenced = "```json\n" + _files_response({"m.py": "x = 1\n"}) + "\n```"
    adapter = LLMCodegenAdapter(_ScriptedLLM([fenced]))
    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")
    assert change.files == [str(tmp_path / "m.py")]


# ---- patch-based editing of existing files (Track 2.3) --------------------


class _Grounder:
    def context_for_spec(self, spec: dict[str, Any]) -> str:
        return "EXISTING CODEBASE CONTEXT"


def _edits_response(path: str, edits: list[dict[str, str]], summary: str = "ok") -> str:
    return json.dumps({"files": [{"path": path, "edits": edits}], "summary": summary})


def _existing(tmp_path: Path, name: str, content: str) -> Path:
    target = tmp_path / name
    target.write_text(content, encoding="utf-8")
    return target


async def test_edits_modify_a_preexisting_file(tmp_path: Path) -> None:
    """The brownfield guard forbids rewriting existing modules; anchored edits
    are the sanctioned way to change them."""
    existing = _existing(tmp_path, "util.py", "def helper() -> int:\n    return 1\n")
    llm = _ScriptedLLM([_edits_response("util.py", [{"find": "return 1", "replace": "return 2"}])])
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())

    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert change.files == [str(existing)]
    assert "return 2" in existing.read_text(encoding="utf-8")


async def test_edits_apply_sequentially(tmp_path: Path) -> None:
    existing = _existing(tmp_path, "util.py", "A = 1\nB = 2\n")
    llm = _ScriptedLLM(
        [
            _edits_response(
                "util.py",
                [
                    {"find": "A = 1", "replace": "A = 10\nC = 3"},
                    {"find": "C = 3\nB = 2", "replace": "B = 2\nC = 3"},
                ],
            )
        ]
    )
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    assert existing.read_text(encoding="utf-8") == "A = 10\nB = 2\nC = 3\n"


async def test_missing_anchor_leaves_file_untouched(tmp_path: Path) -> None:
    original = "def helper() -> int:\n    return 1\n"
    existing = _existing(tmp_path, "util.py", original)
    bad = _edits_response("util.py", [{"find": "return 99", "replace": "return 2"}])
    llm = _ScriptedLLM([bad, bad])  # initial + the anchor-repair retry
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())

    with pytest.raises(CodegenError, match="not found"):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    assert existing.read_text(encoding="utf-8") == original


async def test_ambiguous_anchor_is_atomic_per_file(tmp_path: Path) -> None:
    """An ambiguous edit leaves the file untouched and triggers a repair; the
    repair lands both the new file and the corrected edit."""
    existing = _existing(tmp_path, "util.py", "x = 1\nx = 1\n")
    llm = _ScriptedLLM(
        [
            json.dumps(
                {
                    "files": [
                        {"path": "new.py", "content": "y = 2\n"},
                        {"path": "util.py", "edits": [{"find": "x = 1", "replace": "x = 2"}]},
                    ],
                    "summary": "mixed",
                }
            ),
            # Repair: a unique anchor for the first occurrence.
            _edits_response("util.py", [{"find": "x = 1\nx = 1", "replace": "x = 2\nx = 1"}]),
        ]
    )
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())

    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert existing.read_text(encoding="utf-8") == "x = 2\nx = 1\n"
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "y = 2\n"


async def test_edits_to_nonexistent_file_still_fails_if_model_wont_fix(tmp_path: Path) -> None:
    """Edits aimed at a nonexistent file are recoverable (a repair retry fires),
    but if the model repeats the same mistake it ultimately fails."""
    bad = _edits_response("ghost.py", [{"find": "a", "replace": "b"}])
    llm = _ScriptedLLM([bad, bad])  # initial + the one repair attempt
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())
    with pytest.raises(CodegenError, match="does not exist"):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    assert len(llm.calls) == 2  # it DID retry, not hard-fail on the first attempt


async def test_edits_to_nonexistent_file_repairs_to_content(tmp_path: Path) -> None:
    """Greenfield slip: the model picks the `edits` form for a brand-new file.
    The repair tells it the file doesn't exist; it re-emits with `content` and
    the feature lands (regression test for the `crawler.py` hard-fail)."""
    llm = _ScriptedLLM(
        [
            _edits_response("crawler.py", [{"find": "def crawl", "replace": "def crawl(): pass"}]),
            _files_response({"crawler.py": "def crawl(url: str) -> str:\n    return url\n"}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())

    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert change.files == [str(tmp_path / "crawler.py")]
    assert (tmp_path / "crawler.py").read_text(encoding="utf-8").startswith("def crawl(url")
    # The repair prompt told the model the file did not exist + to use content.
    assert "DO NOT EXIST" in llm.calls[1][-1].content


async def test_content_rewrite_of_existing_file_triggers_repair(tmp_path: Path) -> None:
    """A full-content rewrite of a pre-existing file is guard-skipped, which
    now triggers a repair (run #25): the model is shown the file and switches
    to the edits form. The original is never clobbered by the raw rewrite."""
    original = "def helper() -> int:\n    return 1\n"
    existing = _existing(tmp_path, "util.py", original)
    llm = _ScriptedLLM(
        [
            json.dumps(
                {
                    "files": [
                        {"path": "util.py", "content": "REWRITTEN"},
                        {"path": "new.py", "content": "y = 2\n"},
                    ],
                    "summary": "rewrite attempt",
                }
            ),
            _edits_response("util.py", [{"find": "return 1", "replace": "return 2"}]),
        ]
    )
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())

    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    body = existing.read_text(encoding="utf-8")
    assert "REWRITTEN" not in body  # raw rewrite never applied
    assert "return 2" in body  # the repair's edit did
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "y = 2\n"
    # The repair prompt showed the model the existing file's real content.
    assert "util.py (current content)" in llm.calls[1][-1].content


async def test_create_plus_edit_does_not_silently_drop_the_edit(tmp_path: Path) -> None:
    """Run #25 in miniature: a feature that creates a new module AND must edit
    an existing one. If the model rewrites the existing file instead of editing
    it, the new file landing must NOT let the lost edit pass silently."""
    cli = _existing(tmp_path, "cli.py", "app = App()\n# commands\n")
    llm = _ScriptedLLM(
        [
            # Creates doctor.py (good) but rewrites cli.py via content (skipped).
            json.dumps(
                {
                    "files": [
                        {"path": "doctor.py", "content": "def run() -> int:\n    return 0\n"},
                        {"path": "cli.py", "content": "WHOLE NEW CLI"},
                    ],
                    "summary": "create + (bad) rewrite",
                }
            ),
            # Repair: keep doctor.py, edit cli.py properly.
            _edits_response("cli.py", [{"find": "# commands", "replace": "# commands\napp.add(doctor)"}]),
        ]
    )
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())

    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert (tmp_path / "doctor.py").exists()  # new file survived the repair
    cli_body = cli.read_text(encoding="utf-8")
    assert "WHOLE NEW CLI" not in cli_body  # raw rewrite never applied
    assert "app.add(doctor)" in cli_body  # the registration edit landed


async def test_refine_can_edit_files_the_session_created(tmp_path: Path) -> None:
    """Session-created files may be edited too — refine anchors a fix instead
    of resending the file."""
    llm = _ScriptedLLM(
        [
            _files_response({"calc.py": "def add(a: int, b: int) -> int:\n    return a - b\n"}),
            _edits_response("calc.py", [{"find": "return a - b", "replace": "return a + b"}]),
        ]
    )
    adapter = LLMCodegenAdapter(llm)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="E-1", failures="2-3 != 5")
    assert "return a + b" in (tmp_path / "calc.py").read_text(encoding="utf-8")


async def test_edit_cap_per_file(tmp_path: Path) -> None:
    _existing(tmp_path, "util.py", "x = 1\n")
    too_many = [{"find": "x", "replace": "x"} for _ in range(21)]
    bad = _edits_response("util.py", too_many)
    llm = _ScriptedLLM([bad, bad])  # initial + the anchor-repair retry
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())
    with pytest.raises(CodegenError, match="max 20"):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")


# ---- anchor-repair retry (run #13's live lesson) ---------------------------


async def test_anchor_repair_retries_with_exact_file_content(tmp_path: Path) -> None:
    """A missed anchor triggers ONE corrective call carrying the file's exact
    current content; the second attempt lands."""
    existing = _existing(tmp_path, "util.py", "def helper() -> int:\n    return 1\n")
    llm = _ScriptedLLM(
        [
            # First attempt anchors on text that isn't in the file (snippet drift).
            _edits_response("util.py", [{"find": "return 1  # old", "replace": "return 2"}]),
            # Repair attempt anchors correctly.
            _edits_response("util.py", [{"find": "return 1", "replace": "return 2"}]),
        ]
    )
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())

    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert "return 2" in existing.read_text(encoding="utf-8")
    assert change.files == [str(existing)]
    # The repair prompt carried the failure and the file's exact content.
    repair_prompt = llm.calls[1][-1].content
    assert "YOUR PREVIOUS ATTEMPT FAILED" in repair_prompt
    assert "util.py (current content)" in repair_prompt
    assert "def helper() -> int:" in repair_prompt


async def test_anchor_repair_gives_up_after_one_retry(tmp_path: Path) -> None:
    _existing(tmp_path, "util.py", "x = 1\n")
    bad = _edits_response("util.py", [{"find": "nope", "replace": "y"}])
    llm = _ScriptedLLM([bad, bad])
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())
    with pytest.raises(CodegenError, match="not found"):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    assert len(llm.calls) == 2  # initial + exactly one repair


async def test_no_repair_for_non_edit_failures(tmp_path: Path) -> None:
    """A response with no files at all fails immediately — repair is only for
    anchor misses."""
    llm = _ScriptedLLM([json.dumps({"files": [], "summary": "nothing"})])
    adapter = LLMCodegenAdapter(llm)
    with pytest.raises(CodegenError, match="no 'files'"):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    assert len(llm.calls) == 1


async def test_new_root_module_shadowing_stdlib_is_rejected(tmp_path: Path) -> None:
    """Run #15's failure mode: a new root-level statistics.py hijacks every
    stdlib `import statistics` in the repo. Refused deterministically."""
    bad = _files_response({"statistics.py": "def median(x):\n    return 0\n"})
    llm = _ScriptedLLM([bad])
    adapter = LLMCodegenAdapter(llm)
    with pytest.raises(CodegenError, match="shadow the Python standard-library"):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")


async def test_stdlib_name_inside_a_package_is_fine(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_files_response({"mypkg/types.py": "X = 1\n"})])
    adapter = LLMCodegenAdapter(llm)
    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    assert change.files == [str(tmp_path / "mypkg" / "types.py")]


async def test_guard_skip_triggers_repair_with_edits_form(tmp_path: Path) -> None:
    """Run #16's failure: a full-content rewrite of an existing file is
    guard-skipped; the repair retry shows the file's real content and the
    model comes back with anchored edits."""
    existing = _existing(tmp_path, "test_stats.py", "def test_old() -> None:\n    assert True\n")
    llm = _ScriptedLLM(
        [
            # First attempt resends the existing file in full — guard-skipped.
            _files_response({"test_stats.py": "REWRITE"}),
            # Repair attempt appends via an anchored edit.
            _edits_response(
                "test_stats.py",
                [
                    {
                        "find": "def test_old() -> None:\n    assert True\n",
                        "replace": "def test_old() -> None:\n    assert True\n\n\n"
                        "def test_new() -> None:\n    assert True\n",
                    }
                ],
            ),
        ]
    )
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder())

    change = await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert change.files == [str(existing)]
    assert "test_new" in existing.read_text(encoding="utf-8")
    repair_prompt = llm.calls[1][-1].content
    assert "test_stats.py (current content)" in repair_prompt
    assert "def test_old" in repair_prompt


def test_prompt_examples_contain_no_json_comments() -> None:
    """The first benchmark run's lesson: an illustrative // comment in the
    output-shape example teaches the model to emit comment-laden JSON that
    json.loads rejects. Keep every prompt example strictly valid JSON."""
    from orchestrator.sdlc import codegen

    prompts = (
        codegen._FILE_FORMS,
        codegen._IMPLEMENT_SYSTEM,
        codegen._TESTS_SYSTEM,
        codegen._REFINE_SYSTEM,
    )
    for prompt in prompts:
        assert "//" not in prompt


async def test_literal_newlines_in_content_parse(tmp_path: Path) -> None:
    """Run #21's failure: a coding model emits file content with literal
    newlines inside the JSON string (not \\n escapes). strict=False accepts
    them; the bytes become the file's real content."""
    raw = (
        '{"files": [{"path": "mod.py", "content": "def f() -> int:\n'
        '    return 1\n"}], "summary": "literal newlines"}'
    )
    assert "\n" in raw  # the string literally spans lines
    llm = _ScriptedLLM([raw])
    adapter = LLMCodegenAdapter(llm)
    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    body = (tmp_path / "mod.py").read_text(encoding="utf-8")
    assert body == "def f() -> int:\n    return 1\n"
    assert change.files == [str(tmp_path / "mod.py")]


async def test_fenced_json_with_literal_newlines_parses(tmp_path: Path) -> None:
    """The exact run-#21 shape: ```json fence + literal newlines in content."""
    raw = '```json\n{"files": [{"path": "m.py", "content": "x = 1\ny = 2\n"}], "summary": "ok"}\n```'
    llm = _ScriptedLLM([raw])
    adapter = LLMCodegenAdapter(llm)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"


def test_named_existing_files_included_for_edit(tmp_path: Path) -> None:
    """Run #27: an edit to a large existing file failed because the model
    regenerated it from memory. The prompt now includes the exact current
    content of files the spec names, so the model anchors edits to ground
    truth."""
    from orchestrator.sdlc.codegen import _named_existing_files

    pkg = tmp_path / "src" / "orchestrator" / "sdlc"
    pkg.mkdir(parents=True)
    (pkg / "activities.py").write_text("class SDLCActivities:\n    pass\n", encoding="utf-8")
    spec = {
        "summary": "Modify src/orchestrator/sdlc/activities.py to add a notification.",
        "acceptance_criteria": ["src/orchestrator/sdlc/activities.py is modified"],
    }
    block = _named_existing_files(spec, tmp_path)
    assert "activities.py (current content" in block
    assert "class SDLCActivities:" in block
    assert "edits form" in block


def test_named_existing_files_empty_when_none_named(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _named_existing_files

    assert _named_existing_files({"summary": "create a brand new module"}, tmp_path) == ""


def test_named_existing_files_ignores_nonexistent_and_escapes(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _named_existing_files

    spec = {"summary": "edit src/orchestrator/ghost.py and src/../etc/passwd.py"}
    assert _named_existing_files(spec, tmp_path) == ""


async def test_convention_block_injected_into_prompts(tmp_path: Path) -> None:
    """G8: the repo's observed conventions reach the codegen prompt."""
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 110\n", encoding="utf-8")
    pkg = tmp_path / "src" / "myapp"
    pkg.mkdir(parents=True)
    module_body = (
        '"""Doc."""\n\nfrom __future__ import annotations\n\n\ndef f(x: int) -> int:\n    return x\n'
    )
    for i in range(3):
        (pkg / f"m{i}.py").write_text(module_body, encoding="utf-8")

    llm = _ScriptedLLM([_files_response({"feature.py": "x = 1\n"})])
    adapter = LLMCodegenAdapter(llm)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")

    prompt = llm.calls[0][-1].content
    assert "REPO CONVENTIONS" in prompt
    assert "from __future__ import annotations" in prompt
