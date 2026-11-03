"""Replace a file in one step — the write every change-file editor in ``intake`` shares.

Lives on its own so the editors can share it without importing one another: ``requirements``
edits ``proposal.md`` and ``options`` edits ``design.md``, and the gate in ``requirements`` will
read options, so neither may be what the other imports this from.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` in one step, in the same directory, never through a symlink: a link at
    ``path`` is replaced by the new file rather than written through to its target. The file keeps
    the mode it had; a new one gets the mode the umask gives any new file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        # mkstemp makes the file 0600; the change keeps the mode it had, or a shared checkout
        # would find its file unreadable after one edit.
        with contextlib.suppress(OSError):
            os.chmod(tmp, path.stat().st_mode & 0o7777 if path.exists() else 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current
