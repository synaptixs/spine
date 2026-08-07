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

from orchestrator.core.llm import CompletionResult, Message, ToolCall, ToolSpec
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
        tool_choice: str | None = None,
    ) -> CompletionResult:
        _ = (model, response_format, json_object, temperature, max_tokens, tools, tool_choice)
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

    def test_falls_back_to_the_catalog_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Resolution now always names a model. It used to return None and let the
        adapter's own constant decide, which meant the model in play was a fact you
        could only discover by reading two modules."""
        from orchestrator.core.llm import catalog

        for name in ("SDLC_CODEGEN_MODEL", "ORCHESTRATOR_INTAKE_MODEL", "ORCHESTRATOR_MODEL"):
            monkeypatch.delenv(name, raising=False)
        assert resolve_codegen_model() == catalog.DEFAULT_MODEL


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


async def test_a_submission_with_no_files_is_retried_then_refused(tmp_path: Path) -> None:
    """Empty is recoverable, not fatal. The forced tool call means the model answered in
    the right shape and simply submitted nothing — the schema allows it, since `required`
    means present, not non-empty. A live run died here after a clean implement pass
    because author_tests came back empty and nothing asked it to try again."""
    llm = _ScriptedLLM(['{"summary": "I did nothing"}', '{"summary": "still nothing"}'])
    adapter = LLMCodegenAdapter(llm)

    with pytest.raises(CodegenError):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SDLC-1")

    assert len(llm.calls) == 2  # one corrective retry, then it gives up
    assert "SUBMITTED NO FILES" in llm.calls[1][1].content
    assert "I did nothing" in llm.calls[1][1].content  # its own words, fed back


async def test_a_retried_empty_submission_can_succeed(tmp_path: Path) -> None:
    llm = _ScriptedLLM(['{"summary": "nothing to do"}', _files_response({"src/a.py": "a = 1\n"})])

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")

    assert [Path(f).name for f in change.files] == ["a.py"]


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


@pytest.mark.asyncio
async def test_sql_layout_selects_sql_migration_prompts(tmp_path: Path) -> None:
    """A SQL layout switches implement/refine to the migration prompts and pins the
    migrations dir + dialect in the layout block. SQL has no author_tests leg."""
    from orchestrator.sdlc.layout import TargetLayout

    layout = TargetLayout(
        "shop", "migrations", "migrations", True, "new", language="sql", build_tool="postgres"
    )
    llm = _ScriptedLLM(
        [
            _files_response({"migrations/001_orders.sql": "CREATE TABLE orders (id INT PRIMARY KEY);"}),
            _files_response({"migrations/001_orders.sql": "CREATE TABLE orders (id INT PRIMARY KEY);"}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, layout=layout)
    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SQL-1")
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="SQL-1", failures='near "(": syntax error')

    assert "SQL migration" in llm.calls[0][0].content  # implement system prompt
    assert "dialect: **postgres**" in llm.calls[0][-1].content  # layout block pins the dialect
    assert "migrations/" in llm.calls[0][-1].content
    assert "failed to apply" in llm.calls[1][0].content  # refine system prompt
    assert change.files and change.files[0].endswith("migrations/001_orders.sql")


@pytest.mark.asyncio
async def test_sql_generate_validate_refine_loop(tmp_path: Path) -> None:
    """End-to-end B2: a scripted model generates a broken migration (duplicate
    table), the SqlTestRunner applies it to SQLite and fails, refine fixes it, and
    the rerun applies clean — the greenfield SQL loop, without an LLM."""
    from orchestrator.sdlc.layout import TargetLayout
    from orchestrator.sdlc.testrunner import SqlTestRunner

    layout = TargetLayout(
        "shop", "migrations", "migrations", True, "new", language="sql", build_tool="postgres"
    )
    llm = _ScriptedLLM(
        [
            # broken: the same table created twice → SQLite rejects the second.
            _files_response(
                {
                    "migrations/001_orders.sql": (
                        "CREATE TABLE orders (id INT PRIMARY KEY);\nCREATE TABLE orders (id INT);"
                    )
                }
            ),
            # refine: a single, valid CREATE.
            _files_response({"migrations/001_orders.sql": "CREATE TABLE orders (id INT PRIMARY KEY);"}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, layout=layout)
    runner = SqlTestRunner()

    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SQL-1")
    red = await runner.run(path=str(tmp_path))
    assert not red.passed  # duplicate table fails on apply

    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="SQL-1", failures=red.output)
    green = await runner.run(path=str(tmp_path))
    assert green.passed, green.output


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


async def test_the_empty_retry_is_spent_once(tmp_path: Path) -> None:
    """One corrective pass, not a loop: a model that submits nothing twice has said its
    piece, and a third call would just pay to watch it repeat."""
    empty = json.dumps({"files": [], "summary": "nothing"})
    llm = _ScriptedLLM([empty, empty])
    adapter = LLMCodegenAdapter(llm)
    with pytest.raises(CodegenError, match="no 'files'"):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")
    assert len(llm.calls) == 2


async def test_new_root_module_shadowing_stdlib_is_rejected(tmp_path: Path) -> None:
    """Run #15's failure mode: a new root-level statistics.py hijacks every
    stdlib `import statistics` in the repo. Refused deterministically."""
    bad = _files_response({"statistics.py": "def median(x):\n    return 0\n"})
    llm = _ScriptedLLM([bad])
    adapter = LLMCodegenAdapter(llm)
    with pytest.raises(CodegenError, match="shadow the Python standard-library"):
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")


async def test_a_package_shadowing_an_existing_module_is_rejected(tmp_path: Path) -> None:
    """The real case, reproduced: a run wanted `orchestrator.cli.regression`, so it created
    `orchestrator/cli/__init__.py` beside the existing `cli.py`. That does not extend the
    CLI — it hides it, and every command in the product stops resolving.

    The stdlib guard next door never fired, because `cli` is ours.
    """
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "orchestrator" / "cli.py").write_text("app = 1\n", encoding="utf-8")
    llm = _ScriptedLLM([_files_response({"orchestrator/cli/__init__.py": "app = 2\n"})])

    with pytest.raises(CodegenError, match="shadow the existing module 'orchestrator/cli.py'"):
        await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")


async def test_a_module_shadowing_an_existing_package_is_rejected(tmp_path: Path) -> None:
    """The mirror image, equally ambiguous: `pkg.py` beside `pkg/__init__.py`."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    llm = _ScriptedLLM([_files_response({"pkg.py": "X = 1\n"})])

    with pytest.raises(CodegenError, match="shadow the existing module"):
        await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")


