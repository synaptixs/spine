"""Developer TUI for the orchestrator (unified UI — P4).

A keyboard-driven terminal cousin of the web inbox: watch runs, clear approval
gates, and delegate a run, all over the same ``/v1`` API the web UI uses. The
Textual dependency is optional (the ``tui`` extra); the API client
(``tui.client``) is dependency-light and imported eagerly, while the Textual app
(``tui.app``) is imported only when the TUI is launched.
"""

from __future__ import annotations

from orchestrator.tui.client import RegistryClient

__all__ = ["RegistryClient"]
