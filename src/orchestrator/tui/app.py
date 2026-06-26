"""The Textual TUI app (unified UI — P4).

A keyboard-driven terminal view over the same ``/v1`` API as the web inbox:
- a **runs** table (refreshed on an interval — the live feel),
- an **approvals** table you clear with ``a`` (approve) / ``x`` (reject),
- an **input** to delegate a run (type a source, Enter).

Imports Textual (the ``tui`` extra); the app takes a client so tests can inject a
fake without a live server.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Label

from orchestrator.tui.client import RegistryClient

REFRESH_SECONDS = 3.0


class OrchestratorTUI(App[None]):
    """Watch runs, clear gates, delegate — in the terminal."""

    TITLE = "Orchestrator"
    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("a", "approve", "Approve gate"),
        ("x", "reject", "Reject gate"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, client: Any) -> None:
        super().__init__()
        self._client = client
        self._approvals: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Input(
                placeholder="Delegate a run — source URI, e.g. confluence://<id> or file://./spec.md",
                id="src",
            ),
            Label("Runs", classes="section"),
            DataTable(id="runs"),
            Label("Approvals  (a approve · x reject)", classes="section"),
            DataTable(id="approvals"),
        )
        yield Footer()

    async def on_mount(self) -> None:
        runs = self.query_one("#runs", DataTable)
        runs.add_columns("run", "state", "last action", "updated")
        runs.cursor_type = "row"
        approvals = self.query_one("#approvals", DataTable)
        approvals.add_columns("id", "title", "risk")
        approvals.cursor_type = "row"
        await self.refresh_data()
        self.set_interval(REFRESH_SECONDS, self.refresh_data)

    async def refresh_data(self) -> None:
        try:
            runs = await self._client.runs()
            self._approvals = await self._client.approvals()
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the TUI
            self.notify(f"refresh failed: {exc}", severity="error")
            return
        runs_table = self.query_one("#runs", DataTable)
        runs_table.clear()
        for r in runs:
            runs_table.add_row(
                r.get("sdlc_id", ""),
                r.get("state", ""),
                r.get("last_action", ""),
                str(r.get("updated_at", ""))[:19],
            )
        appr_table = self.query_one("#approvals", DataTable)
        appr_table.clear()
        for a in self._approvals:
            appr_table.add_row(a.get("id", ""), a.get("title", ""), a.get("risk_classification", ""))

    async def action_refresh(self) -> None:
        await self.refresh_data()

    async def action_approve(self) -> None:
        await self._decide("approve")

    async def action_reject(self) -> None:
        await self._decide("reject")

    async def _decide(self, action: str) -> None:
        table = self.query_one("#approvals", DataTable)
        row = table.cursor_row
        if row is None or row >= len(self._approvals):
            return
        approval_id = self._approvals[row].get("id")
        if not approval_id:
            return
        try:
            await self._client.decide(approval_id, action)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"{action} failed: {exc}", severity="error")
            return
        self.notify(f"{action} → {approval_id}")
        await self.refresh_data()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        source = event.value.strip()
        if not source:
            return
        try:
            result = await self._client.start_run(source)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"delegate failed: {exc}", severity="error")
            return
        self.query_one("#src", Input).value = ""
        self.notify(f"started {result.get('sdlc_id', '')}")
        await self.refresh_data()


def run_tui(base_url: str, api_key: str) -> None:
    """Launch the TUI against ``base_url`` with ``api_key``."""
    OrchestratorTUI(RegistryClient(base_url, api_key)).run()


__all__ = ["OrchestratorTUI", "run_tui"]
