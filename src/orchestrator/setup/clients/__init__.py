"""One adapter per client (D10). Adding a client is one more module and one more row here."""

from __future__ import annotations

from .base import Client, find_program
from .claude import ClaudeCode
from .codex import Codex


def all_clients() -> list[Client]:
    """Every client setup knows, in the order it reports them."""
    return [ClaudeCode(), Codex()]


__all__ = ["Client", "ClaudeCode", "Codex", "all_clients", "find_program"]
