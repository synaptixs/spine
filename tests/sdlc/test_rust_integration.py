"""Real Cargo codegen gates; the dedicated Rust CI job supplies the toolchain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from orchestrator.core.llm import CompletionResult, Message, ToolSpec
from orchestrator.sdlc.codegen import LLMCodegenAdapter
from orchestrator.sdlc.layout import resolve_layout
from orchestrator.sdlc.rust_codegen import (
    CargoTestRunner,
    RustPreflightRunner,
    cargo_toolchain_available,
)
from orchestrator.sdlc.scaffold import scaffold

pytestmark = pytest.mark.skipif(
    not cargo_toolchain_available(), reason="needs Cargo, rustc and Rustfmt on PATH"
)


def _crate(root: Path) -> None:
    layout = resolve_layout(root, mode="new", language="rust", package_name="calc")
    scaffold(root, layout)


async def test_greenfield_build_tests_and_preflight(tmp_path: Path) -> None:
    _crate(tmp_path)
    (tmp_path / "src/lib.rs").write_text(
        "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"
        "#[cfg(test)]\nmod tests {\n    #[test]\n    fn adds() {\n"
        "        assert_eq!(super::add(2, 3), 5);\n    }\n}\n",
        encoding="utf-8",
    )
    result = await CargoTestRunner().run(path=str(tmp_path))
    assert result.passed, result.output
    preflight = await RustPreflightRunner().run(path=str(tmp_path))
    assert preflight.passed, preflight.output


async def test_compile_error_is_red(tmp_path: Path) -> None:
    _crate(tmp_path)
    (tmp_path / "src/lib.rs").write_text("pub fn broken() -> i32 { missing_value }\n", encoding="utf-8")
    result = await CargoTestRunner().run(path=str(tmp_path))
    assert not result.passed and result.returncode != 0
    assert "missing_value" in result.output


async def test_failing_test_is_red(tmp_path: Path) -> None:
    _crate(tmp_path)
    (tmp_path / "src/lib.rs").write_text(
        "pub fn value() -> i32 { 2 }\n"
        "#[cfg(test)] mod tests { #[test] fn value_is_one() { "
        "assert_eq!(super::value(), 1); } }\n",
        encoding="utf-8",
    )
    result = await CargoTestRunner().run(path=str(tmp_path))
    assert not result.passed and result.returncode != 0
    assert "FAILED" in result.output


async def test_scripted_codegen_writes_a_buildable_rust_feature(tmp_path: Path) -> None:
    """Use the actual codegen write guards and prompts before real Cargo verification."""
    layout = resolve_layout(tmp_path, mode="new", language="rust", package_name="calc")
    scaffold(tmp_path, layout)
    replies = [
        {
            "files": [
                {"path": "src/add.rs", "content": "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"},
                {
                    "path": "src/lib.rs",
                    "edits": [
                        {"find": "//! New Rust crate.\n", "replace": "//! New Rust crate.\npub mod add;\n"}
                    ],
                },
            ],
            "summary": "add a public addition function",
        },
        {
            "files": [
                {
                    "path": "tests/add.rs",
                    "content": "use calc::add::add;\n\n#[test]\nfn adds() {\n"
                    "    assert_eq!(add(2, 3), 5);\n}\n",
                }
            ],
            "summary": "test addition",
        },
    ]

    class Scripted:
        def __init__(self) -> None:
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
            tools: list[ToolSpec] | None = None,
            tool_choice: str | None = None,
        ) -> CompletionResult:
            _ = (model, response_format, json_object, temperature, max_tokens, tools, tool_choice)
            self.calls.append(messages)
            return CompletionResult(
                text=json.dumps(replies.pop(0)),
                model="scripted",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                latency_ms=0.0,
            )

    model = Scripted()
    adapter = LLMCodegenAdapter(model, layout=layout)
    spec = {"title": "Addition", "summary": "Add two integers", "acceptance_criteria": ["2+3=5"]}
    await adapter.implement(spec=spec, path=str(tmp_path), issue_key="RUST-1")
    await adapter.author_tests(spec=spec, path=str(tmp_path), issue_key="RUST-1")
    assert "selected Cargo target" in str(model.calls[0][0].content)
    result = await CargoTestRunner().run(path=str(tmp_path))
    assert result.passed, result.output
