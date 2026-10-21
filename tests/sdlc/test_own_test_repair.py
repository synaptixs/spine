"""The repair turn for a test the run wrote (test-trust C, SSPN-228) — the adapter half.

Refine is held to "never change a test to make it pass", and the guard enforces it: an edit that drops
or changes an assert is refused. That is right for tests the ticket cares about and wrong for a test the
model itself invented and got wrong, the commonest way a run is lost (23 of 41 in P6). A person looks at
such a test and fixes or deletes it. This turn gives the model that one move and nothing else: it may
rewrite **files this session created**, with the weakened-test guard stood aside for those files only;
any other file in the reply is refused. A repaired test proves less than one that passed untouched, so
the loop (next slice) labels it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.codegen import LLMCodegenAdapter, apply_files

_TEST = "def test_total() -> None:\n    assert sum([1, 2]) == 3\n"


def _repo(tmp_path: Path) -> dict[Path, list[Path]]:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mine.py").write_text(_TEST, encoding="utf-8")
    (tmp_path / "impl.py").write_text("X = 1\n", encoding="utf-8")
    return {
        tmp_path.resolve(): [
            (tmp_path / "tests" / "test_mine.py").resolve(),
            (tmp_path / "impl.py").resolve(),
        ]
    }


def test_only_paths_lets_the_named_test_be_rewritten_even_when_that_changes_an_assert(tmp_path: Path) -> None:
    tracker = _repo(tmp_path)

    change = apply_files(
        [{"path": "tests/test_mine.py", "content": _TEST.replace("== 3", "== 4")}],
        tmp_path,
        written_tracker=tracker,
        grounded=True,
        only_paths=frozenset({"tests/test_mine.py"}),
    )

    assert [Path(f).name for f in change.files] == ["test_mine.py"]
    assert "== 4" in (tmp_path / "tests" / "test_mine.py").read_text(encoding="utf-8")


def test_only_paths_refuses_every_other_file_in_the_reply(tmp_path: Path) -> None:
    tracker = _repo(tmp_path)

    change = apply_files(
        [
            {"path": "tests/test_mine.py", "content": _TEST.replace("== 3", "== 4")},
            {"path": "impl.py", "content": "X = 2\n"},
            {"path": "tests/test_new.py", "content": "def test_x():\n    pass\n"},
        ],
        tmp_path,
        written_tracker=tracker,
        grounded=True,
        only_paths=frozenset({"tests/test_mine.py"}),
    )

    assert [Path(f).name for f in change.files] == ["test_mine.py"]
    assert (tmp_path / "impl.py").read_text(encoding="utf-8") == "X = 1\n"
    assert not (tmp_path / "tests" / "test_new.py").exists()


def test_without_only_paths_nothing_changes(tmp_path: Path) -> None:
    """The restriction is opt-in: every other caller behaves exactly as before."""
    tracker = _repo(tmp_path)

    change = apply_files(
        [{"path": "impl.py", "content": "X = 2\n"}],
        tmp_path,
        written_tracker=tracker,
        grounded=True,
    )

    assert [Path(f).name for f in change.files] == ["impl.py"]


# --- the adapter method --------------------------------------------------------------------------------


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
    payload: dict[str, Any] = {
        "files": [{"path": p, "content": c} for p, c in files.items()],
        "summary": "fixed",
    }
    return json.dumps(payload)


_SPEC: dict[str, Any] = {"title": "Totals", "description": "Sum things.", "acceptance_criteria": ["sums"]}
_FAILURE = (
    "FAIL: test_total (test_mine.T)\nAssertionError: 3 != 4\n\nRan 5 tests in 0.01s\n\nFAILED (failures=1)\n"
)


async def _adapter(tmp_path: Path, repair_reply: str) -> tuple[LLMCodegenAdapter, _ScriptedLLM]:
    llm = _ScriptedLLM([_files({"tests/test_mine.py": _TEST}), repair_reply])
    adapter = LLMCodegenAdapter(llm)
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="SSPN-228")
    return adapter, llm


async def test_the_repair_turn_may_rewrite_a_test_the_session_created(tmp_path: Path) -> None:
    adapter, llm = await _adapter(tmp_path, _files({"tests/test_mine.py": _TEST.replace("== 3", "== 4")}))

    change = await adapter.repair_own_tests(
        spec=_SPEC,
        path=str(tmp_path),
        issue_key="SSPN-228",
        failures=_FAILURE,
        tests=["tests/test_mine.py"],
    )

    assert [Path(f).name for f in change.files] == ["test_mine.py"]
    assert "== 4" in (tmp_path / "tests" / "test_mine.py").read_text(encoding="utf-8")
    prompt = llm.calls[-1][1].content
    assert "tests/test_mine.py" in prompt and "FAILURE OUTPUT" in prompt
    assert "may rewrite only" in prompt.lower()


async def test_the_repair_turn_cannot_touch_production_code(tmp_path: Path) -> None:
    (tmp_path / "impl.py").write_text("X = 1\n", encoding="utf-8")
    adapter, _ = await _adapter(
        tmp_path,
        _files({"tests/test_mine.py": _TEST.replace("== 3", "== 4"), "impl.py": "X = 2\n"}),
    )

    await adapter.repair_own_tests(
        spec=_SPEC, path=str(tmp_path), issue_key="SSPN-228", failures=_FAILURE, tests=["tests/test_mine.py"]
    )

    assert (tmp_path / "impl.py").read_text(encoding="utf-8") == "X = 1\n"


async def test_a_test_the_session_did_not_create_is_not_offered_and_costs_no_call(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_old.py").write_text(_TEST, encoding="utf-8")
    adapter, llm = await _adapter(tmp_path, _files({"tests/test_old.py": "x = 1\n"}))
    calls_before = len(llm.calls)

    change = await adapter.repair_own_tests(
        spec=_SPEC, path=str(tmp_path), issue_key="SSPN-228", failures=_FAILURE, tests=["tests/test_old.py"]
    )

    assert change.files == [] and len(llm.calls) == calls_before
    assert (tmp_path / "tests" / "test_old.py").read_text(encoding="utf-8") == _TEST


async def test_the_ordinary_refine_prompt_is_unchanged_by_the_repair_turn_existing(tmp_path: Path) -> None:
    """Refine keeps its rule: nothing about this turn leaks into the refine prompt."""
    adapter, llm = await _adapter(tmp_path, _files({"impl.py": "X = 1\n"}))

    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="SSPN-228", failures=_FAILURE)

    prompt = llm.calls[-1][1].content
    assert "Do NOT modify test files" in prompt and "may rewrite only" not in prompt.lower()
