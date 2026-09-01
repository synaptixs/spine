"""Spine — the package is ``synaptixs-spine``; the import and CLI stay ``orchestrator``."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

try:
    #: Read from the installed distribution rather than hardcoded. It was `"0.0.0"` until
    #: 2026-09-01 — a literal that no release step touched, so `orchestrator.__version__`
    #: reported 0.0.0 for every version ever shipped. A version string nothing derives is a
    #: version string nobody can trust, which is the same failure as a count nobody re-runs.
    __version__ = _installed_version("synaptixs-spine")
except PackageNotFoundError:  # pragma: no cover - only when running from a source tree uninstalled
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
