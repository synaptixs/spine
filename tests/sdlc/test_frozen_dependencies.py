"""Existing Node repositories: codegen may not change dependencies nobody will install (B177, D3/D9).

`<pm> install` runs once, before codegen (`NodeToolEnvironment.ensure`). On a field run refine added
`vitest` to a Jest repository's `package.json` four times; every run still failed `Cannot find module
'vitest'`, because nothing installs a dependency declared after that. The refusal sits in the shared
`apply_files`, so single-shot codegen and the agentic `write_files` tool get the same rule, and it is
keyed on an existing TypeScript layout so a project Spine scaffolds itself keeps today's behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.agentic.codegen_tools import CodegenSession, build_codegen_tools
from orchestrator.sdlc.attempt_record import AttemptTrace
from orchestrator.sdlc.codegen import CodeChange, CodegenError, apply_files

_PKG = {"name": "app", "scripts": {"test": "jest"}, "devDependencies": {"jest": "29.7.0"}}


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(json.dumps(_PKG, indent=2) + "\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    return tmp_path


def _add_vitest_edit() -> dict[str, object]:
    return {
        "path": "package.json",
        "edits": [{"find": '"jest": "29.7.0"', "replace": '"jest": "29.7.0",\n    "vitest": "2.1.0"'}],
    }


def _apply(
    root: Path, files: list[dict[str, object]], *, frozen: bool, trace: AttemptTrace | None = None
) -> CodeChange:
    return apply_files(
        files, root, written_tracker={}, grounded=False, frozen_dependencies=frozen, trace=trace
    )


def test_adding_a_dependency_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = (root / "package.json").read_text(encoding="utf-8")
    with pytest.raises(CodegenError) as caught:
        _apply(root, [_add_vitest_edit()], frozen=True)
    assert (root / "package.json").read_text(encoding="utf-8") == before
    refusal = caught.value.refusal or ""
    assert "package.json" in refusal and "installed once" in refusal


def test_writing_a_lockfile_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(CodegenError, match="installed once"):
        _apply(root, [{"path": "package-lock.json", "content": '{"lockfileVersion": 3}\n'}], frozen=True)
    assert not (root / "package-lock.json").exists()


def test_a_new_package_json_that_declares_dependencies_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    nested = {"name": "tool", "dependencies": {"left-pad": "1.3.0"}}
    with pytest.raises(CodegenError, match="installed once"):
        _apply(root, [{"path": "tools/package.json", "content": json.dumps(nested)}], frozen=True)
    assert not (root / "tools" / "package.json").exists()


def test_a_non_dependency_edit_to_package_json_is_allowed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    edit: dict[str, object] = {
        "path": "package.json",
        "edits": [{"find": '"test": "jest"', "replace": '"test": "jest --ci"'}],
    }
    change = _apply(root, [edit], frozen=True)
    assert change.files
    assert json.loads((root / "package.json").read_text())["scripts"]["test"] == "jest --ci"


def test_unfrozen_keeps_todays_behaviour(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    change = _apply(root, [_add_vitest_edit()], frozen=False)
    assert change.files
    assert "vitest" in json.loads((root / "package.json").read_text())["devDependencies"]


def test_the_rest_of_a_change_lands_and_the_refusal_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    trace = AttemptTrace()
    change = _apply(
        root,
        [
            {"path": "src/calc.ts", "content": "export const add = (a: number, b: number) => a + b;\n"},
            _add_vitest_edit(),
        ],
        frozen=True,
        trace=trace,
    )
    assert (root / "src" / "calc.ts").exists()
    assert "vitest" not in (root / "package.json").read_text()
    assert "package.json" in change.summary and "dependencies" in change.summary
    assert ("package.json", "dependency_frozen") in trace.dropped  # the attempt record says why


async def test_the_agentic_write_tool_refuses_too(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    session = CodegenSession(tracker={})
    tools = {
        t.spec.name: t
        for t in build_codegen_tools(root, grounded=False, session=session, frozen_dependencies=True)
    }
    out = await tools["write_files"].run({"files": [_add_vitest_edit()]})
    assert out.startswith("error:") and "installed once" in out
    out = await tools["write_files"].run(
        {"files": [{"path": "src/a.ts", "content": "export const a = 1;\n"}, _add_vitest_edit()]}
    )
    assert "wrote 1 file" in out and "package.json" in out  # the refusal reaches the model
    assert "vitest" not in (root / "package.json").read_text()


async def test_existing_typescript_refine_cannot_add_a_dependency(tmp_path: Path) -> None:
    """Through the adapter: an existing-mode TypeScript layout freezes dependencies; refine gets one
    corrective retry naming why, and an exhausted refine changes nothing rather than failing the run."""
    from orchestrator.sdlc.codegen import LLMCodegenAdapter
    from orchestrator.sdlc.layout import resolve_layout
    from tests.sdlc.test_codegen import _SPEC, _ScriptedLLM

    root = _repo(tmp_path)
    layout = resolve_layout(root, mode="existing", language="typescript")
    edit = _add_vitest_edit()
    reply = json.dumps({"files": [edit], "summary": "add vitest"})
    llm = _ScriptedLLM([reply, reply, reply])
    adapter = LLMCodegenAdapter(llm, layout=layout)
    change = await adapter.refine(
        spec=_SPEC, path=str(root), issue_key="T-1", failures="Cannot find module 'vitest'"
    )
    assert not change.files
    assert "vitest" not in (root / "package.json").read_text()
    assert len(llm.calls) == 2 and "installed once" in llm.calls[1][-1].content


async def test_new_typescript_project_may_still_add_a_dependency(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import LLMCodegenAdapter
    from orchestrator.sdlc.layout import resolve_layout
    from tests.sdlc.test_codegen import _SPEC, _ScriptedLLM

    root = _repo(tmp_path)
    layout = resolve_layout(root, mode="new", language="typescript", repo="app")
    llm = _ScriptedLLM([json.dumps({"files": [_add_vitest_edit()], "summary": "add vitest"})])
    adapter = LLMCodegenAdapter(llm, layout=layout)
    change = await adapter.refine(spec=_SPEC, path=str(root), issue_key="T-1", failures="Cannot find module")
    assert change.files
    assert "vitest" in (root / "package.json").read_text()


# ---- review fixes (PR-1 maintainer review, 2026-11-24) ------------------------------------------


def test_a_non_utf8_file_resent_in_full_is_still_a_recoverable_skip(tmp_path: Path) -> None:
    """The frozen check read every existing file before looking at its name; a Latin-1 file then
    raised UnicodeDecodeError past the grounded create-only guard, which never reads it."""
    root = _repo(tmp_path)
    (root / "src" / "legacy.ts").write_bytes(b"export const name = 'caf\xe9';\n")
    tracked = {root.resolve(): [(root / "src" / "other.ts").resolve()]}
    with pytest.raises(CodegenError, match="repair pass"):
        apply_files(
            [{"path": "src/legacy.ts", "content": "export const name = 'cafe';\n"}],
            root,
            written_tracker=tracked,
            grounded=True,
            frozen_dependencies=True,
        )


def test_grounded_refine_refuses_a_dependency_edit_as_frozen(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    tracked = {root.resolve(): [(root / "src" / "calc.ts").resolve()]}
    with pytest.raises(CodegenError, match="installed once"):
        apply_files(
            [_add_vitest_edit()],
            root,
            written_tracker=tracked,
            grounded=True,
            editable_existing=frozenset({"package.json"}),
            frozen_dependencies=True,
        )


def test_the_repair_retry_also_names_the_refused_manifest(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "src" / "a.ts").write_text("export const a = 1;\n", encoding="utf-8")
    bad_anchor: dict[str, object] = {"path": "src/a.ts", "edits": [{"find": "no such text", "replace": "x"}]}
    with pytest.raises(CodegenError, match="repair pass") as caught:
        _apply(root, [bad_anchor, _add_vitest_edit()], frozen=True)
    assert "package.json" in str(caught.value) and "installed once" in str(caught.value)


def test_a_utf8_bom_package_json_still_allows_a_scripts_edit(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "package.json").write_text("﻿" + json.dumps(_PKG, indent=2) + "\n", encoding="utf-8")
    edit: dict[str, object] = {
        "path": "package.json",
        "edits": [{"find": '"test": "jest"', "replace": '"test": "jest --ci"'}],
    }
    assert _apply(root, [edit], frozen=True).files


@pytest.mark.parametrize(
    ("find", "replace"),
    [
        ('"name": "app"', '"name": "app",\n  "workspaces": ["packages/*"]'),
        ('"name": "app"', '"name": "app",\n  "pnpm": {"overrides": {"left-pad": "1.3.0"}}'),
    ],
)
def test_workspace_and_pnpm_settings_are_frozen_too(tmp_path: Path, find: str, replace: str) -> None:
    root = _repo(tmp_path)
    with pytest.raises(CodegenError, match="installed once"):
        edit: dict[str, object] = {"path": "package.json", "edits": [{"find": find, "replace": replace}]}
        _apply(root, [edit], frozen=True)
