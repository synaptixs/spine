"""Refine may repair lint and type findings in the tests this session wrote (B47, SSPN-93).

Refine is told not to touch tests, so a model cannot make a failing test pass by changing it.
That also left every ruff/mypy finding in a *generated* test file unfixable: measured at 25/30
preflight on develop, every failure in a test file this session wrote. Three pieces close it
without reopening the door the rule closed:

- a docstring after ``from __future__`` is moved to the top before ruff runs (no model call);
- only a lint/type line naming a session-written Python test unlocks it — a pytest failure never;
- any refine edit to a session test that drops or changes an assert, or drops a test, is refused.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.codegen import (
    CodegenError,
    LLMCodegenAdapter,
    _hoist_future_docstring,
    _lint_named_session_tests,
    _lint_test_allowance,
    _weakens_test,
    apply_files,
)

# --- the rewrite (P1) ------------------------------------------------------------------------

_MISPLACED = (
    'from __future__ import annotations\n\n"""Tests for the ledger."""\n\nimport os\n\nprint(os.sep)\n'
)


def test_a_docstring_after_future_is_moved_to_the_top() -> None:
    fixed = _hoist_future_docstring(_MISPLACED)

    assert fixed is not None
    assert fixed.startswith('"""Tests for the ledger."""\n\nfrom __future__ import annotations\n')
    assert ast.get_docstring(ast.parse(fixed)) == "Tests for the ledger."


def test_a_line_ending_is_kept() -> None:
    fixed = _hoist_future_docstring(_MISPLACED.replace("\n", "\r\n"))

    assert fixed is not None
    assert "\n" not in fixed.replace("\r\n", "")


def test_the_rewrite_changes_no_code() -> None:
    fixed = _hoist_future_docstring(_MISPLACED)

    assert fixed is not None
    before, after = ast.parse(_MISPLACED).body, ast.parse(fixed).body
    assert ast.dump(after[0]) == ast.dump(before[1])  # the docstring, moved
    assert [ast.dump(s) for s in after[1:]] == [ast.dump(s) for s in before[:1] + before[2:]]


def test_several_future_imports_and_a_leading_comment_are_kept() -> None:
    text = (
        "#!/usr/bin/env python\n"
        "from __future__ import annotations\n"
        "from __future__ import division\n"
        '"""Multi\nline."""\n'
        "import os\n"
    )

    fixed = _hoist_future_docstring(text)

    assert fixed is not None
    assert fixed.startswith("#!/usr/bin/env python\n" + '"""Multi\nline."""\n')
    assert ast.get_docstring(ast.parse(fixed)) == "Multi\nline."
    assert "from __future__ import division\n" in fixed


@pytest.mark.parametrize(
    "text",
    [
        '"""Already first."""\n\nfrom __future__ import annotations\n\nimport os\n',
        "from __future__ import annotations\n\nimport os\n",
        'import os\n\n"""Not after a future import."""\n',
        'from __future__ import annotations\n\nx = 1\n"""A string later on."""\n',
        'from __future__ import annotations\n"""On a line with code."""; x = 1\n',
        'from __future__ import annotations\n"""Doc."""  # a trailing comment\n',
        # ast counts bytes: sliced by characters, this comment would look like nothing
        'from __future__ import annotations\n"""éééééééééé"""  # c\n',
        "def broken(:\n",
        "",
    ],
)
def test_anything_else_is_left_alone(text: str) -> None:
    assert _hoist_future_docstring(text) is None


def test_a_written_file_is_rewritten_before_ruff_runs(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()

    apply_files(
        [{"path": "tests/test_ledger.py", "content": _MISPLACED}],
        tmp_path,
        written_tracker={},
        grounded=False,
    )

    text = (tmp_path / "tests" / "test_ledger.py").read_text(encoding="utf-8")
    assert ast.get_docstring(ast.parse(text)) == "Tests for the ledger."


# --- which test files a failure unlocks (P2, D7) -----------------------------------------------


def _session(tmp_path: Path) -> list[Path]:
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    mine = tmp_path / "tests" / "test_mine.py"
    theirs = tmp_path / "tests" / "test_theirs.py"
    impl = tmp_path / "src" / "impl.py"
    for f in (mine, theirs, impl):
        f.write_text("x = 1\n", encoding="utf-8")
    return [mine.resolve(), impl.resolve()]  # the session wrote these; test_theirs pre-exists


@pytest.mark.parametrize(
    "failures",
    [
        # ruff check, default ("full") output
        "--- ruff check failed (exit 1) ---\n"
        "E402 Module level import not at top of file\n --> tests/test_mine.py:5:1\n",
        # ruff check, concise output
        "tests/test_mine.py:5:1: E402 Module level import not at top of file\n",
        # mypy, with and without a column
        'tests/test_mine.py:12: error: Item "None" of "Match[str] | None" '
        'has no attribute "group"  [union-attr]\n',
        "tests/test_mine.py:12:9: error: Incompatible types in assignment  [assignment]\n",
        # ruff format --check, both spellings
        "Would reformat: tests/test_mine.py\n",
        "unformatted: File would be reformatted\n --> tests/test_mine.py:1:2\n",
        # baseline mode
        "--- 2 NEW finding(s) vs baseline ---\n  ruff check: tests/test_mine.py [E402] x2\n",
        "  mypy: tests/test_mine.py [union-attr] x1\n",
        "  ruff format: tests/test_mine.py [unformatted] x1\n",
    ],
)
def test_a_lint_or_type_line_unlocks_a_session_test(tmp_path: Path, failures: str) -> None:
    written = _session(tmp_path)

    assert _lint_named_session_tests(failures, tmp_path, written) == ["tests/test_mine.py"]


def test_an_absolute_path_on_a_lint_line_still_counts(tmp_path: Path) -> None:
    written = _session(tmp_path)
    failures = f"{tmp_path / 'tests' / 'test_mine.py'}:3: error: Name 'y' is not defined  [name-defined]\n"

    assert _lint_named_session_tests(failures, tmp_path, written) == ["tests/test_mine.py"]


@pytest.mark.parametrize(
    "failures",
    [
        "FAILED tests/test_mine.py::test_total - assert 3 == 4\n",
        "ERROR tests/test_mine.py - ModuleNotFoundError: No module named 'x'\n",
        "tests/test_mine.py:12: AssertionError\n",
        "tests/test_mine.py:12: in test_total\n    assert total() == 4\n",
        "E   AssertionError: assert 3 == 4\n  tests/test_mine.py\n",
        # pytest echoes a failing test's source: a lint-shaped line inside a string is not a finding
        "=================================== FAILURES ===================================\n"
        '    def test_parse() -> None:\n        text = """\n'
        '    tests/test_mine.py:3: error: Name "y" is not defined  [name-defined]\n'
        '        """\n>       assert parse(text)\nE       AssertionError\n',
        # nor is one a test printed, whatever pytest flags were used
        "----- Captured stdout call -----\ntests/test_mine.py:3:1: E402 Module level import\n"
        "1 failed, 2 passed in 0.12s\n",
        # a lint shape mentioned mid-line is prose, not a finding
        "see tests/test_mine.py:3: error: x\n",
    ],
)
def test_a_pytest_failure_never_unlocks_a_test(tmp_path: Path, failures: str) -> None:
    written = _session(tmp_path)

    assert _lint_named_session_tests(failures, tmp_path, written) == []


