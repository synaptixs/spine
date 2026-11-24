"""A correction keeps the one before it, and a claimed-but-unsent change is named (B68, SSPN-149).

django-14434's author_tests went syntax → empty → empty. Each corrective retry was a fresh prompt —
the original plus the latest suffix only — so once the model answered the syntax correction with an
empty reply, the syntax list was gone from every later prompt. Its second empty reply said it had added
a test file and sent none; outside refine that got the generic "submitted no files" advice. The run
ended "model output had no 'files' list", and the record could not say what had happened in order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.sdlc.codegen import CodegenError, LLMCodegenAdapter
from tests.sdlc.test_codegen import _SPEC, _files_response, _ScriptedLLM

_BROKEN = _files_response({"tests/test_add.py": "def test_add(:\n    assert add(2, 3) == 5\n"})
_GOOD = _files_response({"tests/test_add.py": "def test_add():\n    assert 2 + 3 == 5\n"})
_CLAIM = json.dumps({"files": [], "summary": "Added tests/test_add.py covering add(2, 3)."})
_NOTHING = json.dumps({"files": [], "summary": "nothing to add"})
_IMPL = _files_response({"calc.py": "def add(a, b):\n    return a + b\n"})


def _prompts(llm: _ScriptedLLM) -> list[str]:
    """The author_tests prompts: every call after the implement pass that gives it testable source."""
    return [call[-1].content for call in llm.calls[1:]]


async def _author_tests(tmp_path: Path, replies: list[str], **kwargs: object) -> tuple[_ScriptedLLM, object]:
    llm = _ScriptedLLM([_IMPL, *replies])
    adapter = LLMCodegenAdapter(llm, **kwargs)  # type: ignore[arg-type]
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="T-1")
    change = await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="T-1")
    return llm, change


async def test_an_empty_reply_does_not_erase_the_syntax_correction(tmp_path: Path) -> None:
    llm, change = await _author_tests(tmp_path, [_BROKEN, _NOTHING, _NOTHING, _GOOD])
    assert change.files  # type: ignore[attr-defined]
    prompts = _prompts(llm)
    assert "DOES NOT PARSE" in prompts[1]
    for later in prompts[2:]:  # both prompts after an empty reply still carry the syntax list
        assert "DOES NOT PARSE" in later and "tests/test_add.py" in later
        assert "SUBMITTED NO FILES" in later


async def test_one_earlier_correction_is_carried_not_all(tmp_path: Path) -> None:
    llm, _ = await _author_tests(tmp_path, [_BROKEN, _NOTHING, _NOTHING, _GOOD])
    for prompt in _prompts(llm):
        assert prompt.count("SUBMITTED NO FILES") <= 1  # the same kind twice is one block, not two
        assert prompt.count("DOES NOT PARSE") <= 1


async def test_a_repeat_of_the_same_kind_carries_nothing_extra(tmp_path: Path) -> None:
    llm, _ = await _author_tests(tmp_path, [_NOTHING, _NOTHING, _GOOD])
    third = _prompts(llm)[2]
    assert third.count("SUBMITTED NO FILES") == 1
    assert "BEFORE THAT" not in third


async def test_a_claimed_but_unsent_change_is_named_in_author_tests(tmp_path: Path) -> None:
    llm, _ = await _author_tests(tmp_path, [_CLAIM, _GOOD])
    second = _prompts(llm)[1]
    assert "says it changed `tests/test_add.py`" in second
    assert "Nothing was written" in second


async def test_a_claimed_but_unsent_change_is_named_in_refine(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    claim = json.dumps({"files": [], "summary": "Rewrote calc.py so add returns a + b."})
    fix = json.dumps(
        {"files": [{"path": "calc.py", "edits": [{"find": "a - b", "replace": "a + b"}]}], "summary": "fix"}
    )
    llm = _ScriptedLLM([claim, fix])
    change = await LLMCodegenAdapter(llm).refine(
        spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures="FAILED test_add - assert -1 == 5"
    )
    assert change.files
    assert "says it changed `calc.py`" in llm.calls[1][-1].content


async def test_the_final_error_states_the_sequence(tmp_path: Path) -> None:
    with pytest.raises(CodegenError) as caught:
        await _author_tests(tmp_path, [_BROKEN, _NOTHING, _NOTHING, _NOTHING])
    assert "attempts: syntax → empty → empty → empty" in str(caught.value)
    assert caught.value.empty_summary  # the error keeps what callers read from it


async def test_an_empty_author_tests_reply_is_recorded_with_its_text(tmp_path: Path) -> None:
    """SSPN-149's "not verified" item: the recorder keeps empty replies, claims included."""
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    await _author_tests(work, [_CLAIM, _GOOD], record_dir=out)
    first = json.loads((out / "attempts" / "author_tests-1.json").read_text(encoding="utf-8"))
    assert first["outcome"] == "rejected"
    assert first["reply"] == _CLAIM
    assert "no 'files' list" in first["error"]


def test_a_long_earlier_correction_is_carried_as_one_line() -> None:
    from orchestrator.sdlc.codegen import _carried_correction

    repair = "\n\nYOUR PREVIOUS ATTEMPT FAILED: edits did not anchor\n" + "x = 1\n" * 2000
    carried = _carried_correction([("anchors", repair)], "empty")
    assert "YOUR PREVIOUS ATTEMPT FAILED: edits did not anchor" in carried
    assert len(carried) < 500  # the whole file excerpt is not carried again
    assert _carried_correction([("anchors", repair)], "anchors") == ""
