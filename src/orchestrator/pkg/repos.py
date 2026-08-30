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

#: Join kinds this release understands. A kind absent here is refused at load rather than
#: ignored, because a silently dropped join produces missing edges — which look like two
#: services that are not coupled, and read as health.
JOIN_KINDS = frozenset({"http", "data", "package"})

#: Relative to the directory the command runs in — the same `.spine/` a repo already carries.
DEFAULT_CONFIG = Path(".spine") / "repos.yaml"


class RepoConfigError(ValueError):
    """The declaration cannot be used. Always names the file and the offending key."""


@dataclass(frozen=True)
class Join:
    """One declared relationship between two repositories.

    **Declaring a join does not create an edge — it narrows the search.** "web talks to billing
    over HTTP under /v1" is a topology fact; matching ``POST /v1/orders/42`` against
    ``POST /v1/orders/{id}`` is still resolution, and still done from extracted facts. What the
    declaration removes is the part that cannot be resolved from evidence at all: *which*
    repository is even a candidate.
    """

    kind: str
    consumer: str
    provider: str
    base: str = ""

    def __str__(self) -> str:
        under = f" under {self.base}" if self.base else ""
        return f"{self.consumer} -{self.kind}-> {self.provider}{under}"


def joins_from_list(raw: Any, *, where: Path | str = "<inline>") -> tuple[Join, ...]:
    """Validate a ``joins:`` block. Order-independent: sorted, so two spellings agree."""
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise RepoConfigError(f"{where}: 'joins' must be a list")
    out: list[Join] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise RepoConfigError(f"{where}: joins[{i}] is not a mapping")
        kind = str(entry.get("kind", "")).strip()
        if kind not in JOIN_KINDS:
            raise RepoConfigError(
                f"{where}: joins[{i}] has kind {kind!r} — expected one of {sorted(JOIN_KINDS)}"
            )
        consumer, provider = str(entry.get("consumer", "")), str(entry.get("provider", ""))
        for role, value in (("consumer", consumer), ("provider", provider)):
            if not value:
                raise RepoConfigError(f"{where}: joins[{i}] has no {role}")
            try:
                validate_repo_key(value)
            except ScopeError as exc:
                raise RepoConfigError(f"{where}: joins[{i}] {role} — {exc}") from exc
        if consumer == provider:
            raise RepoConfigError(f"{where}: joins[{i}] joins {consumer!r} to itself")
        base = str(entry.get("base", "")).rstrip("/")
        if base and kind != "http":
            raise RepoConfigError(f"{where}: joins[{i}] — 'base' means a URL prefix and applies to http only")
        out.append(Join(kind, consumer, provider, base))
    return tuple(sorted(out, key=lambda j: (j.kind, j.consumer, j.provider, j.base)))


@dataclass(frozen=True)
class RepoSet:
    """Repository keys mapped to their checkout roots, in a stable order.

    Ordering is by key, not by declaration order, so two people whose YAML lists the same repos
    in a different order still produce the same merged graph. Determinism at this layer is what
    lets the merged graph be diffed at all.
    """

    roots: tuple[tuple[str, Path], ...]
    source: Path | None = None
    joins: tuple[Join, ...] = ()

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


def from_mapping(
    mapping: dict[str, Any],
    *,
    base: Path,
    source: Path | None = None,
    joins: Any = None,
) -> RepoSet:
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

    declared = joins_from_list(joins, where=where)
    known = {key for key, _ in roots}
    for join in declared:
        for role, key in (("consumer", join.consumer), ("provider", join.provider)):
            if key not in known:
                raise RepoConfigError(
                    f"{where}: join {join} names an undeclared {role} {key!r} "
                    f"— declared repos are {sorted(known)}"
                )
    return RepoSet(tuple(sorted(roots)), source, declared)


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
    return from_mapping(doc["repos"], base=base or config.parent, source=config, joins=doc.get("joins"))


def find_repo_config(start: Path | str = ".") -> Path | None:
    """``.spine/repos.yaml`` under ``start``, or None. Does not walk upward.

    Deliberately not a search: a config found in a parent directory would silently change what
    a command in a subdirectory means.
    """
    candidate = Path(start) / DEFAULT_CONFIG
    return candidate if candidate.is_file() else None


__all__ = [
    "DEFAULT_CONFIG",
    "JOIN_KINDS",
    "Join",
    "RepoConfigError",
    "RepoSet",
    "joins_from_list",
    "find_repo_config",
    "from_mapping",
    "load_repo_config",
]
