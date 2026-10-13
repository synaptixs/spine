"""B77 (SSPN-161): codegen opts in to native structured output and keeps every other behaviour.

``LiteLLMClient`` does the translation (``tests/core/llm/test_litellm_native_schema.py``); this file pins
what codegen owes it. ``_SUBMIT_TOOL`` opts in, the call codegen makes does not change at all (so no fake of
``LLMClient`` needs touching), and a reply that arrives as text -- which is what native structured output
returns -- goes through the same parse, the same B72 refusal and the same corrections as before.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from orchestrator.core.llm.client import CompletionResult, Message
from orchestrator.sdlc.codegen import _SUBMIT_TOOL, CodegenError, LLMCodegenAdapter
from tests.sdlc.test_codegen import _SPEC, _files_response, _ScriptedLLM


class _RecordingLLM(_ScriptedLLM):
    """A scripted fake that also keeps the keyword arguments of each call."""

    def __init__(self, responses: list[str | Exception]) -> None:
        super().__init__(responses)
        self.kwargs: list[dict[str, Any]] = []

    async def complete(self, messages: list[Message], **kwargs: Any) -> CompletionResult:
        self.kwargs.append(dict(kwargs))
        return await super().complete(messages, **kwargs)


def _refine(llm: _ScriptedLLM, tmp_path: Path) -> Any:
    return LLMCodegenAdapter(llm).refine(spec=_SPEC, path=str(tmp_path), issue_key="S-1", failures="E   boom")


def test_the_submit_tool_opts_in_to_native_schema() -> None:
    assert getattr(_SUBMIT_TOOL, "native_schema", False) is True


async def test_the_call_codegen_makes_is_unchanged(tmp_path: Path) -> None:
    """The translation is the client's business: codegen still asks for the forced tool."""
    llm = _RecordingLLM([_files_response({"src/a.py": "a = 1\n"})])

    await _refine(llm, tmp_path)

    sent = llm.kwargs[0]
    assert sent["tools"] == [_SUBMIT_TOOL]
    assert sent["tool_choice"] == "submit_files"
    assert sent["json_object"] is True
    assert sent.get("response_format") is None


async def test_a_reply_that_arrives_as_text_is_applied(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_files_response({"src/a.py": "a = 1\n"})])

    change = await _refine(llm, tmp_path)

    assert [Path(f).name for f in change.files] == ["a.py"]


async def test_a_text_reply_that_is_a_json_object_does_not_warn_about_a_missing_tool_call(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """On the native path text *is* the reply; a warning per call would read as a failure on every stage."""
    llm = _ScriptedLLM([_files_response({"src/a.py": "a = 1\n"})])

    with caplog.at_level(logging.DEBUG):
        await _refine(llm, tmp_path)

    warned = [r for r in caplog.records if "no_tool_call" in r.getMessage() and r.levelno >= logging.WARNING]
    assert warned == []


async def test_a_text_reply_that_is_not_a_json_object_still_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning keeps its meaning for a provider that ignored the tool and answered in prose."""
    llm = _ScriptedLLM(["I looked at the code and decided nothing needs to change."] * 5)

    with caplog.at_level(logging.DEBUG):
        await _refine(
            llm, tmp_path
        )  # `refine` allows an empty change, so prose ends the pass without raising

    warned = [r for r in caplog.records if "no_tool_call" in r.getMessage() and r.levelno >= logging.WARNING]
    assert warned, "prose is not the intended reply and must still be reported"


async def test_the_malformed_files_refusal_still_applies_to_a_text_reply(tmp_path: Path) -> None:
    """B72 is untouched: a ``files`` string is refused and corrected, as a tool call or as text."""
    stringified = json.dumps(
        {"files": json.dumps([{"path": "src/a.py", "content": "a = 1\n"}]), "summary": "Rewrote src/a.py"}
    )
    llm = _ScriptedLLM([stringified] * 5)

    with pytest.raises(CodegenError):
        await _refine(llm, tmp_path)

    assert len(llm.calls) == 3  # the first try plus two corrections, as before