async def test_a_new_package_beside_nothing_is_fine(tmp_path: Path) -> None:
    """The guard must not block ordinary new packages, which is most of greenfield work."""
    llm = _ScriptedLLM([_files_response({"newpkg/__init__.py": "X = 1\n"})])

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert change.files == [str(tmp_path / "newpkg" / "__init__.py")]


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


# ---- the design reaches the prompt (SSPN-28) --------------------------------


async def test_the_agreed_design_reaches_the_codegen_prompt(tmp_path: Path) -> None:
    """Research and design are worthless to a model that never sees them.

    Before this, `sdlc autorun` investigated a ticket, designed a change, wrote the design to
    disk, and then generated code as if neither had happened. The assertion is on the *prompt*
    rather than on the artifact, because writing a file nobody reads is exactly the bug.
    """
    llm = _ScriptedLLM([_files_response({"src/x.py": "x = 1\n"})])
    adapter = LLMCodegenAdapter(llm, design="## Approach\nEdit cli.py, not a new package.")

    await adapter.implement(
        spec={"title": "t", "summary": "s", "acceptance_criteria": ["a"]},
        path=str(tmp_path),
        issue_key="ENG-1",
    )

    prompt = "\n".join(m.content for m in llm.calls[0])
    assert "PLAN ALREADY AGREED" in prompt
    assert "Edit cli.py, not a new package." in prompt
    # Guidance, not gospel: the design was written before the code was read.
    assert "do not follow a plan you can see is wrong" in prompt


async def test_the_prompt_is_unchanged_without_a_design(tmp_path: Path) -> None:
    """`sdlc feature` standalone must build exactly the prompt it builds today."""
    with_design = _ScriptedLLM([_files_response({"src/x.py": "x = 1\n"})])
    without = _ScriptedLLM([_files_response({"src/x.py": "x = 1\n"})])
    spec = {"title": "t", "summary": "s", "acceptance_criteria": ["a"]}

    # Separate worktrees: codegen observes repo conventions from the directory it is given,
    # so a file written by the first run would change the second run's prompt for reasons
    # that have nothing to do with the design.
    (a := tmp_path / "a").mkdir()
    (b := tmp_path / "b").mkdir()
    await LLMCodegenAdapter(with_design, design="   ").implement(spec=spec, path=str(a), issue_key="ENG-1")
    await LLMCodegenAdapter(without).implement(spec=spec, path=str(b), issue_key="ENG-1")

    assert [m.content for m in with_design.calls[0]] == [m.content for m in without.calls[0]]
    assert "PLAN ALREADY AGREED" not in "\n".join(m.content for m in without.calls[0])


async def test_the_design_also_reaches_the_refine_prompt(tmp_path: Path) -> None:
    """A refine that has forgotten the plan re-solves the ticket its own way."""
    llm = _ScriptedLLM([_files_response({"src/x.py": "x = 2\n"})])
    adapter = LLMCodegenAdapter(llm, design="## Approach\nEdit cli.py, not a new package.")

    await adapter.refine(
        spec={"title": "t", "summary": "s", "acceptance_criteria": ["a"]},
        path=str(tmp_path),
        issue_key="ENG-1",
        failures="E   assert 1 == 2",
    )

    assert "Edit cli.py, not a new package." in "\n".join(m.content for m in llm.calls[0])


# ---- unparseable output gets one corrective retry (SSPN-33) -----------------


async def test_malformed_json_is_retried_once_and_recovers(tmp_path: Path) -> None:
    """The failure that killed the first real end-to-end run. The model emitted a string
    containing an unescaped quote 2,294 characters in; there was no retry, so the run died.
    Models routinely fix their own JSON when shown where it broke."""
    broken = '{"files": [{"path": "src/x.py", "content": "he said "hi""}]}'
    good = _files_response({"src/x.py": "x = 1\n"})
    llm = _ScriptedLLM([broken, good])

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert change.files == [str(tmp_path / "src" / "x.py")]
    assert len(llm.calls) == 2
    # The retry names the break rather than saying "try again".
    retry_prompt = "\n".join(m.content for m in llm.calls[1])
    assert "NOT VALID JSON" in retry_prompt
    assert "position" in retry_prompt


async def test_a_second_parse_failure_raises(tmp_path: Path) -> None:
    """One retry, never a loop: a model that cannot produce JSON twice will not on the third."""
    broken = '{"files": [{"path": "src/x.py", "content": "oops"'
    llm = _ScriptedLLM([broken, broken])

    with pytest.raises(CodegenError, match="not a JSON object"):
        await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert len(llm.calls) == 2


async def test_the_parse_retry_does_not_stack_with_the_edit_repair(tmp_path: Path) -> None:
    """Two recoveries for one generation would double the cost of a bad response. A parse
    failure carries no edit paths, so only the parse retry fires."""
    broken = "not json at all"
    good = _files_response({"src/x.py": "x = 1\n"})
    llm = _ScriptedLLM([broken, good])

    await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert len(llm.calls) == 2  # not 3


async def test_a_parse_failure_after_an_edit_repair_still_retries(tmp_path: Path) -> None:
    """The gap the first live-ish run fell through: failed edits took the repair path, the
    repaired response was unparseable, and the second apply was outside the guard — so it
    raised. One retry budget, whichever way the first attempt failed."""
    (tmp_path / "existing.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    edits_that_fail = json.dumps(
        {
            "files": [{"path": "existing.py", "edits": [{"find": "NOT PRESENT ANYWHERE", "replace": "x"}]}],
            "summary": "edit",
        }
    )
    unparseable = "definitely not json"

    llm = _ScriptedLLM([edits_that_fail, unparseable, unparseable])

    with pytest.raises(CodegenError):
        await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    # Three calls: the anchor miss is corrected, then the parse failure gets its own
    # correction — a different kind of wrong deserves its own attempt — and the *second*
    # parse failure raises, because that kind is now spent. The guard the test was written
    # for still holds: the later applies are inside it.
    assert len(llm.calls) == 3


async def test_two_concatenated_json_documents_are_merged(tmp_path: Path) -> None:
    """Observed twice on real runs: the model emits one document per file and concatenates
    them, so the decoder stops at the second with `Extra data`. Every document is valid and
    every one is a files-object — stitching them is free where a retry costs a call."""
    two_documents = (
        '{"files": [{"path": "src/a.py", "content": "a = 1\\n"}], "summary": "first"}, '
        '{"files": [{"path": "src/b.py", "content": "b = 2\\n"}], "summary": "second"}'
    )
    llm = _ScriptedLLM([two_documents])

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert sorted(Path(f).name for f in change.files) == ["a.py", "b.py"]
    assert len(llm.calls) == 1  # no retry needed


