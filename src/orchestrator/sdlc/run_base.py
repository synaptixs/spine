"""Where runs, approvals and run artifacts live, by default (SSPN-407, G4 D125).

Outside the repo, on purpose: state is not source, and markdown in the working tree becomes a ``Doc``
node in the graph the next stage reads. It used to be ``$TMPDIR/spine-runs``, which macOS clears on a
restart, and a restart then took the run record, its park and every test log with it. The default is
now the user's state directory, ``$XDG_STATE_HOME/spine/runs`` or ``~/.local/state/spine/runs``.

``SPINE_RUN_STATE`` (records and approvals) and ``SPINE_RUN_ARTIFACTS`` (artifacts) still win. With
neither set, a run left under the old temp base is still read (:func:`legacy_run_base`), never moved:
new writes go to the new base.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["artifacts_base", "default_run_base", "legacy_run_base", "state_base"]


def default_run_base() -> Path:
    """``$XDG_STATE_HOME/spine/runs``, else ``~/.local/state/spine/runs``.

    A relative ``XDG_STATE_HOME`` is ignored, as the XDG spec says: it would put the store under whatever
    directory the server happened to start in.
    """
    xdg = os.getenv("XDG_STATE_HOME", "")
    root = Path(xdg) if os.path.isabs(xdg) else Path(os.path.expanduser("~")) / ".local" / "state"
    return root / "spine" / "runs"


def state_base() -> Path:
    """The base of the run records (``_state``) and approvals (``_approvals``)."""
    override = os.getenv("SPINE_RUN_STATE")
    return Path(override) if override else default_run_base()


def artifacts_base() -> Path:
    """The base of each run's artifacts directory (``<base>/<run_id>``)."""
    override = os.getenv("SPINE_RUN_ARTIFACTS")
    return Path(override) if override else default_run_base()


def legacy_run_base() -> Path | None:
    """The temp base Spine used before 3.71, to read runs left there; ``None`` when a store is named
    explicitly (``SPINE_RUN_STATE``) or nothing is there."""
    if os.getenv("SPINE_RUN_STATE"):
        return None
    old = Path(tempfile.gettempdir()) / "spine-runs"
    return old if old.is_dir() else None
