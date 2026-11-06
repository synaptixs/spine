"""What setup would change in each client, worked out before anything is written.

``--dry-run`` prints this and stops. A real run prints it, asks (D3), then hands each
change to :mod:`.apply`. Working the plan out first is what lets the two agree: what a user
approves is exactly what is written.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from orchestrator.setup.clients import Client, all_clients
from orchestrator.setup.entry import ServerEntry

Action = Literal["add", "same", "replace", "missing"]


@dataclass(frozen=True)
class Change:
    """One client's part of a run."""

    client: Client
    action: Action
    wanted: ServerEntry
    current: ServerEntry | None = None

    @property
    def writes(self) -> bool:
        return self.action in ("add", "replace")

    def describe(self) -> str:
        """One line for the terminal. It never shows an env value (see the package docstring)."""
        name = f"{self.client.name:<8}"
        if self.action == "missing":
            return f"{name}not found, skipped"
        where = self.client.config_path()
        if self.action == "same":
            return f"{name}already set        {where}"
        if self.action == "add":
            return f"{name}add                {where}"
        was = self.current.describe() if self.current else "unreadable"
        return f"{name}replace            {where}\n          was: {was}"


def plan_setup(wanted: ServerEntry, *, only: Sequence[str] | None = None) -> list[Change]:
    """Each known client's change, in a fixed order. ``only`` limits it to the named clients."""
    clients = all_clients()
    if only:
        unknown = sorted(set(only) - {c.name for c in clients})
        if unknown:
            raise ValueError(
                f"unknown client(s): {', '.join(unknown)}; known: {', '.join(c.name for c in clients)}"
            )
        clients = [c for c in clients if c.name in only]
    changes: list[Change] = []
    for client in clients:
        if client.program() is None:
            changes.append(Change(client, "missing", wanted))
            continue
        current = client.current()
        if current is None:
            changes.append(Change(client, "add", wanted))
        elif client.matches(wanted):
            changes.append(Change(client, "same", wanted, current))
        else:
            changes.append(Change(client, "replace", wanted, current))
    return changes