async def test_a_files_object_next_to_something_else_is_not_merged(tmp_path: Path) -> None:
    """Strict on purpose: merging a files-object with anything else would be guessing."""
    mixed = '{"files": [{"path": "src/a.py", "content": "a = 1\\n"}]}, {"notes": "hello"}'
    good = _files_response({"src/a.py": "a = 1\n"})
    llm = _ScriptedLLM([mixed, good])

    await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="E-1")

    assert len(llm.calls) == 2  # fell back to the corrective retry


class _ToolCallingLLM:
    """Answers with a forced ``submit_files`` call, and records how it was asked."""

    def __init__(self, arguments: dict[str, Any], *, text: str = "", call_tool: bool = True) -> None:
        self._arguments = arguments
        self._text = text
        self._call_tool = call_tool
        self.tools_offered: list[ToolSpec] = []
        self.tool_choice: str | None = None

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        response_format: type[BaseModel] | None = None,
        json_object: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        _ = (messages, model, response_format, json_object, temperature, max_tokens)
        self.tools_offered = list(tools or [])
        self.tool_choice = tool_choice
        calls = (ToolCall("c1", "submit_files", self._arguments),) if self._call_tool else ()
        return CompletionResult(
            text=self._text,
            model="fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
            tool_calls=calls,
        )


async def test_codegen_forces_the_submit_files_tool(tmp_path: Path) -> None:
    """The output contract is a tool the provider enforces, not a request in the prompt."""
    llm = _ToolCallingLLM({"files": [{"path": "src/a.py", "content": "a = 1\n"}], "summary": "ok"})

    await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="T-1")

    assert llm.tool_choice == "submit_files"
    assert [t.name for t in llm.tools_offered] == ["submit_files"]


async def test_tool_call_arguments_win_over_prose(tmp_path: Path) -> None:
    """The live failure this exists for: the model narrates ("I'll analyze the existing
    code...") and appends a PR blurb after the payload. Every one of those runs died in the
    JSON parser. With the call forced, the prose is just the text field and is ignored."""
    llm = _ToolCallingLLM(
        {"files": [{"path": "src/a.py", "content": "a = 1\n"}], "summary": "ok"},
        text="I'll analyze the existing code to understand the current structure.",
    )

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="T-2")

    assert [Path(f).name for f in change.files] == ["a.py"]
    assert change.summary == "ok"


async def test_falls_back_to_text_when_the_tool_is_ignored(tmp_path: Path) -> None:
    """A provider with no tool support (a local Ollama model) still answers in text, and
    that path stays exactly as it was — the forced tool is an addition, not a replacement."""
    llm = _ToolCallingLLM({}, text=_files_response({"src/a.py": "a = 1\n"}), call_tool=False)

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="T-3")

    assert [Path(f).name for f in change.files] == ["a.py"]


# ---- a file too big to show whole must still be shown (SSPN-14) ----


