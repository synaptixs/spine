"""SSPN-223: the author_tests and refine prompts carry the repository's own test location.

Driven through the real adapter with a scripted model, so the assertion is on the prompt the model
would have been sent. The grounder is a stand-in with exactly the optional method the adapter looks
for, mirroring how `context_for_required_entry_points` is reached: the `CodegenGrounder` Protocol is
not extended.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.pkg import FactStore, GroundedRetriever
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.codegen import LLMCodegenAdapter
from orchestrator.sdlc.grounding import PKGCodegenGrounder

_SPEC: dict[str, Any] = {"title": "t", "summary": "s", "acceptance_criteria": ["it works"]}
_MARK = "WHERE THIS REPOSITORY KEEPS TESTS FOR THESE MODULES"
_ZERO = "no tests ran in 0.01s\n[spine] the runner executed 0 tests, so this run proves nothing"
_FAILED = "FAILED tests/test_x.py::test_a - AssertionError\n1 failed, 3 passed in 0.39s"


class _LLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    async def complete(self, messages: list[Message], **_: object) -> CompletionResult:
        self.calls.append(list(messages))
        return CompletionResult(
            text=self._responses.pop(0),
            model="fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    def last_user(self) -> str:
        return next(m.content for m in reversed(self.calls[-1]) if m.role == "user")


def _files(files: dict[str, str]) -> str:
    return json.dumps({"files": [{"path": p, "content": c} for p, c in files.items()], "summary": "ok"})


class _Grounder:
    def __init__(self, covering: list[str] | None = None, *, explode: bool = False) -> None:
        self._covering = covering or []
        self._explode = explode

    def context_for_spec(self, spec: dict[str, Any]) -> str:
        return ""

    def covering_test_files(self, paths: list[str]) -> list[str]:
        if self._explode:
            raise RuntimeError("graph unavailable")
        return list(self._covering)


class _PlainGrounder:
    """Only the Protocol's method: no covering-test method at all."""

    def context_for_spec(self, spec: dict[str, Any]) -> str:
        return ""


def _repo(root: Path, tests_in: str) -> None:
    (root / "pkg" / "core").mkdir(parents=True)
    (root / "pkg" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    target = root / tests_in
    target.mkdir(parents=True, exist_ok=True)
    (target / "test_existing.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")


async def _implemented(root: Path, tests_in: str, grounder: Any):  # type: ignore[no-untyped-def]
    _repo(root, tests_in)
    llm = _LLM(
        [
            _files({"pkg/core/newmod.py": "X = 1\n"}),
            _files({"pkg/core/test_new.py": "def test_new() -> None:\n    assert True\n"}),
            _files({"pkg/core/other.py": "Y = 2\n"}),
        ]
    )
    adapter = LLMCodegenAdapter(llm, grounder=grounder)
    await adapter.implement(spec=_SPEC, path=str(root), issue_key="T-1")
    return adapter, llm


async def test_refine_after_a_run_that_executed_nothing_names_where_the_repository_keeps_its_tests(
    tmp_path: Path,
) -> None:
    adapter, llm = await _implemented(tmp_path, "pkg/core/tests", _Grounder())
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures=_ZERO)
    prompt = llm.last_user()
    assert prompt.count(_MARK) == 1
    assert "pkg/core/tests" in prompt


async def test_refine_after_an_ordinary_failure_is_untouched(tmp_path: Path) -> None:
    """A run that executed tests is working: it never sees the block, so nothing that works changes."""
    adapter, llm = await _implemented(tmp_path, "pkg/core/tests", _Grounder())
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures=_FAILED)
    assert _MARK not in llm.last_user()


async def test_author_tests_never_carries_the_block(tmp_path: Path) -> None:
    """Replay B: always-on would have changed 39 of 55 working runs, 19 of the 26 resolved ones."""
    adapter, llm = await _implemented(tmp_path, "pkg/core/tests", _Grounder())
    await adapter.author_tests(spec=_SPEC, path=str(tmp_path), issue_key="T-1")
    assert _MARK not in llm.last_user()


async def test_a_repository_that_keeps_its_tests_in_tests_gets_the_prompt_it_gets_today(
    tmp_path: Path,
) -> None:
    adapter, llm = await _implemented(tmp_path, "tests", _Grounder(covering=["tests/test_existing.py"]))
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures=_ZERO)
    assert _MARK not in llm.last_user()


