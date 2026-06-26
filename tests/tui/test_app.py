"""Textual TUI app smoke (P4) — skips cleanly without the `tui` extra."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("textual", reason="install the 'tui' extra")

from textual.widgets import DataTable  # noqa: E402

from orchestrator.tui.app import OrchestratorTUI  # noqa: E402


class _StubClient:
    def __init__(self) -> None:
        self.decided: tuple[str, str] | None = None
        self.started: str | None = None
        self.approvals_data = [{"id": "g1", "title": "Approve intents", "risk_classification": "medium"}]

    async def runs(self) -> list[dict[str, Any]]:
        return [
            {
                "sdlc_id": "R1",
                "state": "running",
                "last_action": "implement",
                "updated_at": "2026-06-24T10:00:00",
            }
        ]

    async def approvals(self) -> list[dict[str, Any]]:
        return list(self.approvals_data)

    async def decide(self, approval_id: str, action: str) -> None:
        self.decided = (approval_id, action)
        self.approvals_data = []  # gate cleared

    async def start_run(self, source: str, *, create_jira: bool = False) -> dict[str, Any]:
        self.started = source
        return {"sdlc_id": "new", "gates": {}}

    async def aclose(self) -> None:
        pass


async def test_tui_loads_runs_and_approvals() -> None:
    stub = _StubClient()
    app = OrchestratorTUI(stub)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#runs", DataTable).row_count == 1
        assert app.query_one("#approvals", DataTable).row_count == 1


async def test_tui_approve_action_decides_the_selected_gate() -> None:
    stub = _StubClient()
    app = OrchestratorTUI(stub)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#approvals", DataTable).move_cursor(row=0)
        await app.action_approve()
        await pilot.pause()
        assert stub.decided == ("g1", "approve")
        assert app.query_one("#approvals", DataTable).row_count == 0  # refreshed → gate gone