def _big_module(target_line: str, size: int = 120_000) -> str:
    """A module larger than the context pool, with one distinctive line in the middle."""
    filler = "\n".join(f"def filler_{i}() -> int:\n    return {i}\n" for i in range(size // 40))
    half = len(filler) // 2
    return filler[:half] + f"\n{target_line}\n" + filler[half:]


async def test_a_file_larger_than_the_budget_is_excerpted_not_dropped(tmp_path: Path) -> None:
    """The live failure: `cli.py` is 100 KB against a 40 KB pool, so the repair block said
    "below is the CURRENT EXACT content" and then appended nothing. Every retry re-guessed
    the anchor and the run died having never seen a byte of the file it was editing."""
    from orchestrator.sdlc.excerpt import _excerpt_files

    # An explicit pool, not the global cap: this test is about what happens when a file
    # exceeds its budget, and it must keep testing that however large the cap becomes.
    pool = 40_000
    target = "def render_contract_types(schema: dict) -> str:"
    (tmp_path / "cli.py").write_text(_big_module(target), encoding="utf-8")

    block = _excerpt_files(tmp_path, ["cli.py"], budget=pool, anchors_by_path={"cli.py": [target]}, label="x")

    assert block, "an oversized file must still reach the prompt"
    assert target in block, "the window must cover the line the model was aiming at"
    assert len(block) <= pool * 2  # bounded by the pool it was given
    assert "lines not shown" in block  # and honest about being partial


async def test_a_near_miss_anchor_still_finds_its_neighbourhood(tmp_path: Path) -> None:
    """A `find` fails on whitespace or a renamed identifier as easily as on being wrong.
    The window should still land where the model was reaching."""
    from orchestrator.sdlc.excerpt import _excerpt_files

    pool = 40_000  # explicit, so the global cap can move without disarming this
    target = "def render_contract_types(schema: dict) -> str:"
    (tmp_path / "cli.py").write_text(_big_module(target), encoding="utf-8")

    # What the model guessed: right function, wrong signature.
    guess = "def render_contract_types(schema):"
    block = _excerpt_files(tmp_path, ["cli.py"], budget=pool, anchors_by_path={"cli.py": [guess]}, label="x")

    assert target in block


async def test_a_small_file_hands_its_unused_budget_to_a_big_one(tmp_path: Path) -> None:
    """Smallest first, equal share of what remains: the old loop dropped whatever came
    after the first file too big to fit, whatever its size."""
    from orchestrator.sdlc.excerpt import _excerpt_files

    target = "def needle_function() -> None:"
    (tmp_path / "small.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "big.py").write_text(_big_module(target), encoding="utf-8")

    block = _excerpt_files(
        tmp_path,
        ["big.py", "small.py"],
        budget=30_000,
        anchors_by_path={"big.py": [target]},
        label="x",
    )

    assert "x = 1" in block  # the small file, whole
    assert target in block  # and the big one still got a window


async def test_the_repair_block_never_promises_content_it_does_not_supply(tmp_path: Path) -> None:
    """The bug in one assertion: the prompt said "copy every `find` VERBATIM from this
    content" above an empty block."""
    from orchestrator.sdlc.codegen import CodegenError

    target = "def render_contract_types(schema: dict) -> str:"
    (tmp_path / "cli.py").write_text(_big_module(target), encoding="utf-8")
    adapter = LLMCodegenAdapter(_ScriptedLLM([]))
    exc = CodegenError(
        "changes need a repair pass",
        failed_edit_paths=["cli.py"],
        failed_anchors={"cli.py": [target]},
    )

    block = adapter._repair_block(exc, tmp_path)

    assert "VERBATIM" in block
    assert target in block, "the promise of exact content must come with exact content"


async def test_codegen_is_shown_the_files_the_design_names(tmp_path: Path) -> None:
    """Three runs opened with "placeholder — need to read the actual file first" and it was
    the literal truth: paths came only from the spec text, this ticket's spec named none, and
    codegen was handed zero bytes of the code it was asked to change."""
    from orchestrator.sdlc.codegen import _named_existing_files

    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "display.py").write_text("def render() -> str:\n    return 'names only'\n", encoding="utf-8")
    spec = {"summary": "Show argument types in the contracts output.", "acceptance_criteria": ["shows type"]}
    design = "Approach: add a type label in src/app/display.py at render()."

    assert _named_existing_files(spec, tmp_path, "") == ""  # the bug: nothing at all
    with_design = _named_existing_files(spec, tmp_path, design)
    assert "def render() -> str:" in with_design


async def test_a_spec_that_names_its_files_still_wins(tmp_path: Path) -> None:
    """When a ticket does name its files, that is the sharpest statement of intent there is."""
    from orchestrator.sdlc.codegen import _named_existing_files

    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "named.py").write_text("A = 1\n", encoding="utf-8")
    (pkg / "designed.py").write_text("B = 2\n", encoding="utf-8")
    spec = {"summary": "Change src/app/named.py.", "acceptance_criteria": []}

    block = _named_existing_files(spec, tmp_path, "also touch src/app/designed.py")

    assert block.index("named.py") < block.index("designed.py")


async def test_a_type_error_pulls_in_the_definition_it_names(tmp_path: Path) -> None:
    """The loop that could not converge: mypy said `"MCPToolHandler" has no attribute
    "input_schema"`, the model guessed `handler.tool`, was rejected identically, and spent its
    whole budget proposing names for a class no stage had ever shown it."""

    class _Grounder:
        def __init__(self) -> None:
            self.asked: list[str] = []

        def context_for_spec(self, spec: dict[str, Any]) -> str:
            return ""

        def context_for_symbols(self, names: list[str]) -> str:
            self.asked = names
            return "### Type `MCPToolHandler`\nclass MCPToolHandler:\n    self._qualified = ...\n"

    grounder = _Grounder()
    adapter = LLMCodegenAdapter(_ScriptedLLM([]), grounder=grounder)
    errors = 'src/orchestrator/cli.py:1126: error: "MCPToolHandler" has no attribute "tool"'

    block = adapter._definitions_for(errors, tmp_path)

    assert "MCPToolHandler" in grounder.asked
    assert "class MCPToolHandler" in block


async def test_a_failure_naming_nothing_adds_no_definitions(tmp_path: Path) -> None:
    adapter = LLMCodegenAdapter(_ScriptedLLM([]))
    assert adapter._definitions_for("E   assert 1 == 2", tmp_path) == ""


async def test_author_tests_is_shown_how_this_repo_tests_the_module(tmp_path: Path) -> None:
    """Told to reach the entry point, a run reached for CliRunner — right instinct — then
    spent its budget guessing the Typer app was named `cli`, and edited *production* cli.py
    to add an alias so its own test would import. The answer was in tests/test_cli.py."""
    from orchestrator.sdlc.codegen import _existing_test_examples

    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app" / "cli.py").write_text("entry = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_cli.py").write_text(
        "from app.cli import entry\n\n\ndef test_entry() -> None:\n    assert entry == 1\n",
        encoding="utf-8",
    )

    block = _existing_test_examples({"summary": "change it"}, tmp_path, "touch src/app/cli.py")

    assert "from app.cli import entry" in block


async def test_a_file_that_only_mentions_the_module_does_not_count(tmp_path: Path) -> None:
    """Mentioning a module is not testing it: a wikilink test naming `orchestrator.cli.md`
    six times outranked tests/test_cli.py, which imports the app."""
    from orchestrator.sdlc.codegen import _exercises_module

    assert _exercises_module("from orchestrator.cli import app\n", "orchestrator.cli") > 0
    assert _exercises_module('monkeypatch.setattr("orchestrator.cli.thing", x)\n', "orchestrator.cli") > 0
    assert _exercises_module('doc = "orchestrator.cli.md"\n' * 6, "orchestrator.cli") == 0


