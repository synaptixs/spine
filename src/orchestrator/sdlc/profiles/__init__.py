"""Workflow profiles — the SDLC pipeline as data rather than as Python stage ordering.

Phase 1 ships one profile and loads it from this package. Where profiles ultimately live —
here, or `.spine/workflows/` in the target repo — is open question 1 in
``docs/specs/graphir-sdlc-workflow.md`` and belongs to Phase 3, which is when a repo first has
a reason to carry its own.

**Named ``profiles`` and not ``workflows``** because ``orchestrator.sdlc.workflows`` is already
the Temporal workflow module. A package of that name shadows it — imports resolve to the
package, and `SDLCWorkflow` silently stops existing. That is a runtime break the test suite did
not catch and ``mypy`` did.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from orchestrator.ir.graph import GraphIR

__all__ = ["ProfileNotFoundError", "load_profile", "profile_names", "profile_path"]

_DIR = Path(__file__).parent
DEFAULT_PROFILE = "default"


class ProfileNotFoundError(FileNotFoundError):
    """No profile of that name ships with this package."""


def profile_names() -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in _DIR.glob("*.yaml")))


def profile_path(name: str) -> Path:
    path = _DIR / f"{name}.yaml"
    if not path.is_file():
        known = ", ".join(profile_names()) or "none"
        raise ProfileNotFoundError(f"no workflow profile {name!r}; available: {known}")
    return path


@lru_cache(maxsize=8)
def load_profile(name: str = DEFAULT_PROFILE) -> GraphIR:
    """Parse and model-validate a profile.

    Cached because a profile is a file that does not change during a run, and `autorun` builds
    the graph on every run. Pydantic raises on a malformed profile, which is the right moment:
    a shipped profile that does not parse is a packaging bug, not a runtime condition.
    """
    return GraphIR.model_validate(yaml.safe_load(profile_path(name).read_text(encoding="utf-8")))
