"""Refine is told when a test it wrote imports a module the environment lacks (test-trust B, SSPN-228).

django-14349 (P6): the model wrote `import pytest` into a test for a Django project whose runner is unittest.
The run died on `ModuleNotFoundError: No module named 'pytest'` in a file it wrote, and refine, told never to
touch tests, went looking for a production fix that does not exist. A person says one sentence: that import is
yours, this runner has no such library, use what the project's own tests use. This pins that sentence and the
narrow door it opens: only a file this session created, only the module the failure names, only when the
repository does not itself contain that module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.sdlc.codegen import LLMCodegenAdapter, _import_fault_note, _import_faulty_session_tests

_FAILURE = (
    "ERROR: test_url_validator_unsafe_chars (unittest.loader._FailedTest)\n"
    "ImportError: Failed to import test module: test_url_validator_unsafe_chars\n"
    "Traceback (most recent call last):\n"
    '  File "/testbed/tests/test_url_validator_unsafe_chars.py", line 11, in <module>\n'
    "    import pytest\n"
    "ModuleNotFoundError: No module named 'pytest'\n"
)


def _repo(tmp_path: Path, source: str = "import pytest\n\n\ndef test_a():\n    assert 1\n") -> list[Path]:
    (tmp_path / "tests").mkdir(exist_ok=True)
    mine = tmp_path / "tests" / "test_url_validator_unsafe_chars.py"
    mine.write_text(source, encoding="utf-8")
    return [mine.resolve()]


def test_a_session_test_that_imports_the_missing_module_is_named(tmp_path: Path) -> None:
    created = _repo(tmp_path)

    assert _import_faulty_session_tests(_FAILURE, tmp_path, created) == [
        ("tests/test_url_validator_unsafe_chars.py", "pytest")
    ]


def test_the_other_import_spellings_count(tmp_path: Path) -> None:
    for source in (
        "import pytest as p\n",
        "from pytest import raises\n",
        "import os, pytest\n",
        "def test_a():\n    import pytest\n",
    ):
        created = _repo(tmp_path, source)
        assert _import_faulty_session_tests(_FAILURE, tmp_path, created), source


def test_a_submodule_failure_names_the_top_level_module(tmp_path: Path) -> None:
    created = _repo(tmp_path, "from numpy.testing import assert_allclose\n")
    failure = "ModuleNotFoundError: No module named 'numpy.testing'\n"

    assert _import_faulty_session_tests(failure, tmp_path, created) == [
        ("tests/test_url_validator_unsafe_chars.py", "numpy")
    ]


def test_a_test_the_session_did_not_create_is_never_named(tmp_path: Path) -> None:
    _repo(tmp_path)

    assert _import_faulty_session_tests(_FAILURE, tmp_path, []) == []


def test_a_created_file_that_does_not_import_the_module_is_not_named(tmp_path: Path) -> None:
    created = _repo(tmp_path, "import os\n\n\ndef test_a():\n    assert os.sep\n")

    assert _import_faulty_session_tests(_FAILURE, tmp_path, created) == []


def test_a_module_the_repository_itself_contains_is_a_code_problem_not_an_environment_one(
    tmp_path: Path,
) -> None:
    """A missing `mypkg` that the repo has means the change broke an import: that goes to refine as before."""
    (tmp_path / "mypkg").mkdir()
    created = _repo(tmp_path, "import mypkg\n")

    assert (
        _import_faulty_session_tests("ModuleNotFoundError: No module named 'mypkg'\n", tmp_path, created)
        == []
    )


def test_a_module_in_src_counts_as_the_repository_having_it(tmp_path: Path) -> None:
    (tmp_path / "src" / "mypkg").mkdir(parents=True)
    created = _repo(tmp_path, "import mypkg\n")

    assert _import_faulty_session_tests("No module named 'mypkg'\n", tmp_path, created) == []


def test_a_non_python_file_is_never_named(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    spec = tmp_path / "tests" / "test_x.js"
    spec.write_text("import pytest\n", encoding="utf-8")

    assert _import_faulty_session_tests(_FAILURE, tmp_path, [spec.resolve()]) == []


def test_each_pair_comes_back_once(tmp_path: Path) -> None:
    created = _repo(tmp_path)

    found = _import_faulty_session_tests(_FAILURE + _FAILURE, tmp_path, created)

    assert len(found) == 1


def test_the_note_names_the_file_and_the_module_and_opens_only_that_door() -> None:
    note = _import_fault_note([("tests/test_x.py", "pytest")])

    assert "tests/test_x.py" in note and "`pytest`" in note
    assert "you wrote" in note and "may edit" in note.lower()
    assert "keep every test and every assert" in note.lower()
    assert "unittest" in note  # what to use instead, for the runner that has no pytest


def test_no_fault_no_note() -> None:
    assert _import_fault_note([]) == ""


# --- the refine prompt ------------------------------------------------------------------------------


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


_SPEC: dict[str, Any] = {
    "title": "Validators",
    "description": "Reject controls.",
    "acceptance_criteria": ["rejects"],
}


async def _adapter(tmp_path: Path, test_source: str) -> tuple[LLMCodegenAdapter, _ScriptedLLM]:
    llm = _ScriptedLLM(
        [_files({"tests/test_url_validator_unsafe_chars.py": test_source}), _files({"impl.py": "X = 1\n"})]
    )
    adapter = LLMCodegenAdapter(llm)
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="SSPN-228")
    return adapter, llm


async def test_the_refine_prompt_carries_the_note_when_the_run_wrote_the_faulty_import(
    tmp_path: Path,
) -> None:
    adapter, llm = await _adapter(
        tmp_path,
        "import pytest\n\n\ndef test_a():\n    with pytest.raises(ValueError):\n        raise ValueError\n",
    )

    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="SSPN-228", failures=_FAILURE)

    prompt = llm.calls[-1][1].content
    assert _import_fault_note([("tests/test_url_validator_unsafe_chars.py", "pytest")]) in prompt
    assert "Do NOT modify test files" in prompt  # the rule stands for everything else


async def test_the_refine_prompt_has_no_note_for_an_import_the_run_did_not_write(tmp_path: Path) -> None:
    adapter, llm = await _adapter(tmp_path, "import os\n\n\ndef test_a():\n    assert os.sep\n")

    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="SSPN-228", failures=_FAILURE)

    assert "ENVIRONMENT FAULT" not in llm.calls[-1][1].content
