"""What every client adapter provides, and how a client's program is found."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from orchestrator.setup.entry import ServerEntry


class Client(Protocol):
    """A client Spine can be registered in.

    An adapter never prints what it reads: a client's config holds other servers' secrets.
    """

    name: str

    def program(self) -> Path | None:
        """The client's program, or ``None`` when the client is not installed."""
        ...

    def config_path(self) -> Path:
        """The file the user-scope entry lives in."""
        ...

    def current(self) -> ServerEntry | None:
        """The ``spine`` entry the client has now, or ``None``."""
        ...

    def matches(self, wanted: ServerEntry) -> bool:
        """Whether the current entry already says everything ``wanted`` says."""
        ...

    def write(self, wanted: ServerEntry) -> None:
        """Make the client's ``spine`` entry ``wanted``, replacing any other one."""
        ...


def find_program(name: str, fallbacks: Iterable[Path] = ()) -> Path | None:
    """``name`` on ``PATH``, else the first fallback that exists and is executable.

    Fallbacks matter because clients are often installed only inside an app bundle
    (VS Code's ``code``; the Codex CLI inside ChatGPT.app), measured 2026-11-06.
    """
    found = shutil.which(name)
    if found:
        return Path(found)
    for candidate in fallbacks:
        if candidate.is_file() and shutil.which(str(candidate)):
            return candidate
    return None
