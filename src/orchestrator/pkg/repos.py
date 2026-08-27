"""Which repositories are part of this system — declared, never guessed.

A multi-repo graph needs a **key** per repository, and that key is baked into every scoped node
id (see :mod:`orchestrator.pkg.scoping`) and every cache entry. So it has to be stable across a
developer's laptop and CI. Anything derived is not: a directory name differs per checkout, a
remote URL differs between SSH and HTTPS forms, and a key that drifts silently invalidates
caches and makes two runs incomparable.

**So it is declared.** ``.spine/repos.yaml`` sits beside ``.spine/workflows/``, which already
establishes repo-carried configuration::

    repos:
      billing: ../billing-service
      web:     ../storefront
      shared:  ../shared-lib

**Discovery from manifests is deliberately not done.** Reading ``go.mod`` or ``package.json`` to
find "our" repositories means guessing which of forty dependencies belong to the system, which
is a judgement the team owns and a second resolution problem this package does not need.

**The failure mode is loud, which is why a hand-maintained file is acceptable here.** A repo
someone forgot to add produces no nodes, no landing sites and a visibly narrower graph — you
notice on the first run. That is the opposite of a configuration whose absence reads as health.

**Local paths only, for now.** Cloning is ``WorkspaceManager``'s job; pulling it in here would
drag in auth, shallow-clone policy and workspace layout for a feature whose point is that
merging works. Remote support is a later decision, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.pkg.scoping import ScopeError, validate_repo_key

#: Relative to the directory the command runs in — the same `.spine/` a repo already carries.
DEFAULT_CONFIG = Path(".spine") / "repos.yaml"


class RepoConfigError(ValueError):
    """The declaration cannot be used. Always names the file and the offending key."""


@dataclass(frozen=True)
class RepoSet:
    """Repository keys mapped to their checkout roots, in a stable order.

    Ordering is by key, not by declaration order, so two people whose YAML lists the same repos
    in a different order still produce the same merged graph. Determinism at this layer is what
    lets the merged graph be diffed at all.
    """

    roots: tuple[tuple[str, Path], ...]
    source: Path | None = None

    def __post_init__(self) -> None:
        if not self.roots:
            raise RepoConfigError(f"{self.source or '<inline>'}: no repositories declared")

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.roots)

    def path(self, key: str) -> Path:
        for candidate, root in self.roots:
            if candidate == key:
                return root
        raise KeyError(key)

    def __len__(self) -> int:
        return len(self.roots)

    def __iter__(self) -> Any:
        return iter(self.roots)


def from_mapping(mapping: dict[str, Any], *, base: Path, source: Path | None = None) -> RepoSet:
    """Build a :class:`RepoSet` from ``{key: path}``. Relative paths resolve against ``base``.

    Every key is validated here rather than at merge time. A bad key caught at load names the
    file it came from; the same key caught later surfaces as a malformed node id with nothing
    pointing back at its origin.
    """
    where = source or Path("<inline>")
    if not isinstance(mapping, dict):
        raise RepoConfigError(f"{where}: 'repos' must be a mapping of key to path")

    roots: list[tuple[str, Path]] = []
    seen_paths: dict[Path, str] = {}
    for key, raw in mapping.items():
        if not isinstance(key, str):
            raise RepoConfigError(f"{where}: repo key {key!r} is not a string")
        try:
            validate_repo_key(key)
        except ScopeError as exc:
            raise RepoConfigError(f"{where}: {exc}") from exc
        if not isinstance(raw, str) or not raw.strip():
            raise RepoConfigError(f"{where}: repo {key!r} has no path")

        root = Path(raw).expanduser()
        root = root if root.is_absolute() else (base / root)
        root = root.resolve()
        if not root.is_dir():
            raise RepoConfigError(f"{where}: repo {key!r} points at {root}, which is not a directory")
        # Two keys for one checkout would scope the same facts twice under different ids —
        # every symbol duplicated, every count doubled, and nothing to signal it.
        if root in seen_paths:
            raise RepoConfigError(f"{where}: repos {seen_paths[root]!r} and {key!r} both point at {root}")
        seen_paths[root] = key
        roots.append((key, root))

    return RepoSet(tuple(sorted(roots)), source)


def load_repo_config(path: Path | str, *, base: Path | None = None) -> RepoSet:
    """Read a ``repos.yaml``. ``base`` defaults to the file's own directory."""
    import yaml

    config = Path(path)
    try:
        text = config.read_text(encoding="utf-8")
    except OSError as exc:
        raise RepoConfigError(f"{config}: cannot be read — {exc}") from exc
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RepoConfigError(f"{config}: invalid YAML — {exc}") from exc
    if not isinstance(doc, dict) or "repos" not in doc:
        raise RepoConfigError(f"{config}: expected a top-level 'repos:' mapping")
    return from_mapping(doc["repos"], base=base or config.parent, source=config)


def find_repo_config(start: Path | str = ".") -> Path | None:
    """``.spine/repos.yaml`` under ``start``, or None. Does not walk upward.

    Deliberately not a search: a config found in a parent directory would silently change what
    a command in a subdirectory means.
    """
    candidate = Path(start) / DEFAULT_CONFIG
    return candidate if candidate.is_file() else None


__all__ = [
    "DEFAULT_CONFIG",
    "RepoConfigError",
    "RepoSet",
    "find_repo_config",
    "from_mapping",
    "load_repo_config",
]