def test_a_pre_existing_test_is_never_unlocked(tmp_path: Path) -> None:
    written = _session(tmp_path)

    assert (
        _lint_named_session_tests("  ruff check: tests/test_theirs.py [E402] x1\n", tmp_path, written) == []
    )


def test_a_lint_line_on_implementation_code_unlocks_no_test(tmp_path: Path) -> None:
    """Refine may already fix implementation files; the allowance is for tests only."""
    written = _session(tmp_path)

    assert _lint_named_session_tests("  mypy: src/impl.py [union-attr] x1\n", tmp_path, written) == []


def test_findings_come_back_once_each_in_first_mention_order(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    files = [tmp_path / "tests" / "test_a.py", tmp_path / "tests" / "test_b.py"]
    for f in files:
        f.write_text("x = 1\n", encoding="utf-8")
    failures = (
        "  mypy: tests/test_b.py [union-attr] x1\n"
        "  ruff check: tests/test_a.py [E402] x1\n"
        "  mypy: tests/test_b.py [arg-type] x1\n"
    )

    found = _lint_named_session_tests(failures, tmp_path, [f.resolve() for f in files])

    assert found == ["tests/test_b.py", "tests/test_a.py"]


def test_a_stub_file_is_never_unlocked(tmp_path: Path) -> None:
    """Python test files only: the guard reads `.py`, so the allowance must not name anything else."""
    (tmp_path / "tests").mkdir()
    stub = tmp_path / "tests" / "test_mine.pyi"
    stub.write_text("x: int\n", encoding="utf-8")

    assert (
        _lint_named_session_tests("tests/test_mine.pyi:1: error: x  [misc]\n", tmp_path, [stub.resolve()])
        == []
    )


def test_a_non_python_test_is_never_unlocked(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    ts = tmp_path / "tests" / "test_mine.ts"
    ts.write_text("x\n", encoding="utf-8")

    assert _lint_named_session_tests("tests/test_mine.ts:1:1: E402 x\n", tmp_path, [ts.resolve()]) == []


# --- the guard (P2, D2) ---------------------------------------------------------------------------

_TEST = """\
import re


def test_group() -> None:
    m = re.match(r"(a)", "a")
    assert m.group(1) == "a"


class TestTotals:
    def test_sum(self) -> None:
        assert sum([1, 2]) == 3
"""


def test_adding_an_assert_does_not_weaken() -> None:
    """The usual union-attr fix *adds* an assert, which strengthens the test."""
    fixed = _TEST.replace(
        '    assert m.group(1) == "a"', '    assert m is not None\n    assert m.group(1) == "a"'
    )

    assert _weakens_test(_TEST, fixed) is None


def test_reformatting_does_not_weaken() -> None:
    assert _weakens_test(_TEST, _TEST.replace("sum([1, 2])", "sum( [1,2] )")) is None


def test_removing_an_assert_weakens() -> None:
    reason = _weakens_test(_TEST, _TEST.replace('    assert m.group(1) == "a"\n', "    _ = m\n"))

    assert reason is not None
    assert "assert" in reason


def test_changing_an_assert_weakens() -> None:
    assert _weakens_test(_TEST, _TEST.replace("== 3", "== 4")) is not None


def test_removing_a_test_function_weakens() -> None:
    without = _TEST.split("class TestTotals")[0]

    reason = _weakens_test(_TEST, without)

    assert reason is not None
    assert "TestTotals.test_sum" in reason


def test_renaming_a_test_function_weakens() -> None:
    assert _weakens_test(_TEST, _TEST.replace("def test_group", "def check_group")) is not None


def test_removing_an_async_test_weakens() -> None:
    with_async = _TEST + "\n\nasync def test_later() -> None:\n    pass\n"

    reason = _weakens_test(with_async, _TEST)

    assert reason is not None
    assert "test_later" in reason


def test_an_old_file_that_does_not_parse_cannot_be_judged() -> None:
    assert _weakens_test("def broken(:\n", _TEST) is None


def test_a_duplicated_assert_removed_once_weakens() -> None:
    """Asserts are counted, not collected into a set: two identical checks are two checks."""
    twice = _TEST + "\n\ndef test_again() -> None:\n    assert sum([1, 2]) == 3\n"
    once = twice.replace(
        "\n\ndef test_again() -> None:\n    assert sum([1, 2]) == 3\n",
        "\n\ndef test_again() -> None:\n    pass\n",
    )

    assert _weakens_test(twice, once) is not None


# --- the guard inside apply_files (P2, D8) ---------------------------------------------------------


def _refine_repo(tmp_path: Path) -> dict[Path, list[Path]]:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mine.py").write_text(_TEST, encoding="utf-8")
    (tmp_path / "impl.py").write_text("X = 1\n", encoding="utf-8")
    return {
        tmp_path.resolve(): [
            (tmp_path / "tests" / "test_mine.py").resolve(),
            (tmp_path / "impl.py").resolve(),
        ]
    }


def test_refine_may_add_an_assert_to_a_session_test(tmp_path: Path) -> None:
    tracker = _refine_repo(tmp_path)
    edit = {
        "find": '    assert m.group(1) == "a"',
        "replace": '    assert m is not None\n    assert m.group(1) == "a"',
    }

    change = apply_files(
        [{"path": "tests/test_mine.py", "edits": [edit]}],
        tmp_path,
        written_tracker=tracker,
        grounded=True,
        editable_existing=frozenset(),
    )

    assert [Path(f).name for f in change.files] == ["test_mine.py"]
    assert "assert m is not None" in (tmp_path / "tests" / "test_mine.py").read_text(encoding="utf-8")


def test_refine_changing_an_assert_is_refused_and_the_rest_applied(tmp_path: Path) -> None:
    tracker = _refine_repo(tmp_path)

    change = apply_files(
        [
            {"path": "tests/test_mine.py", "edits": [{"find": "== 3", "replace": "== 4"}]},
            {"path": "impl.py", "content": "X = 2\n"},
        ],
        tmp_path,
        written_tracker=tracker,
        grounded=True,
        editable_existing=frozenset(),
    )

    assert [Path(f).name for f in change.files] == ["impl.py"]
    assert "tests/test_mine.py" in change.summary
    assert (tmp_path / "tests" / "test_mine.py").read_text(encoding="utf-8") == _TEST


def test_refine_rewriting_a_session_test_without_a_test_is_refused(tmp_path: Path) -> None:
    """The guard holds for the content form too: a session file may be resent in full."""
    tracker = _refine_repo(tmp_path)

    with pytest.raises(CodegenError) as caught:
        apply_files(
            [{"path": "tests/test_mine.py", "content": _TEST.split("class TestTotals")[0]}],
            tmp_path,
            written_tracker=tracker,
            grounded=True,
            editable_existing=frozenset(),
        )

    assert "tests/test_mine.py" in caught.value.empty_summary  # routed to a corrective retry
    assert "TestTotals.test_sum" in caught.value.empty_summary
    assert (tmp_path / "tests" / "test_mine.py").read_text(encoding="utf-8") == _TEST


def test_a_refused_test_edit_is_named_when_a_repair_pass_follows(tmp_path: Path) -> None:
    """The repair retry quotes this message to the model: the refusal must not vanish in it."""
    tracker = _refine_repo(tmp_path)

    with pytest.raises(CodegenError) as caught:
        apply_files(
            [
                {"path": "tests/test_mine.py", "edits": [{"find": "== 3", "replace": "== 4"}]},
                {"path": "impl.py", "edits": [{"find": "not in the file", "replace": "x"}]},
            ],
            tmp_path,
            written_tracker=tracker,
            grounded=True,
            editable_existing=frozenset(),
        )

    assert "refused test edits, not applied: tests/test_mine.py" in str(caught.value)
    assert caught.value.failed_edit_paths == ["impl.py"]


def test_outside_refine_the_guard_stands_aside(tmp_path: Path) -> None:
    """author_tests may rewrite the tests it wrote; only refine is held to them."""
    tracker = _refine_repo(tmp_path)

    change = apply_files(
        [{"path": "tests/test_mine.py", "content": _TEST.replace("== 3", "== 4")}],
        tmp_path,
        written_tracker=tracker,
        grounded=True,
    )

    assert [Path(f).name for f in change.files] == ["test_mine.py"]


# --- the refine prompt (P2, D1c/D7) -----------------------------------------------------------------


class _ScriptedLLM:
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
        return CompletionResult(
            text=self._responses.pop(0),
            model="fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
        )


def _files(files: dict[str, str]) -> str:
    payload: dict[str, Any] = {"files": [{"path": p, "content": c} for p, c in files.items()], "summary": "s"}
    return json.dumps(payload)


_SPEC: dict[str, Any] = {"title": "Totals", "description": "Sum things.", "acceptance_criteria": ["sums"]}


async def _session_adapter(tmp_path: Path, reply: str) -> tuple[LLMCodegenAdapter, _ScriptedLLM]:
    llm = _ScriptedLLM([_files({"tests/test_mine.py": _TEST}), reply])
    adapter = LLMCodegenAdapter(llm)
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="SSPN-93")
    return adapter, llm


async def test_a_lint_named_session_test_is_offered_to_refine(tmp_path: Path) -> None:
    adapter, llm = await _session_adapter(tmp_path, _files({"impl.py": "X = 1\n"}))

    await adapter.refine(
        spec=_SPEC,
        path=str(tmp_path),
        issue_key="SSPN-93",
        failures="  mypy: tests/test_mine.py [union-attr] x1\n",
    )

    prompt = llm.calls[-1][1].content
    assert "Do NOT modify test files" in prompt  # the rule stands for everything else
    assert _lint_test_allowance(["tests/test_mine.py"]) in prompt


async def test_a_pytest_failure_offers_no_test_to_refine(tmp_path: Path) -> None:
    adapter, llm = await _session_adapter(tmp_path, _files({"impl.py": "X = 1\n"}))

    await adapter.refine(
        spec=_SPEC,
        path=str(tmp_path),
        issue_key="SSPN-93",
        failures="FAILED tests/test_mine.py::test_group - x\n",
    )

    assert _lint_test_allowance(["tests/test_mine.py"]) not in llm.calls[-1][1].content


async def test_a_test_the_session_only_edited_is_never_offered(tmp_path: Path) -> None:
    """Pre-existing means existed before the run: implement editing a test does not make it ours."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_old.py").write_text(_TEST, encoding="utf-8")
    implement = json.dumps(
        {
            "files": [
                {"path": "impl.py", "content": "X = 1\n"},
                {"path": "tests/test_old.py", "edits": [{"find": "== 3", "replace": "== 3  # still"}]},
            ],
            "summary": "s",
        }
    )
    llm = _ScriptedLLM([implement, _files({"impl.py": "X = 2\n"})])
    adapter = LLMCodegenAdapter(llm)
    await adapter.implement(spec=_SPEC, path=str(tmp_path), issue_key="SSPN-93")

    await adapter.refine(
        spec=_SPEC,
        path=str(tmp_path),
        issue_key="SSPN-93",
        failures="  mypy: tests/test_old.py [union-attr] x1\n",
    )

    assert _lint_test_allowance(["tests/test_old.py"]) not in llm.calls[-1][1].content


async def test_refine_is_held_to_the_guard_and_told_why(tmp_path: Path) -> None:
    """End to end through refine: the weakening reply is refused, and the retry says why."""
    weakening = _files({"tests/test_mine.py": _TEST.split("class TestTotals")[0]})
    adapter, llm = await _session_adapter(tmp_path, weakening)
    llm._responses.append(_files({"impl.py": "X = 1\n"}))

    change = await adapter.refine(
        spec=_SPEC,
        path=str(tmp_path),
        issue_key="SSPN-93",
        failures="  mypy: tests/test_mine.py [union-attr] x1\n",
    )

    assert [Path(f).name for f in change.files] == ["impl.py"]
    assert (tmp_path / "tests" / "test_mine.py").read_text(encoding="utf-8") == _TEST
    retry = llm.calls[-1][1].content
    assert "TestTotals.test_sum" in retry
    assert "every existing assert and test function must stay" in retry