async def test_no_existing_tests_is_not_an_error(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _existing_test_examples

    assert _existing_test_examples({"summary": "x"}, tmp_path, "touch src/app/nothing.py") == ""


async def test_the_layout_permits_editing_the_repo_s_own_docs(tmp_path: Path) -> None:
    """The layout block is stamped "authoritative — overrides any default path guidance"
    and leads every phase prompt, so its absolute ban on files outside src/ and tests/
    beat the revise prompt's explicit permission to write documentation. A run said so in
    its own summary: "USER_GUIDE.md is outside the allowed src/tests paths so the doc note
    was not added" — while the judge was failing the ticket for the missing doc."""
    from orchestrator.sdlc.codegen import LLMCodegenAdapter
    from orchestrator.sdlc.layout import TargetLayout

    layout = TargetLayout(
        package_name="app",
        source_dir="src/app",
        tests_dir="tests",
        src_layout=True,
        mode="existing",
        language="python",
    )
    block = LLMCodegenAdapter(_ScriptedLLM([]), layout=layout)._layout_block()

    assert "MAY edit files that already exist" in block
    assert "USER_GUIDE" in block
    # The part that was right stays: invented paths are still refused.
    assert "do NOT invent unrelated top-level paths" in block


# ---- generated Python that does not parse never reaches disk (SSPN-14) ----


async def test_unparseable_generated_python_is_refused_and_repaired(tmp_path: Path) -> None:
    """A live run wrote a test file ending in a stray `</content>` — markup that leaked
    out of the tool payload into the file body. The only symptom was `rc=2` from pytest
    three stages later, buried in an importlib traceback; refine spent both attempts on
    that and never found the line."""
    broken = _files_response({"src/a.py": "def f():\n    return 1\n</content>\n"})
    good = _files_response({"src/a.py": "def f():\n    return 1\n"})
    llm = _ScriptedLLM([broken, good])

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")

    assert len(llm.calls) == 2  # the syntax error bought a corrective retry
    assert "</content>" not in (tmp_path / "src" / "a.py").read_text()
    assert [Path(f).name for f in change.files] == ["a.py"]


async def test_the_repair_names_the_line(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _python_syntax_error

    err = _python_syntax_error("tests/test_x.py", "def f():\n    return 1\n</content>\n")

    assert "line 3" in err and "</content>" in err


async def test_a_broken_file_is_never_written(tmp_path: Path) -> None:
    """Not written, not left behind: an unparseable module turns the whole suite into a
    collection error, so every later stage reads an unrelated traceback instead."""
    llm = _ScriptedLLM([_files_response({"src/bad.py": "def f(\n"})] * 2)

    with pytest.raises(CodegenError):
        await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-2")

    assert not (tmp_path / "src" / "bad.py").exists()


async def test_non_python_files_are_not_syntax_checked(tmp_path: Path) -> None:
    """Markdown is not Python. The doc a criterion demands must still get written."""
    llm = _ScriptedLLM([_files_response({"USER_GUIDE.md": "# Step 9\n\nnot <<< python\n"})])

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-3")

    assert [Path(f).name for f in change.files] == ["USER_GUIDE.md"]


async def test_an_empty_retry_does_not_consume_the_syntax_retry(tmp_path: Path) -> None:
    """The live sequence exactly: the model submitted no files, spent the one shared
    retry recovering from that, then produced a file with a stray `</content>` — and the
    syntax error, the very thing the retry exists for, had no attempt left."""
    empty = '{"summary": "nothing yet"}'
    broken = _files_response({"src/a.py": "def f():\n    return 1\n</content>\n"})
    good = _files_response({"src/a.py": "def f():\n    return 1\n"})
    llm = _ScriptedLLM([empty, broken, good])

    change = await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")

    assert len(llm.calls) == 3  # empty → corrected, syntax → corrected, then clean
    assert [Path(f).name for f in change.files] == ["a.py"]
    assert "</content>" not in (tmp_path / "src" / "a.py").read_text()


async def test_the_same_failure_twice_still_stops(tmp_path: Path) -> None:
    """Per-kind, not unlimited: a kind that fails twice is looping, not fixing."""
    broken = _files_response({"src/a.py": "def f(\n"})
    llm = _ScriptedLLM([broken, broken])

    with pytest.raises(CodegenError):
        await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-2")

    assert len(llm.calls) == 2  # one correction for that kind, then it gives up


def test_each_failure_kind_is_classified_apart() -> None:
    from orchestrator.sdlc.codegen import _failure_kind

    assert _failure_kind(CodegenError("x", parse_detail="bad json")) == "parse"
    assert _failure_kind(CodegenError("x", syntax_errors=["a.py: line 1"])) == "syntax"
    assert _failure_kind(CodegenError("x", failed_edit_paths=["a.py"])) == "anchors"
    assert _failure_kind(CodegenError("x", empty_summary="did nothing")) == "empty"


async def test_a_big_file_no_longer_hides_the_small_ones_from_refine(tmp_path: Path) -> None:
    """The live failure, stated by the model itself: "the failure is in models.py … but
    that file's current content was not provided, so I have no verbatim anchor to edit it
    safely." It was right — `cli.py` was 91 KB against a 40 KB budget, the loop hit
    `break`, and every file after it was dropped. `models.py` was 1.8 KB."""
    from orchestrator.sdlc.codegen import _MAX_CONTEXT_BYTES

    for rel, body in (
        ("small_first.py", "a = 1\n"),
        ("huge.py", "# " + "x" * (_MAX_CONTEXT_BYTES + 5_000)),
        ("tiny_last.py", "class MCPTool:\n    server: str\n"),
    ):
        target = tmp_path / rel
        target.write_text(body, encoding="utf-8")

    adapter = LLMCodegenAdapter(_ScriptedLLM([]))
    adapter._written[tmp_path.resolve()] = [
        tmp_path / r for r in ("small_first.py", "huge.py", "tiny_last.py")
    ]

    block = adapter._session_files(tmp_path, include_tests=True)

    # The file *after* the oversized one is the whole point.
    assert "tiny_last.py" in block
    assert "class MCPTool" in block
    assert "small_first.py" in block
    assert "huge.py" in block  # windowed, not dropped


async def test_refine_windows_land_on_what_the_traceback_names(tmp_path: Path) -> None:
    """On a file too big to show whole, the excerpt should cover the failing line rather
    than the top of the module."""
    from orchestrator.sdlc.codegen import _MAX_CONTEXT_BYTES

    needle = "def the_function_that_broke() -> None:"
    filler = "\n".join(f"def pad_{i}() -> int:\n    return {i}\n" for i in range(_MAX_CONTEXT_BYTES // 18))
    half = len(filler) // 2
    (tmp_path / "big.py").write_text(filler[:half] + f"\n{needle}\n" + filler[half:], encoding="utf-8")

    adapter = LLMCodegenAdapter(_ScriptedLLM([]))
    adapter._written[tmp_path.resolve()] = [tmp_path / "big.py"]

    block = adapter._session_files(tmp_path, include_tests=True, anchors=["the_function_that_broke"])

    assert needle in block


# --- the classifier and the corrector must agree on priority ------------------------
#
# `_failure_kind` decides which kind's one correction is being spent; `_corrective_suffix`
# decides what the model is actually told. When they disagree, an attempt is charged to one
# kind and spent advising about another. A live run died that way: a stray `</content>` in a
# new file arrived alongside an edit whose anchor missed, the kind was recorded as "syntax",
# and the model was handed the anchor-repair block — so it was never told to stop emitting
# markup wrappers, and by the next failure "syntax" was already marked used.


async def test_a_syntax_error_gets_syntax_advice_even_alongside_a_failed_edit(
    tmp_path: Path,
) -> None:
    from orchestrator.sdlc.codegen import CodegenError, _failure_kind

    exc = CodegenError(
        "both at once",
        syntax_errors=["t.py: line 219: invalid syntax — '</content>'"],
        failed_edit_paths=["src/a.py"],
    )
    assert _failure_kind(exc) == "syntax"

    gen = LLMCodegenAdapter(_ScriptedLLM([]))
    suffix = gen._corrective_suffix(exc, tmp_path)

    assert suffix is not None
    assert "DOES NOT PARSE" in suffix, "the advice must address the kind that was charged"
    assert "</content>" in suffix or "`<content>` tags" in suffix


def test_every_failure_kind_has_advice_that_matches_it(tmp_path: Path) -> None:
    """Each kind, alone, must produce advice — and the same kind the classifier names."""
    from orchestrator.sdlc.codegen import CodegenError, _failure_kind

    gen = LLMCodegenAdapter(_ScriptedLLM([]))
    cases = [
        ("parse", CodegenError("x", parse_detail="not an object"), "JSON"),
        ("syntax", CodegenError("x", syntax_errors=["a.py: line 1: bad"]), "DOES NOT PARSE"),
        ("anchors", CodegenError("x", failed_edit_paths=["a.py"]), "CURRENT"),
        ("empty", CodegenError("x", empty_summary="nothing submitted"), "submit"),
    ]
    for expected_kind, exc, marker in cases:
        assert _failure_kind(exc) == expected_kind
        suffix = gen._corrective_suffix(exc, tmp_path)
        assert suffix is not None, f"{expected_kind} has no advice"
        assert marker.lower() in suffix.lower(), f"{expected_kind} advice does not address it"


# --- a partially-applied attempt can be repaired (SSPN-40) --------------------------
#
# `apply_files` is per-file atomic, not per-batch: a successful file is written before the
# rest are attempted. When a later file fails, the earlier ones are already on disk — so a
# repair that says "re-emit the full JSON object" without saying which landed guarantees the
# model resends `find` snippets its own previous attempt replaced. A live run (SSPN-39)
# produced a complete, correct change and failed anyway on `edit 0 'find' text not found`.


def _one_file(rel: str, find: str, replace: str) -> dict[str, Any]:
    return {"path": rel, "edits": [{"find": find, "replace": replace}]}


def test_a_failure_reports_which_files_already_landed(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import CodegenError, apply_files

    (tmp_path / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("OTHER = 2\n", encoding="utf-8")

    with pytest.raises(CodegenError) as exc:
        apply_files(
            [
                _one_file("good.py", "VALUE = 1", "VALUE = 99"),
                _one_file("bad.py", "TEXT THAT IS NOT THERE", "x"),
            ],
            tmp_path,
            written_tracker={},
            grounded=False,
        )

    assert exc.value.applied_paths == ["good.py"]
    assert exc.value.failed_edit_paths == ["bad.py"]
    # And it really is on disk — which is exactly why the retry must not resend it.
    assert (tmp_path / "good.py").read_text() == "VALUE = 99\n"


def test_the_repair_names_what_landed_and_why_the_old_anchors_fail(tmp_path: Path) -> None:
    """Was `..._tells_the_model_not_to_resend_what_landed`, asserting a blanket ban.

    That ban was too absolute: it also forbade *revising* an applied file, and a run needed
    exactly that when it renamed helpers in one file and could not update the other. The
    obligation that survives is narrower — name the files, and say why the previous
    attempt's anchors no longer match.
    """
    from orchestrator.sdlc.codegen import CodegenError

    (tmp_path / "bad.py").write_text("OTHER = 2\n", encoding="utf-8")
    (tmp_path / "good.py").write_text("VALUE = 1\n", encoding="utf-8")
    exc = CodegenError(
        "changes need a repair pass",
        failed_edit_paths=["bad.py"],
        applied_paths=["good.py"],
    )

    block = LLMCodegenAdapter(_ScriptedLLM([]))._repair_block(exc, tmp_path)

    assert "ALREADY WRITTEN" in block
    assert "good.py" in block
    assert "no longer match" in block


def test_no_already_written_note_when_nothing_landed(tmp_path: Path) -> None:
    """The common case is unchanged — no confusing note about an empty set."""
    from orchestrator.sdlc.codegen import CodegenError

    (tmp_path / "bad.py").write_text("OTHER = 2\n", encoding="utf-8")
    exc = CodegenError("changes need a repair pass", failed_edit_paths=["bad.py"])

    block = LLMCodegenAdapter(_ScriptedLLM([]))._repair_block(exc, tmp_path)

    assert "ALREADY WRITTEN" not in block


# --- a stub is not progress (SSPN-45) -----------------------------------------------
#
# A run wrote a file literally named PLACEHOLDER containing `x`, and the refine loop
# counted it as a file change. Its stop condition is "no file changes", so a model with
# nothing to say kept the loop alive and spent two of three attempts on it while the real
# failure went unfixed. Dropping the stub at the write is enough: the loop needs no change.


def test_a_placeholder_file_is_not_written(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import apply_files

    change = apply_files(
        [
            {"path": "PLACEHOLDER", "content": "x"},
            {"path": "real.py", "content": "VALUE = 1\n"},
        ],
        tmp_path,
        written_tracker={},
        grounded=False,
    )

    assert not (tmp_path / "PLACEHOLDER").exists()
    assert [Path(f).name for f in change.files] == ["real.py"]


@pytest.mark.parametrize("rel", ["PLACEHOLDER", "placeholder.py", "TODO.md", "src/pkg/stub.py"])
def test_placeholder_names_are_rejected_whatever_they_contain(tmp_path: Path, rel: str) -> None:
    from orchestrator.sdlc.codegen import _is_placeholder

    assert _is_placeholder(rel, "def real_looking_code() -> int:\n    return 1\n")


@pytest.mark.parametrize("rel", ["__init__.py", "src/pkg/__init__.py", "py.typed", ".gitkeep"])
def test_legitimately_empty_files_are_kept(tmp_path: Path, rel: str) -> None:
    """An empty __init__.py is a real file — the size rule must never catch it."""
    from orchestrator.sdlc.codegen import _is_placeholder

    assert not _is_placeholder(rel, "")


def test_a_trivially_short_body_is_a_placeholder() -> None:
    from orchestrator.sdlc.codegen import _is_placeholder

    assert _is_placeholder("thing.py", "x")
    assert _is_placeholder("thing.py", "  \n ")
    assert not _is_placeholder("thing.py", "X = 1\n")


def test_placeholders_only_is_recoverable_not_a_dead_end(tmp_path: Path) -> None:
    """It is the same shape as submitting nothing, so it gets the same corrective retry."""
    from orchestrator.sdlc.codegen import CodegenError, apply_files

    with pytest.raises(CodegenError) as exc:
        apply_files([{"path": "PLACEHOLDER", "content": "x"}], tmp_path, written_tracker={}, grounded=False)

    assert "placeholder" in str(exc.value).lower()
    assert exc.value.empty_summary, "must route to the empty-submission retry"


# --- the fixtures see the types they construct (SSPN-42) ----------------------------
#
# Three consecutive runs produced correct source and a broken test module, each time by
# constructing a type the spec never named: CompletionResult with an invented `usage`
# kwarg, then CompletionResult missing its four required fields, then FactStore() without
# `batch`. Writing one signature into the spec did not help — the next run failed on a
# different type. The names were always right there in the source's own imports.


def test_imported_type_names_finds_what_a_fixture_would_build(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _imported_type_names

    src = tmp_path / "specs.py"
    src.write_text(
        "from orchestrator.core.llm import CompletionResult, LLMClient, Message, ToolSpec\n"
        "from orchestrator.pkg.store import FactStore\n"
        "from pathlib import Path\n"
        "import json\n"
        "\n"
        "def helper() -> int:\n    return 1\n",
        encoding="utf-8",
    )

    names = _imported_type_names(tmp_path, [src])

    # Exactly the three that bit us, plus the other imported classes.
    assert "CompletionResult" in names
    assert "FactStore" in names
    assert "ToolSpec" in names
    # Not the module-level noise a fixture never constructs.
    assert "json" not in names


def test_lowercase_imports_are_left_out(tmp_path: Path) -> None:
    """A fixture builds types; pulling in every imported function would swamp the budget."""
    from orchestrator.sdlc.codegen import _imported_type_names

    src = tmp_path / "m.py"
    src.write_text("from orchestrator.util import load_thing, save_thing\n", encoding="utf-8")

    assert _imported_type_names(tmp_path, [src]) == []


def test_an_unparseable_source_does_not_break_the_stage(tmp_path: Path) -> None:
    """author_tests runs while files may be mid-edit; a syntax error is not fatal here."""
    from orchestrator.sdlc.codegen import _imported_type_names

    bad = tmp_path / "broken.py"
    bad.write_text("from x import (\n", encoding="utf-8")
    good = tmp_path / "ok.py"
    good.write_text("from a.b import Thing\n", encoding="utf-8")

    assert _imported_type_names(tmp_path, [bad, good]) == ["Thing"]


def test_aliased_imports_use_the_local_name(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _imported_type_names

    src = tmp_path / "m.py"
    src.write_text("from a.b import Thing as Renamed\n", encoding="utf-8")

    assert _imported_type_names(tmp_path, [src]) == ["Renamed"]


def test_test_files_are_not_mined_for_their_own_imports(tmp_path: Path) -> None:
    """The question is what the code *under test* builds, not what an existing test built."""
    from orchestrator.sdlc.codegen import _is_test_file

    assert _is_test_file(Path("tests/sdlc/test_codegen.py"))
    assert _is_test_file(Path("src/pkg/thing_test.py"))
    assert not _is_test_file(Path("src/orchestrator/intake/specs.py"))


# --- "no tests" is an answer, or a skipped job, depending on the change (SSPN-47) ----
#
# author_tests is unconditional and no spec can turn it off. On a documentation-only change
# it first answered correctly — "no tests submitted: this is a prose edit" — and that was
# treated as an empty submission and retried, and the retry invented a test module for a
# paragraph and corrupted it with markup. A finished, correct change was discarded.
#
# The rule has to hold both ways: empty is valid with no testable source, and a skipped job
# with one.


@pytest.mark.parametrize(
    "written,expected",
    [
        ([Path("README.md")], False),
        ([Path("docs/guide.rst"), Path("pyproject.toml")], False),
        ([Path("src/pkg/thing.py")], True),
        ([Path("README.md"), Path("src/pkg/thing.py")], True),
        ([Path("tests/test_thing.py")], False),
        ([], False),
    ],
)
def test_testable_source_is_recognised(written: list[Path], expected: bool) -> None:
    from orchestrator.sdlc.codegen import _has_testable_source

    assert _has_testable_source(written) is expected


def test_a_test_file_alone_is_not_testable_source() -> None:
    """Otherwise author_tests would demand tests for the tests it just wrote."""
    from orchestrator.sdlc.codegen import _has_testable_source

    assert not _has_testable_source([Path("tests/sdlc/test_codegen.py")])


async def test_a_docs_only_change_may_submit_no_tests(tmp_path: Path) -> None:
    """The SSPN-46 case: a README edit that correctly has nothing to test."""
    from orchestrator.sdlc.codegen import LLMCodegenAdapter

    (tmp_path / "README.md").write_text("# Doc\n", encoding="utf-8")
    llm = _ScriptedLLM([json.dumps({"summary": "documentation-only; nothing to test", "files": []})])
    adapter = LLMCodegenAdapter(llm)
    adapter._written[tmp_path.resolve()] = [tmp_path / "README.md"]

    change = await adapter.author_tests(spec={"title": "t"}, path=str(tmp_path), issue_key="S-1")

    assert change.files == []
    assert "nothing to test" in change.summary


async def test_a_source_change_that_submits_no_tests_is_still_refused(tmp_path: Path) -> None:
    """The rule must not have loosened for real code."""
    from orchestrator.sdlc.codegen import CodegenError, LLMCodegenAdapter

    (tmp_path / "thing.py").write_text("X = 1\n", encoding="utf-8")
    llm = _ScriptedLLM([json.dumps({"summary": "nothing to do", "files": []})] * 6)
    adapter = LLMCodegenAdapter(llm)
    adapter._written[tmp_path.resolve()] = [tmp_path / "thing.py"]

    with pytest.raises(CodegenError):
        await adapter.author_tests(spec={"title": "t"}, path=str(tmp_path), issue_key="S-1")


# --- the repair path must not crash (regression from #150) --------------------------
#
# `applied_paths` names what already landed so the repair retry knows not to resend it.
# Computing it with a bare `Path.relative_to` raised on macOS, where /tmp is a symlink to
# /private/tmp: the written paths come back resolved and the worktree root does not, so
# nothing is "in the subpath of" anything. The ValueError killed the whole run — and it
# fired only when an attempt partially succeeded, which is the exact case this information
# exists to rescue.


def test_written_paths_survive_a_symlinked_root(tmp_path: Path) -> None:
    """The macOS /tmp -> /private/tmp shape, built explicitly rather than assumed."""
    from orchestrator.sdlc.codegen import _relative_paths

    real = tmp_path / "real"
    (real / "src").mkdir(parents=True)
    (real / "src" / "a.py").write_text("X = 1\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real)

    # root as the symlink, the written path as the resolved location — the failing shape.
    assert _relative_paths([str(real / "src" / "a.py")], link) == ["src/a.py"]


def test_a_path_outside_the_root_is_reported_not_raised(tmp_path: Path) -> None:
    """Worth reporting; never worth losing the run over."""
    from orchestrator.sdlc.codegen import _relative_paths

    outside = tmp_path.parent / "elsewhere.py"

    result = _relative_paths([str(outside)], tmp_path)

    assert result == [str(outside)]


def test_ordinary_paths_are_relative(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _relative_paths

    (tmp_path / "pkg").mkdir()
    target = tmp_path / "pkg" / "thing.py"
    target.write_text("Y = 2\n", encoding="utf-8")

    assert _relative_paths([str(target)], tmp_path) == ["pkg/thing.py"]


# --- a new module is not a "parallel module" (SSPN-49) -------------------------------
#
# The rule read "when the SPEC names existing files, change THOSE files; do not create a
# parallel module instead." It was added after a run wrote a helper beside a file and never
# wired it in — a real failure. But as written it forbids the ordinary shape of an
# extract-a-wrapper refactor, and SSPN-49's spec asks for exactly that ("one place that
# wraps the request"). Three runs on that ticket submitted zero files rather than choose
# between the spec and the rule.
#
# These assert on prompt *content*, which is a weak test of a real change: a model's
# behaviour cannot be unit-tested. What they do catch is the prohibition coming back, or the
# obligation it was protecting being dropped.


def test_the_prompt_still_requires_the_named_files_to_be_edited() -> None:
    """The obligation the old rule was really protecting: do not leave them untouched."""
    from orchestrator.sdlc.codegen import _IMPLEMENT_SYSTEM

    assert "MUST appear in your `files` with `edits`" in _IMPLEMENT_SYSTEM
    assert "leaving the named files untouched" in _IMPLEMENT_SYSTEM


def test_the_prompt_no_longer_forbids_a_new_module_outright() -> None:
    from orchestrator.sdlc.codegen import _IMPLEMENT_SYSTEM

    assert "do not create a parallel module instead" not in _IMPLEMENT_SYSTEM
    assert "MAY add a new module" in _IMPLEMENT_SYSTEM


def test_the_stdlib_shadow_rule_is_untouched() -> None:
    """Adjacent in the same string, and a genuine guard — it must survive the edit."""
    from orchestrator.sdlc.codegen import _IMPLEMENT_SYSTEM

    assert "Never name a new top-level module after a Python standard-library module" in _IMPLEMENT_SYSTEM
    assert "Every new file must be complete and syntactically valid" in _IMPLEMENT_SYSTEM


def test_only_the_python_prompt_carries_this_rule() -> None:
    """No other language prompt has it, so none should grow it by copy-paste."""
    from orchestrator.sdlc import codegen

    others = [
        v for k, v in vars(codegen).items() if k.startswith("_IMPLEMENT_SYSTEM_") and isinstance(v, str)
    ]
    assert others, "expected the per-language prompts to exist"
    for prompt in others:
        assert "parallel module" not in prompt


# --- recovery can revise what it wrote, and cannot claim without sending -------------
#
# Two defects that trapped one run between them. Attempt 1 wrote api_errors.py; attempt 2
# rewrote cli.py against renamed helpers and was told "do NOT include [api_errors.py]
# again", so the module could not be renamed to match and the import failed. refine then
# diagnosed it exactly — "Rewrote orchestrator/api_errors.py to export api_call/..." — and
# submitted no files, which `allow_empty=True` read as "nothing to change", stopping the
# loop. Each rule was individually right.


def test_the_repair_permits_revising_a_file_that_landed(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import CodegenError

    (tmp_path / "api_errors.py").write_text("def old_name() -> None: ...\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("OTHER = 2\n", encoding="utf-8")
    exc = CodegenError(
        "changes need a repair pass",
        failed_edit_paths=["bad.py"],
        applied_paths=["api_errors.py"],
    )

    block = LLMCodegenAdapter(_ScriptedLLM([]))._repair_block(exc, tmp_path)

    assert "ALREADY WRITTEN" in block
    assert "now needs changing" in block, "revising an applied file must be permitted"
    assert "do not include them again" not in block.lower()
    # And it must be re-anchorable: the current content has to be in front of the model.
    assert "def old_name" in block


def test_the_repair_still_warns_against_stale_anchors(tmp_path: Path) -> None:
    """The original point of #150 survives: do not resend the edits that already applied."""
    from orchestrator.sdlc.codegen import CodegenError

    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("Y = 2\n", encoding="utf-8")
    exc = CodegenError("x", failed_edit_paths=["bad.py"], applied_paths=["a.py"])

    block = LLMCodegenAdapter(_ScriptedLLM([]))._repair_block(exc, tmp_path)

    assert "no longer match" in block
    assert "CURRENT content" in block


@pytest.mark.parametrize(
    "summary,claims",
    [
        ("Rewrote orchestrator/api_errors.py to export api_call and explain_status", False),
        ("Rewrote src/orchestrator/api_errors.py to export api_call", True),
        ("Added tests/test_cli.py covering the timeout path", True),
        ("No changes needed — src/orchestrator/cli.py already handles this", False),
        ("The implementation is already correct; nothing to change", False),
        ("Updated the docstring", False),
        ("", False),
    ],
)
def test_a_claim_needs_a_verb_and_a_path(summary: str, claims: bool) -> None:
    """Both halves, so a real no-op that mentions a file stays a no-op."""
    from orchestrator.sdlc.codegen import _claims_a_change

    assert _claims_a_change(summary) is claims


async def test_refine_that_describes_an_unsent_change_is_refused(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import CodegenError, LLMCodegenAdapter

    claim = "Rewrote src/orchestrator/api_errors.py to export api_call and explain_status"
    llm = _ScriptedLLM([json.dumps({"summary": claim, "files": []})] * 6)

    with pytest.raises(CodegenError):
        await LLMCodegenAdapter(llm).refine(
            spec={"title": "t"}, path=str(tmp_path), issue_key="S-1", failures="ImportError"
        )


async def test_refine_with_nothing_to_do_is_still_a_no_op(tmp_path: Path) -> None:
    """The behaviour `allow_empty` exists for must survive."""
    from orchestrator.sdlc.codegen import LLMCodegenAdapter

    llm = _ScriptedLLM([json.dumps({"summary": "nothing to change here", "files": []})])

    change = await LLMCodegenAdapter(llm).refine(
        spec={"title": "t"}, path=str(tmp_path), issue_key="S-1", failures="boom"
    )

    assert change.files == []
    assert "nothing to change" in change.summary
