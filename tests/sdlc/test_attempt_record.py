"""B72 (SSPN-155): every codegen attempt leaves a record, and every test run is kept in full.

Nine benchmark runs ended at ``[refine] [] - <a fix described in words>`` and nobody could say
why: the raw reply, what ``apply_files`` dropped and the failing test output were written
nowhere. This is the instrument, not the fix. It must not change what any stage returns.

The adapter gets a ``record_dir``; each model call of each stage writes
``<record_dir>/attempts/<stage>-<n>.json`` (``n`` counts per stage, retries included), and
``record_test_run`` keeps a test run's output in ``<record_dir>/run-tests-<n>.txt``.
``SDLC_ATTEMPT_RECORD=0`` turns both off.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from orchestrator.core.llm import CompletionResult, LLMError, Message, StructuredOutputError, ToolCall
from orchestrator.sdlc.attempt_record import (
    MAX_CONTEXT_BYTES,
    MAX_REPLY_BYTES,
    MAX_TEST_OUTPUT_BYTES,
    AttemptTrace,
    enabled,
    record_test_run,
    scrub,
    write_attempt,
)
from orchestrator.sdlc.codegen import _SUBMIT_TOOL, CodegenError, LLMCodegenAdapter, apply_files
from orchestrator.sdlc.scope import EditScope

_SPEC: dict[str, Any] = {
    "title": "Add two numbers",
    "summary": "Provide an add(a, b) helper.",
    "acceptance_criteria": ["add(2, 3) returns 5"],
}


class _ScriptedLLM:
    """Queued replies, in order; quacks like ``LLMClient``."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)

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
        _ = (messages, model, response_format, json_object, temperature, max_tokens, tools, tool_choice)
        reply = self._responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return CompletionResult(
            text=reply,
            model="fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
        )


def _files(files: dict[str, str], summary: str = "ok") -> str:
    return json.dumps({"files": [{"path": p, "content": c} for p, c in files.items()], "summary": summary})


def _edit(path: str, find: str, replace: str, summary: str = "ok") -> str:
    return json.dumps(
        {"files": [{"path": path, "edits": [{"find": find, "replace": replace}]}], "summary": summary}
    )


