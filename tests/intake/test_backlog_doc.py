"""BACKLOG.md rendering: status markers, progress summary, annotations."""

from __future__ import annotations

from pathlib import Path

from orchestrator.intake.backlog_doc import backlog_path, render_markdown, write_backlog
from orchestrator.intake.intents import Intent
from orchestrator.intake.service import BacklogPlan

_PLAN = BacklogPlan(
    intents=[
        Intent(id="intent-a", title="Alpha"),
        Intent(id="intent-b", title="Beta"),
        Intent(id="intent-c", title="Gamma"),
    ]
)
_PROGRESS = {
    "intent-a": {"status": "done", "issue_key": "PROJ-1", "pr_url": "http://pr/1"},
    "intent-b": {"status": "in_progress", "issue_key": "PROJ-2"},
    # intent-c has no entry → todo
}


def test_markers_and_summary() -> None:
    md = render_markdown("confluence://x", _PLAN, _PROGRESS)
    assert "**Progress:** 1 / 3 done · 1 in progress" in md
    assert "- [x] `intent-a` — Alpha  (PROJ-1 · http://pr/1)" in md
    assert "- [~] `intent-b` — Beta  (PROJ-2)" in md
    assert "- [ ] `intent-c` — Gamma" in md


def test_empty_progress_is_all_todo() -> None:
    md = render_markdown("confluence://x", _PLAN, {})
    assert "**Progress:** 0 / 3 done · 0 in progress" in md
    assert md.count("- [ ]") == 3


def test_write_backlog_creates_file(tmp_path: Path) -> None:
    out = write_backlog(tmp_path / "BACKLOG.md", "confluence://x", _PLAN, _PROGRESS)
    assert out.read_text().startswith("# Backlog — confluence://x")


def test_backlog_path_env_override(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ORCHESTRATOR_BACKLOG_PATH", str(tmp_path / "custom.md"))
    assert backlog_path() == tmp_path / "custom.md"
