"""Agentic implement path (Phase 5b) — deterministic via a scripted Mock.

Drives the loop through write_files → run_tests → submit_changes and asserts the
files land on disk and a CodeChange comes back, all offline.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall
from orchestrator.sdlc.codegen import LLMCodegenAdapter

_SPEC = {"title": "add greet", "summary": "a greet() helper", "acceptance_criteria": ["greet() returns 'hi'"]}


def _call(name: str, args: dict[str, object], cid: str = "c") -> CompletionResult:
    return CompletionResult(
        text="",
        model="m",
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        latency_ms=0.0,
        tool_calls=(ToolCall(cid, name, args),),
    )


async def test_agentic_implement_writes_and_submits(tmp_path: Path) -> None:
    script = [
        _call(
            "write_files", {"files": [{"path": "feature.py", "content": "def greet():\n    return 'hi'\n"}]}
        ),
        _call("run_tests", {}),
        _call("submit_changes", {"summary": "added greet"}),
    ]
    adapter = LLMCodegenAdapter(MockLLMClient(script=script), model="m", agentic=True)
    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
    assert (tmp_path / "feature.py").exists()
    assert change.summary == "added greet"
    assert any("feature.py" in f for f in change.files)


async def test_agentic_implement_raises_if_nothing_written(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import CodegenError

    # Submits without ever writing → no files → a clean CodegenError.
    script = [
        _call("submit_changes", {"summary": "premature"}),
        _call("submit_changes", {"summary": "again"}),
    ]
    adapter = LLMCodegenAdapter(MockLLMClient(script=script), model="m", agentic=True)
    raised = False
    try:
        await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
    except CodegenError:
        raised = True
    assert raised


def test_skills_condition_the_system_prompt() -> None:
    adapter = LLMCodegenAdapter(MockLLMClient(), model="m", agentic=True)
    assert "For this project" not in adapter._agentic_system([])
    prompt = adapter._agentic_system(["python-conventions"])
    assert "For this project" in prompt and "Python conventions" in prompt


def test_persona_drives_role_and_vetting_gated_skills() -> None:
    # Phase 2b: with a persona, the prompt leads with its role and the skill guidance
    # is the persona-endorsed ∩ plan-selected ∩ vetting-approved set.
    from orchestrator.personas.software_engineer import SOFTWARE_ENGINEER

    adapter = LLMCodegenAdapter(MockLLMClient(), model="m", agentic=True, persona=SOFTWARE_ENGINEER)
    prompt = adapter._agentic_system(["python-conventions"])
    assert prompt.startswith("You are a senior software engineer")  # role leads
    assert "For this project" in prompt and "Python conventions" in prompt


def test_persona_excludes_skills_it_does_not_endorse() -> None:
    # A plan may select a skill the persona doesn't list → it's dropped.
    from orchestrator.registry._common import Metadata
    from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema

    persona = AgentTemplate(
        metadata=Metadata(id="persona.narrow", version="0.0.1", description="narrow persona"),
        spec=AgentSpec(
            role="You are a focused engineer.",
            skills=["python-conventions"],
            model="m",
            outputs=[
                FieldSchema(name="confidence", type="number"),
                FieldSchema(name="caveats", type="array"),
            ],
        ),
    )
    adapter = LLMCodegenAdapter(MockLLMClient(), model="m", agentic=True, persona=persona)
    prompt = adapter._agentic_system(["python-conventions", "repo-pkg-grounding"])
    assert "Python conventions" in prompt
    assert "Reuse existing symbols" not in prompt  # repo-pkg-grounding not persona-endorsed


async def test_implement_skills_arg_conditions_the_prompt(tmp_path: Path, monkeypatch: object) -> None:
    # The per-call skills (threaded from the run's plan) reach the loop's system
    # prompt — captured here via the system message of the first complete() call.
    seen: dict[str, str] = {}

    class _CapturingMock(MockLLMClient):
        async def complete(self, messages, **kw):  # type: ignore[no-untyped-def]
            seen["system"] = messages[0].content
            return await super().complete(messages, **kw)

    llm = _CapturingMock(
        script=[
            _call("write_files", {"files": [{"path": "f.py", "content": "x = 1\n"}]}),
            _call("submit_changes", {"summary": "done"}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, model="m", agentic=True)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-9", skills=["python-conventions"])
    assert "Python conventions" in seen["system"]


async def test_agentic_implement_can_call_an_mcp_tool(tmp_path: Path) -> None:
    # A fake MCP registry exposing one read-only tool; the loop calls it, then
    # writes + submits. Proves MCP tools are reachable mid-task (Phase 5c).
    from dataclasses import dataclass

    @dataclass
    class _T:
        server: str
        name: str
        description: str = ""
        input_schema: dict[str, object] | None = None
        read_only: bool | None = True

        @property
        def qualified_name(self) -> str:
            return f"{self.server}:{self.name}"

    @dataclass
    class _R:
        text: str
        is_error: bool = False

    @dataclass
    class _C:
        name: str
        write_enabled: bool = False

    class _Reg:
        def __init__(self) -> None:
            self.called = False

        async def list_tools(self) -> list[_T]:
            return [_T("db", "schema", "describe the schema", {"type": "object"})]

        async def call(self, qualified: str, args: dict[str, object]) -> _R:
            self.called = True
            return _R(text="table users(id, name)")

    reg = _Reg()
    script = [
        _call("mcp__db__schema", {}),  # the agent queries the DB MCP tool
        _call("write_files", {"files": [{"path": "m.py", "content": "USERS = ['id', 'name']\n"}]}),
        _call("submit_changes", {"summary": "used schema"}),
    ]
    adapter = LLMCodegenAdapter(
        MockLLMClient(script=script), model="m", agentic=True, mcp_registry=reg, mcp_configs=[_C("db")]
    )
    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-2")
    assert reg.called is True  # the MCP tool was actually invoked in the loop
    assert (tmp_path / "m.py").exists() and change.summary == "used schema"


async def test_single_shot_remains_default(tmp_path: Path) -> None:
    # agentic defaults off → implement uses the single-shot JSON path.
    payload = '{"files": [{"path": "feature.py", "content": "z = 1\\n"}], "summary": "ss"}'
    result = CompletionResult(
        text=payload, model="m", prompt_tokens=0, completion_tokens=0, cost_usd=0.0, latency_ms=0.0
    )
    llm = MockLLMClient(script=[result])
    adapter = LLMCodegenAdapter(llm, model="m")  # agentic not set
    change = await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")
    assert change.summary == "ss" and (tmp_path / "feature.py").exists()
