"""Workflow profiles — the SDLC pipeline as data rather than as Python stage ordering.

Three ship here: `default`, `bug` and `enhancement`. `profile_select` maps a ticket's issue type
onto one, deterministically.

**A repo may carry its own.** A profile in ``.spine/workflows/<name>.yaml`` inside the target
repository shadows a shipped one of the same name, which settles open question 1 in
``docs/specs/graphir-sdlc-workflow.md``. `.spine/` is already Spine's directory in a target repo,
and same-name-wins is the least surprising rule: a team that wants a different `bug` profile
writes `bug.yaml` and does not have to learn a precedence order.

A repo profile is validated exactly like a shipped one — it is a graph the SDLC will execute, and
"it came from the repo" is not a reason to trust it less carefully or more.

**Named ``profiles`` and not ``workflows``** because ``orchestrator.sdlc.workflows`` is already
the Temporal workflow module. A package of that name shadows it — imports resolve to the
package, and `SDLCWorkflow` silently stops existing. That is a runtime break the test suite did
not catch and ``mypy`` did.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator.ir.graph import GraphIR

__all__ = [
    "REPO_PROFILE_DIR",
    "ProfileNotFoundError",
    "load_profile",
    "profile_names",
    "profile_path",
    "repo_profile_names",
]

_DIR = Path(__file__).parent
DEFAULT_PROFILE = "default"

# Where a target repo keeps its own. Relative to the repo root, and inside `.spine/` because that
# is already where Spine's per-repo configuration lives.
REPO_PROFILE_DIR = Path(".spine") / "workflows"


class ProfileNotFoundError(FileNotFoundError):
    """No profile of that name ships with this package, or exists in the repo."""


def profile_names(root: Path | str | None = None) -> tuple[str, ...]:
    """Every profile that can be loaded — shipped, plus any the repo carries."""
    names = {p.stem for p in _DIR.glob("*.yaml")}
    names.update(repo_profile_names(root))
    return tuple(sorted(names))


def repo_profile_names(root: Path | str | None = None) -> tuple[str, ...]:
    """Profiles the target repo carries, if any."""
    if root is None:
        return ()
    directory = Path(root) / REPO_PROFILE_DIR
    if not directory.is_dir():
        return ()
    return tuple(sorted(p.stem for p in directory.glob("*.yaml")))


def profile_path(name: str, root: Path | str | None = None) -> Path:
    """Where a profile lives. **The repo's copy wins** over the shipped one of the same name."""
    if root is not None:
        carried = Path(root) / REPO_PROFILE_DIR / f"{name}.yaml"
        if carried.is_file():
            return carried
    path = _DIR / f"{name}.yaml"
    if not path.is_file():
        known = ", ".join(profile_names(root)) or "none"
        raise ProfileNotFoundError(f"no workflow profile {name!r}; available: {known}")
    return path


def load_profile(name: str = DEFAULT_PROFILE, root: Path | str | None = None) -> GraphIR:
    """Parse and model-validate a profile.

    Not cached. It was, until profiles could come from a target repo — at which point the cache
    key would have had to include the root, and a stale entry would silently run the shipped
    profile for a repo that carries its own. Parsing a small YAML per run is cheaper than that
    class of bug; the graph itself is still built once.

    Pydantic raises on a malformed profile, which is the right moment: a shipped profile that
    does not parse is a packaging bug, and a repo-carried one that does not parse is a message
    to whoever wrote it.
    """
    return GraphIR.model_validate(yaml.safe_load(profile_path(name, root).read_text(encoding="utf-8")))
