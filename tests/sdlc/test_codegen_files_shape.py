"""B72 (SSPN-155): a reply whose ``files`` is present but is not a list is malformed, not empty.

A SWE-bench re-run of ``django__django-11138`` ended at ``[refine] [] - <a fix in words>`` and the
attempt record showed why: the refine reply was ``{"files": "<string>", "summary": ...}`` — a JSON
*string* cut off mid-file — while the same run's other stages sent lists. ``_apply`` treated any
non-list like an empty list, so a refine pass that had written its fix was read as "nothing to
change" and the run stopped; a stage that must send files raised ``no 'files' list`` and told the
model it had "submitted NO FILES", which was untrue.

The rule under test is refuse-only: ``files`` present and not a list -> a ``shape`` failure with
its own advice and two corrections (a bad draw is cured by another draw, as for ``empty``). An
absent key and ``[]`` keep today's behaviour, which ``allow_empty`` exists for. The string is never
parsed, even when it would parse: a second reply format to maintain, and it would not have helped
here (the string was unterminated).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.codegen import CodegenError, LLMCodegenAdapter, _failure_kind
from tests.sdlc.test_codegen import _SPEC, _files_response, _ScriptedLLM

# ``malformed_files`` is the field this change adds to ``CodegenError``. It is passed through a
# dict so this module type-checks (the repo's hook runs ``mypy src tests``) before the field exists.
_SHAPE: dict[str, Any] = {"malformed_files": "str, 12"}

_SENTINEL = "SENTINEL_BODY_THAT_MUST_NOT_BE_ECHOED"


def _stringified(path: str = "src/a.py", body: str = "a = 1\n") -> str:
    """What the model sent: ``files`` as a JSON string holding a list, not a list."""
    return json.dumps({"files": json.dumps([{"path": path, "content": body}]), "summary": "fix"})


def _refine(llm: _ScriptedLLM, tmp_path: Path, **kwargs: Any) -> Any:
    return LLMCodegenAdapter(llm, **kwargs).refine(
        spec=_SPEC, path=str(tmp_path), issue_key="S-1", failures="E   boom"
    )


# --- refine: a malformed reply is no longer a no-op -------------------------------------


async def test_a_refine_reply_with_files_as_a_string_is_retried_and_can_succeed(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_stringified(), _files_response({"src/a.py": "a = 1\n"})])

    change = await _refine(llm, tmp_path)

    assert [Path(f).name for f in change.files] == ["a.py"]
    assert len(llm.calls) == 2, "the malformed reply must cost a correction, not end the pass"


async def test_a_refine_reply_that_is_always_malformed_is_refused_after_two_corrections(
    tmp_path: Path,
) -> None:
    llm = _ScriptedLLM([_stringified()] * 5)

    with pytest.raises(CodegenError):
        await _refine(llm, tmp_path)

    assert len(llm.calls) == 3  # the first try plus two corrections, as for `empty`


@pytest.mark.parametrize("value", ["[]", "x", None, {"a": 1}, 5, True])
async def test_every_non_list_value_of_files_is_malformed(tmp_path: Path, value: object) -> None:
    llm = _ScriptedLLM([json.dumps({"files": value, "summary": "fix"})] * 5)

    with pytest.raises(CodegenError):
        await _refine(llm, tmp_path)

    assert len(llm.calls) == 3


async def test_an_absent_files_key_is_still_a_no_op(tmp_path: Path) -> None:
    """Guard: ``allow_empty`` exists for this; the rule must not reach it."""
    llm = _ScriptedLLM(['{"summary": "no change needed"}'])

    change = await _refine(llm, tmp_path)

    assert change.files == []
    assert len(llm.calls) == 1


async def test_an_empty_files_list_is_still_a_no_op(tmp_path: Path) -> None:
    """Guard: ``[]`` is a list, so it is not malformed."""
    llm = _ScriptedLLM([json.dumps({"files": [], "summary": "nothing to change here"})])

    change = await _refine(llm, tmp_path)

    assert change.files == []
    assert len(llm.calls) == 1


# --- a stage that must send files: the advice has to be true ----------------------------


async def test_the_advice_for_a_string_names_the_type_and_does_not_say_no_files_were_sent(
    tmp_path: Path,
) -> None:
    llm = _ScriptedLLM([_stringified()] * 5)

    with pytest.raises(CodegenError):
        await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")

    advice = llm.calls[1][1].content
    assert "SUBMITTED NO FILES" not in advice, "the model did send files; saying it did not is untrue"
    assert "array" in advice.lower()
    assert "string" in advice.lower(), "it must say what was found"


async def test_the_advice_carries_the_type_and_length_but_not_the_content(tmp_path: Path) -> None:
    body = f"# {_SENTINEL}\n" + "x = 1\n" * 2000
    llm = _ScriptedLLM([_stringified(body=body)] * 5)

    with pytest.raises(CodegenError):
        await LLMCodegenAdapter(llm).implement(spec=_SPEC, path=str(tmp_path), issue_key="S-1")

    advice = llm.calls[1][1].content
    assert _SENTINEL not in advice, "a 12 KB string must not be fed back into the prompt"
    assert "string" in advice.lower()
    assert any(ch.isdigit() for ch in advice.split("PREVIOUS")[-1]), "the length is part of the diagnosis"


# --- classification --------------------------------------------------------------------


def test_a_malformed_files_value_is_classified_apart_from_the_other_kinds() -> None:
    assert _failure_kind(CodegenError("x", **_SHAPE)) == "shape"
    # Unchanged neighbours, so the new kind did not swallow an old one.
    assert _failure_kind(CodegenError("x", parse_detail="bad json")) == "parse"
    assert _failure_kind(CodegenError("x", empty_summary="did nothing")) == "empty"


def test_the_shape_kind_has_advice_that_matches_it(tmp_path: Path) -> None:
    """Each kind alone must produce advice — see ``test_every_failure_kind_has_advice_that_matches_it``."""
    gen = LLMCodegenAdapter(_ScriptedLLM([]))

    suffix = gen._corrective_suffix(CodegenError("x", **_SHAPE), tmp_path)

    assert suffix is not None
    assert "array" in suffix.lower()
    assert "str, 12" in suffix, "the type and length found are the diagnosis"


# --- what is recorded ------------------------------------------------------------------


async def test_the_attempt_record_shows_the_rejection_then_the_success(tmp_path: Path) -> None:
    record = tmp_path / "rec"
    work = tmp_path / "work"
    work.mkdir()
    llm = _ScriptedLLM([_stringified(), _files_response({"src/a.py": "a = 1\n"})])

    await _refine(llm, work, record_dir=record)

    first = json.loads((record / "attempts" / "refine-1.json").read_text(encoding="utf-8"))
    second = json.loads((record / "attempts" / "refine-2.json").read_text(encoding="utf-8"))
    assert first["outcome"] == "rejected"
    assert "files" in first["error"]
    assert second["outcome"] == "applied"


async def test_the_log_names_the_type_and_length_not_the_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    llm = _ScriptedLLM([_stringified(body=f"# {_SENTINEL}\n"), _files_response({"src/a.py": "a = 1\n"})])

    with caplog.at_level(logging.WARNING, logger="orchestrator.sdlc.codegen"):
        await _refine(llm, tmp_path)

    lines = [r.getMessage() for r in caplog.records if "files_not_a_list" in r.getMessage()]
    assert len(lines) == 1
    assert "type=str" in lines[0]
    assert "len=" in lines[0]
    assert _SENTINEL not in lines[0]