async def test_the_graphs_covering_tests_are_used_when_the_grounder_has_them(tmp_path: Path) -> None:
    (tmp_path / "tests" / "utils_tests").mkdir(parents=True)
    (tmp_path / "tests" / "utils_tests" / "test_html.py").write_text("", encoding="utf-8")
    adapter, llm = await _implemented(
        tmp_path, "tests/other", _Grounder(covering=["tests/utils_tests/test_html.py"])
    )
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures=_ZERO)
    prompt = llm.last_user()
    assert prompt.count(_MARK) == 1
    assert "tests/utils_tests" in prompt


async def test_a_grounder_without_the_optional_method_still_gets_the_sibling_rule(tmp_path: Path) -> None:
    adapter, llm = await _implemented(tmp_path, "pkg/core/tests", _PlainGrounder())
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures=_ZERO)
    assert "pkg/core/tests" in llm.last_user()


async def test_no_grounder_at_all_still_gets_the_sibling_rule(tmp_path: Path) -> None:
    adapter, llm = await _implemented(tmp_path, "pkg/core/tests", None)
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures=_ZERO)
    assert "pkg/core/tests" in llm.last_user()


async def test_a_grounder_that_fails_never_breaks_codegen(tmp_path: Path) -> None:
    """Grounding is an enhancement: a graph error costs the covering signal, not the run."""
    adapter, llm = await _implemented(tmp_path, "pkg/core/tests", _Grounder(explode=True))
    await adapter.refine(spec=_SPEC, path=str(tmp_path), issue_key="T-1", failures=_ZERO)
    assert "pkg/core/tests" in llm.last_user()


async def test_other_languages_keep_their_layout_unchanged(tmp_path: Path) -> None:
    """Measured on Python only (sympy, django): a Java or C# layout is not second-guessed."""
    from orchestrator.sdlc.layout import TargetLayout

    _repo(tmp_path, "pkg/core/tests")
    layout = TargetLayout("p", "src/main/java", "src/test/java", False, "existing", language="java")
    llm = _LLM([_files({"pkg/core/Newmod.java": "class Newmod {}\n"}), _files({"x/T.java": "class T {}\n"})])
    adapter = LLMCodegenAdapter(llm, grounder=_Grounder(), layout=layout)
    assert adapter._test_location_block(tmp_path, _SPEC, _ZERO) == ""


# ---- the PKG side: covering tests of the code in the touched files ---------------------------


def _fn(nid: str, name: str, file: str, line: int = 1) -> Node:
    return Node(
        id=nid, kind=NodeKind.FUNCTION, name=name, language="python", provenance=Provenance(file, line)
    )


def _store() -> FactStore:
    batch = FactBatch()
    for node in (
        _fn("py:core.validate", "validate", "core.py", 10),
        _fn("py:web.handler", "handler", "web.py", 5),
        _fn("py:tests.test_core.test_validate", "test_validate", "tests/test_core.py", 3),
    ):
        batch.add_node(node)
    batch.add_edge(Edge("py:web.handler", "py:core.validate", EdgeKind.CALLS, Provenance("web.py", 6)))
    batch.add_edge(
        Edge(
            "py:tests.test_core.test_validate",
            "py:core.validate",
            EdgeKind.CALLS,
            Provenance("tests/test_core.py", 4),
        )
    )
    return FactStore(batch)


def test_the_pkg_grounder_lists_the_test_files_that_reach_the_touched_code(tmp_path: Path) -> None:
    store = _store()
    grounder = PKGCodegenGrounder(GroundedRetriever(store), root=tmp_path, store=store)
    assert grounder.covering_test_files(["core.py"]) == ["tests/test_core.py"]


def test_code_no_test_reaches_has_no_covering_file(tmp_path: Path) -> None:
    store = _store()
    grounder = PKGCodegenGrounder(GroundedRetriever(store), root=tmp_path, store=store)
    assert grounder.covering_test_files(["web.py"]) == []
    assert grounder.covering_test_files(["not_in_graph.py"]) == []


def test_a_grounder_built_without_a_store_has_no_covering_files(tmp_path: Path) -> None:
    grounder = PKGCodegenGrounder(GroundedRetriever(_store()), root=tmp_path)
    assert grounder.covering_test_files(["core.py"]) == []
