"""Write/test/submit in-loop tools (Phase 5b)."""

from __future__ import annotations

from pathlib import Path

from orchestrator.agentic.codegen_tools import CodegenSession, build_codegen_tools
from orchestrator.agentic.loop import Tool


def _tools(root: Path, *, grounded: bool = False) -> tuple[dict[str, Tool], CodegenSession]:
    session = CodegenSession(tracker={})
    by_name = {t.spec.name: t for t in build_codegen_tools(root, grounded=grounded, session=session)}
    return by_name, session


async def test_write_files_creates_a_file(tmp_path: Path) -> None:
    tools, session = _tools(tmp_path)
    out = await tools["write_files"].run({"files": [{"path": "feature.py", "content": "x = 1\n"}]})
    assert "wrote 1 file" in out
    assert (tmp_path / "feature.py").read_text(encoding="utf-8").strip() == "x = 1"
    assert session.written  # accumulated for the CodeChange


async def test_write_files_rejects_empty(tmp_path: Path) -> None:
    tools, _ = _tools(tmp_path)
    out = await tools["write_files"].run({"files": []})
    assert out.startswith("error:")


async def test_write_files_guard_error_is_an_observation(tmp_path: Path) -> None:
    # Brownfield create-only guard: a full-content write over a pre-existing,
    # untracked file is refused — surfaced as an error string, not raised.
    (tmp_path / "existing.py").write_text("old = 1\n", encoding="utf-8")
    tools, _ = _tools(tmp_path, grounded=True)
    out = await tools["write_files"].run({"files": [{"path": "existing.py", "content": "new = 2\n"}]})
    assert out.startswith("error:")
    assert (tmp_path / "existing.py").read_text(encoding="utf-8").strip() == "old = 1"  # untouched


async def test_submit_requires_prior_write(tmp_path: Path) -> None:
    tools, _ = _tools(tmp_path)
    out = await tools["submit_changes"].run({"summary": "done"})
    assert "nothing has been written" in out


async def test_submit_after_write_records_summary(tmp_path: Path) -> None:
    tools, session = _tools(tmp_path)
    await tools["write_files"].run({"files": [{"path": "f.py", "content": "y = 2\n"}]})
    out = await tools["submit_changes"].run({"summary": "added f"})
    assert "submitted 1 file" in out
    assert session.submitted is True and session.summary == "added f"


def test_submit_changes_is_terminal(tmp_path: Path) -> None:
    tools, _ = _tools(tmp_path)
    assert tools["submit_changes"].terminal is True
    assert tools["write_files"].terminal is False
