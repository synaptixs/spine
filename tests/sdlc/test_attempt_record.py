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

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.attempt_record import MAX_REPLY_BYTES, MAX_TEST_OUTPUT_BYTES, record_test_run
from orchestrator.sdlc.codegen import LLMCodegenAdapter

_SPEC: dict[str, Any] = {
    "title": "Add two numbers",
    "summary": "Provide an add(a, b) helper.",
    "acceptance_criteria": ["add(2, 3) returns 5"],
}


class _ScriptedLLM:
    """Queued replies, in order; quacks like ``LLMClient``."""

    def __init__(self, responses: list[str]) -> None:
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
        return CompletionResult(
            text=self._responses.pop(0),
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
