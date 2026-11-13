"""Make one planned change: back the file up, write it, record what was done.

**The record is what makes ``--undo`` safe (D7, story P4).** It lives in
``~/.config/spine/setup-record.json`` and lists every entry setup added or replaced, with
the backup it took first. Undo reverses exactly that list, so an entry the user wrote by
hand, and which setup left alone, is never touched.

**A backup is taken only when a file existed before the write,** named
``<file>.bak-spine-<UTC time>`` beside it, the same pattern earlier manual setups on the
user's machine already used.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.setup.entry import user_env_path
from orchestrator.setup.plan import Change


def record_path() -> Path:
    return user_env_path().parent / "setup-record.json"


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def backup(path: Path) -> Path | None:
    """Copy ``path`` aside before it is changed. ``None`` when there was nothing to keep."""
    if not path.is_file():
        return None
    copy = path.with_name(f"{path.name}.bak-spine-{_stamp()}")
    shutil.copy2(path, copy)
    return copy


def apply_change(change: Change) -> None:
    """Write ``change`` if it writes anything, then record it."""
    if not change.writes:
        return
    path = change.client.config_path()
    saved = backup(path)
    change.client.write(change.wanted)
    _record(
        {
            "client": change.client.name,
            "action": change.action,
            "path": str(path),
            "backup": str(saved) if saved else None,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )


def _record(row: dict[str, object]) -> None:
    path = record_path()
    rows: list[object] = []
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            rows = loaded if isinstance(loaded, list) else []
        except ValueError:
            rows = []
    rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