def _record(out: Path, stage: str, n: int) -> dict[str, Any]:
    loaded = json.loads((out / "attempts" / f"{stage}-{n}.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _recording_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SDLC_ATTEMPT_RECORD", raising=False)


# ---- what an attempt records ------------------------------------------------------


async def test_an_applied_attempt_records_the_raw_reply_and_what_landed(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    reply = _files({"src/a.py": "a = 1\n"}, summary="Add a")

    await LLMCodegenAdapter(_ScriptedLLM([reply]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    rec = _record(out, "implement", 1)
    assert rec["stage"] == "implement"
    assert rec["attempt"] == 1
    assert rec["reply"] == reply
    assert rec["reply_truncated"] is False
    assert rec["outcome"] == "applied"
    assert rec["summary"] == "Add a"
    assert rec["files"] == [{"path": "src/a.py", "outcome": "written", "reason": ""}]
    assert rec["hashes"]["src/a.py"] == {"before": None, "after": _sha(work / "src" / "a.py")}
    assert rec["error"] == ""


async def test_an_edit_records_the_file_hash_before_and_after(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    (work / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    before = _sha(work / "calc.py")

    await LLMCodegenAdapter(
        _ScriptedLLM([_edit("calc.py", "return a - b", "return a + b")]), record_dir=out
    ).refine(spec=_SPEC, path=str(work), issue_key="S-1", failures="E   assert 1 == 5\ncalc.py:2")

    rec = _record(out, "refine", 1)
    assert rec["outcome"] == "applied"
    assert rec["hashes"]["calc.py"]["before"] == before
    assert rec["hashes"]["calc.py"]["after"] == _sha(work / "calc.py")
    assert rec["hashes"]["calc.py"]["before"] != rec["hashes"]["calc.py"]["after"]


async def test_an_empty_refine_is_recorded_and_still_returns_as_before(tmp_path: Path) -> None:
    """The B72 case itself: a summary that describes a fix in the imperative and sends no files."""
    out = tmp_path / "out"
    summary = "Make tests importable without cx_Oracle by stubbing the Oracle driver when it is missing"
    reply = json.dumps({"summary": summary, "files": []})

    change = await LLMCodegenAdapter(_ScriptedLLM([reply]), record_dir=out).refine(
        spec=_SPEC, path=str(tmp_path), issue_key="S-1", failures="ImportError: cx_Oracle"
    )

    assert change.files == []  # behaviour unchanged
    assert change.summary == summary
    rec = _record(out, "refine", 1)
    assert rec["outcome"] == "empty"
    assert rec["reply"] == reply
    assert rec["summary"] == summary
    assert rec["files"] == []


async def test_every_model_call_of_a_retry_is_recorded_with_its_own_reply(tmp_path: Path) -> None:
    """A rejected attempt is exactly what B68 could not see: the first reply, kept."""
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    nothing = '{"summary": "nothing to do"}'
    good = _files({"src/a.py": "a = 1\n"})

    await LLMCodegenAdapter(_ScriptedLLM([nothing, good]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    first, second = _record(out, "implement", 1), _record(out, "implement", 2)
    assert (first["outcome"], first["reply"]) == ("rejected", nothing)
    assert "no 'files' list" in first["error"]
    assert (second["outcome"], second["reply"]) == ("applied", good)


async def test_a_bad_anchor_is_recorded_as_a_dropped_file_with_its_reason(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    (work / "util.py").write_text("x = 1\n", encoding="utf-8")
    bad = _edit("util.py", "NOT PRESENT ANYWHERE", "x = 2")
    good = _edit("util.py", "x = 1", "x = 2")

    await LLMCodegenAdapter(_ScriptedLLM([bad, good]), record_dir=out).refine(
        spec=_SPEC, path=str(work), issue_key="S-1", failures="boom util.py"
    )

    first = _record(out, "refine", 1)
    assert first["outcome"] == "rejected"
    assert first["files"] == [{"path": "util.py", "outcome": "dropped", "reason": "edit_failed"}]
    assert _record(out, "refine", 2)["outcome"] == "applied"


async def test_a_file_that_does_not_parse_is_recorded_as_dropped_for_syntax(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    broken = _files({"src/bad.py": "def (:\n"})
    good = _files({"src/ok.py": "ok = 1\n"})

    await LLMCodegenAdapter(_ScriptedLLM([broken, good]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    first = _record(out, "implement", 1)
    assert first["outcome"] == "rejected"
    assert first["files"] == [{"path": "src/bad.py", "outcome": "dropped", "reason": "syntax"}]
    assert not (work / "src" / "bad.py").exists()


async def test_attempts_are_numbered_per_stage(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    llm = _ScriptedLLM(
        [
            _files({"src/a.py": "a = 1\n"}),
            _files({"tests/test_a.py": "def test_a():\n    assert True\n"}),
            _files({"src/a.py": "a = 2\n"}),
            _files({"src/a.py": "a = 3\n"}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, record_dir=out)

    await adapter.implement(spec=_SPEC, path=str(work), issue_key="S-1")
    await adapter.author_tests(spec=_SPEC, path=str(work), issue_key="S-1")
    await adapter.refine(spec=_SPEC, path=str(work), issue_key="S-1", failures="x")
    await adapter.refine(spec=_SPEC, path=str(work), issue_key="S-1", failures="y")

    assert sorted(p.name for p in (out / "attempts").iterdir()) == [
        "author_tests-1.json",
        "implement-1.json",
        "refine-1.json",
        "refine-2.json",
    ]


async def test_revise_is_recorded_as_its_own_stage(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.py").write_text("a = 1\n", encoding="utf-8")

    await LLMCodegenAdapter(_ScriptedLLM([_edit("a.py", "a = 1", "a = 2")]), record_dir=out).revise(
        spec=_SPEC, path=str(work), issue_key="S-1", blockers=["a must be 2"]
    )

    assert _record(out, "revise", 1)["outcome"] == "applied"
    assert not (out / "attempts" / "refine-1.json").exists()


async def test_a_provider_parse_failure_is_recorded_with_no_reply_and_its_error(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    llm = _ScriptedLLM(
        [StructuredOutputError("Invalid structured response: cut off"), _files({"a.py": "a = 1\n"})]
    )

    await LLMCodegenAdapter(llm, record_dir=out).implement(spec=_SPEC, path=str(work), issue_key="S-1")

    first = _record(out, "implement", 1)
    assert (first["outcome"], first["reply"]) == ("rejected", "")
    assert "Invalid structured response" in first["error"]
    assert _record(out, "implement", 2)["outcome"] == "applied"


async def test_a_long_reply_is_truncated_and_says_so(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    reply = _files({"src/a.py": "a = 1\n"}, summary="s" * (MAX_REPLY_BYTES + 5000))

    await LLMCodegenAdapter(_ScriptedLLM([reply]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    rec = _record(out, "implement", 1)
    assert rec["reply_truncated"] is True
    assert len(rec["reply"].encode("utf-8")) <= MAX_REPLY_BYTES
    assert rec["outcome"] == "applied"  # recording does not change the result


# ---- off, and never in the way ----------------------------------------------------


async def test_the_kill_switch_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDLC_ATTEMPT_RECORD", "0")
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()

    change = await LLMCodegenAdapter(
        _ScriptedLLM([_files({"src/a.py": "a = 1\n"})]), record_dir=out
    ).implement(spec=_SPEC, path=str(work), issue_key="S-1")

    assert [Path(f).name for f in change.files] == ["a.py"]
    assert not out.exists()
    assert record_test_run(out, 1, "E boom") is None
    assert not out.exists()


async def test_no_record_dir_writes_nothing_and_changes_nothing(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()

    change = await LLMCodegenAdapter(_ScriptedLLM([_files({"src/a.py": "a = 1\n"})])).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    assert [Path(f).name for f in change.files] == ["a.py"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["work"]
    assert record_test_run(None, 1, "E boom") is None


async def test_a_record_dir_that_cannot_be_written_never_changes_the_result(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so nothing can be created under it", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()

    change = await LLMCodegenAdapter(
        _ScriptedLLM([_files({"src/a.py": "a = 1\n"})]), record_dir=blocker / "out"
    ).implement(spec=_SPEC, path=str(work), issue_key="S-1")

    assert [Path(f).name for f in change.files] == ["a.py"]
    assert (work / "src" / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert record_test_run(blocker / "out", 1, "E boom") is None  # also best-effort


# ---- test runs, kept in full ------------------------------------------------------


def test_a_test_run_is_kept_in_full(tmp_path: Path) -> None:
    out = tmp_path / "out"
    output = "FAILED tests/test_a.py::test_a\nE   AssertionError: boom\n"

    written = record_test_run(out, 2, output)

    assert written == out / "run-tests-2.txt"
    assert written is not None and written.read_text(encoding="utf-8") == output


def test_an_oversized_test_run_keeps_its_tail_and_says_what_it_dropped(tmp_path: Path) -> None:
    """The failure is at the end of a test run's output, so the end is what survives."""
    out = tmp_path / "out"
    output = ("noise line\n" * 20000) + "E   the real failure is here\n"
    assert len(output.encode("utf-8")) > MAX_TEST_OUTPUT_BYTES

    written = record_test_run(out, 1, output)

    assert written is not None
    text = written.read_text(encoding="utf-8")
    assert text.startswith("[truncated:")
    assert text.endswith("E   the real failure is here\n")
    assert len(text.encode("utf-8")) <= MAX_TEST_OUTPUT_BYTES + 200


# ---- every reason a file can be dropped, at the point that drops it ---------------

_TEST = """\
def test_group() -> None:
    assert "a".upper() == "A"


class TestTotals:
    def test_sum(self) -> None:
        assert sum([1, 2]) == 3
"""


def _trace(files: list[Any], root: Path, **kwargs: Any) -> AttemptTrace:
    """Run ``apply_files`` and return what it traced, whether or not the pass raised."""
    trace = AttemptTrace()
    kwargs.setdefault("written_tracker", {})
    kwargs.setdefault("grounded", False)
    with contextlib.suppress(CodegenError):
        apply_files(files, root, trace=trace, **kwargs)
    return trace


def test_edits_to_a_file_that_does_not_exist_are_dropped_as_a_missing_target(tmp_path: Path) -> None:
    trace = _trace([{"path": "nope.py", "edits": [{"find": "a", "replace": "b"}]}], tmp_path)

    assert trace.dropped == [("nope.py", "missing_target")]


def test_a_patched_file_over_the_cap_is_dropped_as_too_large(tmp_path: Path) -> None:
    (tmp_path / "big.py").write_text("x = 1\n", encoding="utf-8")
    huge = "x = '" + "a" * 300_000 + "'"

    trace = _trace([{"path": "big.py", "edits": [{"find": "x = 1", "replace": huge}]}], tmp_path)

    assert trace.dropped == [("big.py", "too_large")]
    assert (tmp_path / "big.py").read_text(encoding="utf-8") == "x = 1\n"


def test_a_stub_is_dropped_as_a_placeholder(tmp_path: Path) -> None:
    trace = _trace([{"path": "PLACEHOLDER", "content": "x"}], tmp_path)

    assert trace.dropped == [("PLACEHOLDER", "placeholder")]


def test_rewriting_an_existing_file_in_full_is_dropped_as_skipped_existing_file(tmp_path: Path) -> None:
    (tmp_path / "util.py").write_text("x = 1\n", encoding="utf-8")

    trace = _trace([{"path": "util.py", "content": "y = 2\n"}], tmp_path, grounded=True)

    assert trace.dropped == [("util.py", "skipped_existing_file")]


def test_refine_touching_a_file_nothing_names_is_dropped_as_refused_unnamed_edit(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "mine.py").write_text("a = 1\n", encoding="utf-8")
    (root / "other.py").write_text("b = 1\n", encoding="utf-8")
    tracker = {root: [root / "mine.py"]}  # this session wrote mine.py; other.py is someone else's

    trace = _trace(
        [{"path": "other.py", "edits": [{"find": "b = 1", "replace": "b = 2"}]}],
        root,
        written_tracker=tracker,
        grounded=True,
        editable_existing=frozenset({"mine.py"}),
    )

    assert trace.dropped == [("other.py", "refused_unnamed_edit")]
    assert (root / "other.py").read_text(encoding="utf-8") == "b = 1\n"


@pytest.mark.parametrize("form", ["edits", "content"])
def test_a_comment_only_change_outside_a_confident_scope_is_dropped_as_out_of_scope(
    tmp_path: Path, form: str
) -> None:
    (tmp_path / "unrelated.py").write_text("Y = 2\n", encoding="utf-8")
    entry: dict[str, Any] = {"path": "unrelated.py"}
    if form == "edits":
        entry["edits"] = [{"find": "Y = 2", "replace": "Y = 2  # see summary"}]
    else:
        entry["content"] = "Y = 2  # see summary\n"

    trace = _trace([entry], tmp_path, scope=EditScope(files=("wanted.py",), confident=True))

    assert trace.dropped == [("unrelated.py", "out_of_scope")]
    assert (tmp_path / "unrelated.py").read_text(encoding="utf-8") == "Y = 2\n"


@pytest.mark.parametrize("form", ["edits", "content"])
def test_losing_an_assert_in_a_session_test_is_dropped_as_weakened_test(tmp_path: Path, form: str) -> None:
    root = tmp_path.resolve()
    (root / "tests").mkdir()
    test_file = root / "tests" / "test_mine.py"
    test_file.write_text(_TEST, encoding="utf-8")
    entry: dict[str, Any] = {"path": "tests/test_mine.py"}
    if form == "edits":
        entry["edits"] = [{"find": "assert sum([1, 2]) == 3", "replace": "pass"}]
    else:
        entry["content"] = _TEST.split("class TestTotals")[0]

    trace = _trace([entry], root, written_tracker={root: [test_file]}, editable_existing=frozenset())

    assert trace.dropped == [("tests/test_mine.py", "weakened_test")]
    assert test_file.read_text(encoding="utf-8") == _TEST


def test_a_pass_that_raises_still_traces_what_it_wrote_and_what_it_dropped(tmp_path: Path) -> None:
    """The trace is settled before the pass raises, which is when a record matters most."""
    trace = _trace(
        [{"path": "a.py", "content": "a = 1\n"}, {"path": "b.py", "content": "def (:\n"}],
        tmp_path,
    )

    assert trace.written == ["a.py"]
    assert trace.dropped == [("b.py", "syntax")]
    assert trace.before["a.py"] is None
    assert trace.after["a.py"] == _sha(tmp_path / "a.py")
    assert trace.after["b.py"] is None  # never written


def test_an_edit_that_leaves_a_file_unparseable_is_dropped_for_syntax(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    trace = _trace([{"path": "a.py", "edits": [{"find": "x = 1", "replace": "def (:"}]}], tmp_path)

    assert trace.dropped == [("a.py", "syntax")]
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_a_file_named_twice_in_one_reply_keeps_its_hash_from_before_the_pass(tmp_path: Path) -> None:
    """Created by the first entry, edited by the second: 'before' is still 'did not exist'."""
    trace = _trace(
        [
            {"path": "a.py", "content": "a = 1\n"},
            {"path": "a.py", "edits": [{"find": "a = 1", "replace": "a = 2"}]},
        ],
        tmp_path,
    )

    assert trace.before["a.py"] is None
    assert trace.after["a.py"] == _sha(tmp_path / "a.py")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 2\n"


# ---- review round 1 (#550): each finding reproduced, then fixed -------------------

_SURROGATE = "\ud800"  # a lone surrogate: valid in a Python str, not encodable as UTF-8


def test_m1_a_lone_surrogate_in_a_reply_or_test_output_never_raises(tmp_path: Path) -> None:
    out = tmp_path / "out"

    written = write_attempt(
        out,
        stage="refine",
        attempt=1,
        reply=f"bad {_SURROGATE} char",
        outcome="applied",
        trace=AttemptTrace(),
    )
    kept = record_test_run(out, 1, f"E   output with {_SURROGATE}")

    assert written is not None and json.loads(written.read_text(encoding="utf-8"))["stage"] == "refine"
    assert kept is not None and kept.read_text(encoding="utf-8").startswith("E   output with")


async def test_m1_a_surrogate_in_the_raw_reply_does_not_crash_a_good_change(tmp_path: Path) -> None:
    """The provider text fallback hands `_generate` the model's own text, surrogates and all."""
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    reply = '{"files": [{"path": "a.py", "content": "a = 1\\n"}], "summary": "bad ' + _SURROGATE + ' char"}'

    change = await LLMCodegenAdapter(_ScriptedLLM([reply]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    assert [Path(f).name for f in change.files] == ["a.py"]


async def test_m1_whatever_goes_wrong_in_the_recorder_never_reaches_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the recorder has a bug")

    monkeypatch.setattr("orchestrator.sdlc.codegen.write_attempt", explode)
    work = tmp_path / "work"
    work.mkdir()

    change = await LLMCodegenAdapter(
        _ScriptedLLM([_files({"a.py": "a = 1\n"})]), record_dir=tmp_path / "out"
    ).implement(spec=_SPEC, path=str(work), issue_key="S-1")

    assert [Path(f).name for f in change.files] == ["a.py"]


def test_m2_entries_that_name_nothing_usable_are_dropped_with_a_reason(tmp_path: Path) -> None:
    trace = _trace(
        [
            None,
            {"path": ""},
            {"path": "a.py", "content": None},
            {"path": "b.py", "content": 5},
            {"path": "c.py", "edits": "oops"},
        ],
        tmp_path,
    )

    assert trace.dropped == [
        ("#0", "not_an_object"),
        ("#1", "no_path"),
        ("a.py", "no_content_or_edits"),
        ("b.py", "no_content_or_edits"),
        ("c.py", "no_content_or_edits"),
    ]


def test_m2_a_pass_that_raises_inside_the_loop_still_says_what_it_wrote(tmp_path: Path) -> None:
    """The shadow-module guard raises mid-loop; the earlier file is on disk and must be in the record."""
    trace = _trace(
        [{"path": "ok.py", "content": "ok = 1\n"}, {"path": "json.py", "content": "x = 1\n"}], tmp_path
    )

    assert trace.written == ["ok.py"]
    assert trace.after["ok.py"] == _sha(tmp_path / "ok.py")
    assert trace.dropped == [("json.py", "shadows_module")]


def test_m2_a_file_over_the_size_cap_is_dropped_with_a_reason(tmp_path: Path) -> None:
    trace = _trace(
        [{"path": "ok.py", "content": "ok = 1\n"}, {"path": "big.py", "content": "x = 1\n" * 20_000}],
        tmp_path,
    )

    assert trace.written == ["ok.py"]
    assert trace.dropped == [("big.py", "too_large")]


async def test_s1_a_second_adapter_on_the_same_directory_continues_the_numbering(tmp_path: Path) -> None:
    """`--resume` keeps the run id, so a fresh process writes into the directory the first one used."""
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    first = _files({"a.py": "a = 1\n"})
    await LLMCodegenAdapter(_ScriptedLLM([first]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    second = _files({"b.py": "b = 1\n"})
    await LLMCodegenAdapter(_ScriptedLLM([second]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    assert _record(out, "implement", 1)["reply"] == first  # not overwritten
    assert _record(out, "implement", 2)["reply"] == second


def test_s1_a_second_run_does_not_overwrite_a_kept_test_run(tmp_path: Path) -> None:
    out = tmp_path / "out"

    record_test_run(out, 1, "first process")
    again = record_test_run(out, 1, "resumed process")

    assert (out / "run-tests-1.txt").read_text(encoding="utf-8") == "first process"
    assert again is not None and again != out / "run-tests-1.txt"
    assert again.read_text(encoding="utf-8") == "resumed process"


def test_s2_secret_values_from_the_environment_are_redacted_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-abcdef123456")
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "tok_0123456789")
    monkeypatch.setenv("SHORT_SECRET", "abc")  # too short to redact without wrecking ordinary text
    monkeypatch.setenv("HARMLESS_SETTING", "plain-value-here")
    out = tmp_path / "out"

    attempt = write_attempt(
        out,
        stage="refine",
        attempt=1,
        reply="key=sk-test-abcdef123456 abc plain-value-here",
        outcome="applied",
        trace=AttemptTrace(),
        context="E token tok_0123456789",
    )
    run = record_test_run(out, 1, "env: ANTHROPIC_API_KEY=sk-test-abcdef123456")

    assert attempt is not None and run is not None
    blob = attempt.read_text(encoding="utf-8") + run.read_text(encoding="utf-8")
    assert "sk-test-abcdef123456" not in blob and "tok_0123456789" not in blob
    assert "[redacted]" in blob
    assert "plain-value-here" in blob and "abc" in blob


def test_s2_the_ci_artifact_upload_leaves_the_raw_replies_out() -> None:
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/spine-sdlc.yml").read_text(
        encoding="utf-8"
    )
    upload = workflow.split("name: spine-run", 1)[1]

    assert "!run-artifacts/**/attempts/**" in upload
    assert "!run-artifacts/**/run-tests-*" in upload


async def test_s3_with_recording_off_nothing_is_traced_or_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(path: Path) -> None:
        raise AssertionError(f"hashed {path} while recording was off")

    monkeypatch.setattr("orchestrator.sdlc.attempt_record.file_hash", forbidden)
    work = tmp_path / "work"
    work.mkdir()

    no_dir = await LLMCodegenAdapter(_ScriptedLLM([_files({"a.py": "a = 1\n"})])).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )
    monkeypatch.setenv("SDLC_ATTEMPT_RECORD", "0")
    switched_off = await LLMCodegenAdapter(
        _ScriptedLLM([_files({"b.py": "b = 1\n"})]), record_dir=tmp_path / "out"
    ).implement(spec=_SPEC, path=str(work), issue_key="S-1")

    assert [Path(f).name for f in no_dir.files + switched_off.files] == ["a.py", "b.py"]


class _RichLLM:
    """Returns prebuilt ``CompletionResult`` objects, to give a reply its metadata."""

    def __init__(self, results: list[CompletionResult | Exception]) -> None:
        self._results = list(results)

    async def complete(self, messages: list[Message], **kwargs: Any) -> CompletionResult:
        _ = (messages, kwargs)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _tool_result(args: dict[str, Any], **kwargs: Any) -> CompletionResult:
    return CompletionResult(
        text=kwargs.pop("text", ""),
        model="fake-model",
        prompt_tokens=11,
        completion_tokens=kwargs.pop("completion_tokens", 22),
        cost_usd=0.0,
        latency_ms=1.0,
        raw=kwargs.pop("raw", None),
        tool_calls=(ToolCall(id="1", name=_SUBMIT_TOOL.name, arguments=args),),
    )


async def test_s4_the_record_keeps_what_the_model_said_beside_the_tool_call(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    result = _tool_result(
        {"files": [{"path": "a.py", "content": "a = 1\n"}], "summary": "ok"},
        text="thinking aloud",
        completion_tokens=123,
        raw={"finish_reason": "length"},
    )

    await LLMCodegenAdapter(_RichLLM([result]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    assert _record(out, "implement", 1)["meta"] == {
        "model": "fake-model",
        "prompt_tokens": 11,
        "completion_tokens": 123,
        "finish_reason": "length",
        "side_text": "thinking aloud",
    }


async def test_s4_refine_and_revise_record_the_reason_they_were_asked_to_act(tmp_path: Path) -> None:
    """Not only a test failure: the type check, coverage and judge text reach refine as `failures`."""
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.py").write_text("a = 1\n", encoding="utf-8")
    llm = _ScriptedLLM([_edit("a.py", "a = 1", "a = 2"), _edit("a.py", "a = 2", "a = 3")])
    adapter = LLMCodegenAdapter(llm, record_dir=out)

    await adapter.refine(spec=_SPEC, path=str(work), issue_key="S-1", failures="mypy: a.py:1 error")
    await adapter.revise(spec=_SPEC, path=str(work), issue_key="S-1", blockers=["must be 3"])

    assert _record(out, "refine", 1)["context"] == "mypy: a.py:1 error"
    assert "must be 3" in _record(out, "revise", 1)["context"]


async def test_s4_an_llm_error_that_ends_the_call_is_recorded_too(tmp_path: Path) -> None:
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()

    with pytest.raises(LLMError, match="boom"):
        await LLMCodegenAdapter(_RichLLM([LLMError("boom: rate limited")]), record_dir=out).implement(
            spec=_SPEC, path=str(work), issue_key="S-1"
        )

    rec = _record(out, "implement", 1)
    assert rec["outcome"] == "error" and "boom" in rec["error"]


async def test_s5_recording_changes_neither_results_nor_files_nor_the_number_of_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run(
        root: Path, record_dir: Path | None
    ) -> tuple[list[tuple[list[str], str]], dict[str, str], int]:
        root.mkdir()
        (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        replies: list[str | Exception] = [
            _files({"src/a.py": "a = 1\n"}),
            json.dumps({"summary": "Make add use plus", "files": []}),  # B72's shape
            _edit("calc.py", "return a - b", "return a + b"),
            '{"summary": "nothing"}',  # empty, retried by implement's corrective retry
            _files({"src/b.py": "b = 1\n"}),
        ]
        llm = _ScriptedLLM(replies)
        adapter = LLMCodegenAdapter(llm, record_dir=record_dir)
        changes = [
            await adapter.implement(spec=_SPEC, path=str(root), issue_key="S-1"),
            await adapter.refine(spec=_SPEC, path=str(root), issue_key="S-1", failures="x"),
            await adapter.refine(spec=_SPEC, path=str(root), issue_key="S-1", failures="y"),
            await adapter.implement(spec=_SPEC, path=str(root), issue_key="S-1"),
        ]
        tree = {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.py"))}
        shown = [([Path(f).name for f in c.files], c.summary) for c in changes]
        return shown, tree, len(replies) - len(llm._responses)

    monkeypatch.delenv("SDLC_ATTEMPT_RECORD", raising=False)
    on = await run(tmp_path / "on", tmp_path / "out")
    off = await run(tmp_path / "off", None)

    assert on == off
    assert len(list((tmp_path / "out" / "attempts").iterdir())) == 5  # one per model call


class _YieldingLLM(_ScriptedLLM):
    """Suspends inside `complete`, so two concurrent calls on one adapter can really interleave."""

    async def complete(self, messages: list[Message], **kwargs: Any) -> CompletionResult:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return await super().complete(messages, **kwargs)


async def test_s5_concurrent_calls_on_one_adapter_get_distinct_numbers(tmp_path: Path) -> None:
    out = tmp_path / "out"
    roots = []
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        (root / "a.py").write_text("a = 1\n", encoding="utf-8")
        roots.append(root)
    adapter = LLMCodegenAdapter(
        _YieldingLLM([_edit("a.py", "a = 1", "a = 2"), _edit("a.py", "a = 1", "a = 3")]), record_dir=out
    )

    await asyncio.gather(
        *(adapter.refine(spec=_SPEC, path=str(r), issue_key="S-1", failures="x") for r in roots)
    )

    assert sorted(p.name for p in (out / "attempts").iterdir()) == ["refine-1.json", "refine-2.json"]


def test_nit_a_path_spelled_two_ways_is_one_file_with_one_before_hash(tmp_path: Path) -> None:
    trace = _trace(
        [
            {"path": "a.py", "content": "a = 1\n"},
            {"path": "./a.py", "edits": [{"find": "a = 1", "replace": "a = 2"}]},
        ],
        tmp_path,
    )

    assert trace.before == {"a.py": None, "./a.py": None}
    assert trace.written == ["a.py"]


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "off", "no"])
def test_nit_the_kill_switch_accepts_the_usual_spellings_of_off(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDLC_ATTEMPT_RECORD", value)

    assert enabled() is False


async def test_nit_an_unparseable_reply_is_not_called_an_empty_one(tmp_path: Path) -> None:
    out = tmp_path / "out"

    await LLMCodegenAdapter(_ScriptedLLM(["Sorry, I can't fix this."]), record_dir=out).refine(
        spec=_SPEC, path=str(tmp_path), issue_key="S-1", failures="boom"
    )

    assert _record(out, "refine", 1)["outcome"] == "unparseable"


def test_the_module_itself_never_raises_for_an_unwritable_directory(tmp_path: Path) -> None:
    """Not left to the adapter's own safety net: each writer keeps the promise on its own."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so nothing can be created under it", encoding="utf-8")

    attempt = write_attempt(
        blocker / "out", stage="refine", attempt=1, reply="r", outcome="applied", trace=AttemptTrace()
    )

    assert attempt is None
    assert record_test_run(blocker / "out", 1, "E boom") is None


def test_m2_a_package_that_would_hide_an_existing_module_is_dropped_as_shadowing_it(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "cli.py").write_text("X = 1\n", encoding="utf-8")

    trace = _trace([{"path": "pkg/cli/__init__.py", "content": "Y = 2\n"}], tmp_path)

    assert trace.dropped == [("pkg/cli/__init__.py", "shadows_module")]
    assert not (tmp_path / "pkg" / "cli").exists()


# ---- review round 2 (#552): everything the report listed -------------------------


def test_r2_the_module_has_no_empty_except_handler() -> None:
    """CodeQL py/empty-except (alert 317) blocks the develop -> main promotion while unresolved."""
    import orchestrator.sdlc.attempt_record as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    empty = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.ExceptHandler) and all(isinstance(b, ast.Pass) for b in n.body)
    ]

    assert empty == []


def test_r2_a_long_context_keeps_its_tail_where_the_error_is_and_says_it_was_cut(tmp_path: Path) -> None:
    out = tmp_path / "out"
    long = "HEADMARK " + "x" * (MAX_CONTEXT_BYTES + 5000) + " TAILMARK the actual error"
    short = "mypy: a.py:1 error"

    cut = write_attempt(
        out, stage="refine", attempt=1, reply="r", outcome="applied", trace=AttemptTrace(), context=long
    )
    whole = write_attempt(
        out, stage="refine", attempt=2, reply="r", outcome="applied", trace=AttemptTrace(), context=short
    )

    assert cut is not None and whole is not None
    cut_rec = json.loads(cut.read_text(encoding="utf-8"))
    assert "TAILMARK the actual error" in cut_rec["context"] and "HEADMARK" not in cut_rec["context"]
    assert cut_rec["context_truncated"] is True
    assert len(cut_rec["context"].encode("utf-8")) <= MAX_CONTEXT_BYTES
    whole_rec = json.loads(whole.read_text(encoding="utf-8"))
    assert whole_rec["context"] == short and whole_rec["context_truncated"] is False


async def test_r2_the_finish_reason_is_read_from_the_shape_litellm_produces(tmp_path: Path) -> None:
    """`raw` is the provider response: the reason lives under choices[0], not at the top."""
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    result = _tool_result(
        {"files": [{"path": "a.py", "content": "a = 1\n"}], "summary": "ok"},
        raw={"choices": [{"finish_reason": "length", "message": {}}]},
    )

    await LLMCodegenAdapter(_RichLLM([result]), record_dir=out).implement(
        spec=_SPEC, path=str(work), issue_key="S-1"
    )

    assert _record(out, "implement", 1)["meta"]["finish_reason"] == "length"


def test_r2_a_multi_line_secret_is_redacted_in_its_json_escaped_form_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pem = "-----BEGIN KEY-----\nabcdef123456\n-----END KEY-----"
    monkeypatch.setenv("APP_PRIVATE_KEY", pem)
    out = tmp_path / "out"
    reply = json.dumps({"files": [], "summary": f"the key is {pem}"})  # a newline becomes \n here

    path = write_attempt(out, stage="refine", attempt=1, reply=reply, outcome="applied", trace=AttemptTrace())

    assert path is not None
    assert "abcdef123456" not in path.read_text(encoding="utf-8")
    assert "abcdef123456" not in scrub(reply)


def test_r2_a_value_that_is_an_existing_path_is_not_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A password-store directory is not a password; blanking it makes tracebacks unreadable."""
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("DEPLOY_TOKEN", "tok_real_secret_value")

    text = scrub(f'File "{tmp_path}/pkg/mod.py" token=tok_real_secret_value')

    assert str(tmp_path) in text
    assert "tok_real_secret_value" not in text


def test_r2_a_secret_inside_a_longer_secret_does_not_leave_the_rest_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("A_TOKEN", "secretvalue1")
    monkeypatch.setenv("B_TOKEN", "pre-secretvalue1-post")

    assert scrub("x pre-secretvalue1-post y") == "x [redacted] y"


def test_r2_a_reply_can_be_copied_and_pickled_with_its_metadata() -> None:
    from orchestrator.sdlc.codegen import _Reply

    reply = _Reply('{"files": []}', {"finish_reason": "stop"})

    for clone in (copy.copy(reply), copy.deepcopy(reply), pickle.loads(pickle.dumps(reply))):  # noqa: S301 — a round trip of our own bytes
        assert clone == '{"files": []}' and clone.meta == {"finish_reason": "stop"}


def test_r2_reply_metadata_never_raises_for_a_result_of_an_unexpected_shape() -> None:
    from orchestrator.sdlc.codegen import _reply_meta

    assert _reply_meta(object(), side_text="") == {}


async def test_r2_an_unexpected_exception_is_recorded_and_still_raised(tmp_path: Path) -> None:
    """A non-UTF-8 file under an `edits` entry raises out of refine, as it always has: now with a record."""
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir()
    (work / "bin.py").write_bytes(b"\xff\xfe not utf-8")

    with pytest.raises(UnicodeDecodeError):
        await LLMCodegenAdapter(_ScriptedLLM([_edit("bin.py", "x", "y")]), record_dir=out).refine(
            spec=_SPEC, path=str(work), issue_key="S-1", failures="boom"
        )

    rec = _record(out, "refine", 1)
    assert rec["outcome"] == "error" and "UnicodeDecodeError" in rec["error"]
    assert "bin.py" in rec["hashes"]  # the pass was settled before the exception left


def test_r2_a_path_that_escapes_the_worktree_is_dropped_with_a_reason(tmp_path: Path) -> None:
    trace = _trace([{"path": "../evil.py", "content": "x = 1\n"}], tmp_path)

    assert trace.dropped == [("../evil.py", "unsafe_path")]


def test_r2_a_third_run_of_the_same_number_gets_the_next_free_name(tmp_path: Path) -> None:
    out = tmp_path / "out"
    names = [record_test_run(out, 1, f"run {i}") for i in range(3)]

    assert [p.name for p in names if p] == ["run-tests-1.txt", "run-tests-1.2.txt", "run-tests-1.3.txt"]
    assert [p.read_text(encoding="utf-8") for p in names if p] == ["run 0", "run 1", "run 2"]


def test_r2_the_ci_upload_step_lists_the_exclusions_after_the_directory() -> None:
    """Structure, not a substring: the negations belong to the `spine-run` upload step's path list."""
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/spine-sdlc.yml").read_text(encoding="utf-8")
    )
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    upload = next(s for s in steps if s.get("with", {}).get("name") == "spine-run")

    lines = [ln.strip() for ln in upload["with"]["path"].splitlines() if ln.strip()]
    assert lines == ["run-artifacts", "!run-artifacts/**/attempts/**", "!run-artifacts/**/run-tests-*"]
