"""Write/test/submit in-loop tools (Phase 5b).

The agent writes files, runs the tests, fixes, and submits — all in the loop.
``write_files`` routes through the same ``apply_files`` the single-shot codegen
uses, so every guard (path safety, stdlib shadow, brownfield create-only, size
caps, ruff-fix) is preserved; a guard rejection comes back as an observation the
model can act on. ``submit_changes`` is the terminal tool (the chosen explicit
convention) — it ends the loop and the accumulated changes feed the existing
test/verify machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.agentic.loop import Tool
from orchestrator.core.llm import ToolSpec
from orchestrator.sdlc.testrunner import SubprocessTestRunner, TestRunner

_FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "full content for a NEW file"},
                    "edits": {
                        "type": "array",
                        "description": "anchored find/replace for an EXISTING file",
                        "items": {
                            "type": "object",
                            "properties": {"find": {"type": "string"}, "replace": {"type": "string"}},
                        },
                    },
                },
                "required": ["path"],
            },
        }
    },
    "required": ["files"],
}


@dataclass
class CodegenSession:
    """Accumulates what the loop wrote + the final summary, for the CodeChange."""

    tracker: dict[Path, list[Path]]
    written: list[str] = field(default_factory=list)
    summary: str = ""
    submitted: bool = False


def build_codegen_tools(
    root: Path | str,
    *,
    grounded: bool,
    session: CodegenSession,
    runner: TestRunner | None = None,
) -> list[Tool]:
    """write_files / run_tests / submit_changes bound to ``root`` and ``session``."""
    from orchestrator.sdlc.codegen import CodegenError, apply_files

    root_path = Path(root).resolve()
    test_runner = runner or SubprocessTestRunner()

    async def _write_files(args: dict[str, object]) -> str:
        files = args.get("files")
        if not isinstance(files, list) or not files:
            return "error: 'files' must be a non-empty array"
        try:
            change = apply_files(files, root_path, written_tracker=session.tracker, grounded=grounded)
        except CodegenError as exc:
            # A guard rejection (bad anchor, shadow, no writable files) is an
            # observation — the model revises and tries again.
            return f"error: {exc}"
        for f in change.files:
            if f not in session.written:
                session.written.append(f)
        return f"wrote {len(change.files)} file(s): {', '.join(Path(f).name for f in change.files)}"

    async def _run_tests(_args: dict[str, object]) -> str:
        result = await test_runner.run(path=str(root_path))
        head = f"passed={result.passed} returncode={result.returncode}\n"
        return head + (result.output or "")

    async def _submit_changes(args: dict[str, object]) -> str:
        session.summary = str(args.get("summary") or "").strip()
        session.submitted = True
        if not session.written:
            return "error: nothing has been written yet — call write_files before submitting"
        return f"submitted {len(session.written)} file(s)"

    return [
        Tool(
            ToolSpec(
                "write_files",
                "Create new files (content) or edit existing ones (anchored find/replace). "
                "Returns what landed or a guard error to fix.",
                _FILES_SCHEMA,
            ),
            _write_files,
        ),
        Tool(
            ToolSpec(
                "run_tests",
                "Run the worktree's tests and return pass/fail with captured output.",
                {"type": "object", "properties": {}},
            ),
            _run_tests,
        ),
        Tool(
            ToolSpec(
                "submit_changes",
                "Finish: submit the changes written so far with a one-line summary. Ends the task.",
                {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
            ),
            _submit_changes,
            terminal=True,
        ),
    ]


__all__ = ["CodegenSession", "build_codegen_tools"]
