"""The shared server's repository allow-list: a call may name only what the admin listed.

On a laptop the plugin acts for one person on their own machine, and any path is theirs to
name. A server shared inside a company is different: whoever holds a token could otherwise ask
it to map, read or write any directory the server can see. So when ``ORCHESTRATOR_MCP_REPOS``
names an admin file — the same ``repos.yaml`` form as ``.spine/repos.yaml`` — every call is
checked against it before the tool runs (``server._scoped``).

A path reaches the disk through five kinds of argument, not one, and each tool declares which
of its arguments is which (``server._PATH_ARGS``; a tool with an undeclared path-like
parameter does not register):

- ``repo`` — a repository: a listed checkout or a directory inside one. Empty or ``.`` would be
  the server's own working directory and is refused, unless the call names the admin's list
  as ``repos`` instead. ``None`` is the tool's own default, which the admin configured.
- ``repo_optional`` — the same, except that empty means "no repository" (``requirements_check``).
- ``repos_file`` — a ``repos.yaml``: only the admin's own list. Any other could declare any
  directory as a repository.
- ``inside`` — a file or directory that must lie inside a listed checkout (an OpenSpec change,
  which ``requirements_answer`` writes into).
- ``repo_relative`` — a file named relative to the repository the same call names, which the
  tool itself confines there (``file_insights``); an absolute one must lie inside a listed
  checkout, like ``inside``.
- ``source`` — an intake source URI: ``file://`` inside a listed checkout only. Jira, Confluence
  and the rest would read through the *server's* credentials, which are not the caller's (D34).

Every path is compared after ``realpath``, so ``..`` and a symlink a caller names are judged by
where they land. A link *committed inside* a checkout is a different door: any tool that reads or
writes through it leaves the checkout, and three security-review rounds each found another tool
that did. So a checkout holding any link whose target is outside it — file or directory, dangling
or not — is not served at all (``escaping_link``, scanned on every call); the refusal names the
link for the admin to remove. The bank, design and intake readers also skip such links on their
own (``knowledge.access.inside_root``), which keeps a laptop's reads inside the repository too. A value padded
with whitespace is refused outright — the tool would otherwise open a different string from the
one checked. Git URLs are refused in v1 (D39): listed repositories are checkouts the admin
mounts and keeps current. A refusal names the argument and the listed keys, never a path on
the server.

The list is re-read when the file changes (keyed on its size and mtime), so an admin edit
takes effect without a restart; a list that stops parsing refuses every call rather than
falling open.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOS_ENV = "ORCHESTRATOR_MCP_REPOS"

KINDS = frozenset({"repo", "repo_optional", "repos_file", "inside", "source", "repo_relative"})

_FILE_SCHEME = "file://"


def _looks_like_url(value: str) -> bool:
    return "://" in value or value.startswith("git@")


def _unsafe_spelling(path: str) -> bool:
    """A path the check and the tool could read differently, or one into git's own files: ``~``
    is expanded here but not by every tool (which would open it under the working directory),
    and nothing a tool does belongs inside ``.git``."""
    return path.startswith("~") or ".git" in Path(path).parts


def _real(value: str) -> Path:
    return Path(os.path.realpath(os.path.expanduser(value)))


@dataclass(frozen=True)
class RepoAllowList:
    """The admin's list: repository keys and their real checkout roots."""

    config: Path
    roots: Mapping[str, Path]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.roots)

    @classmethod
    def load(cls, path: Path | str) -> RepoAllowList:
        """Read and check the list; anything wrong is a ``ValueError`` naming the file."""
        import yaml

        from orchestrator.pkg.repos import RepoConfigError, load_repo_config

        config = Path(path)
        try:
            doc = yaml.safe_load(config.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"{REPOS_ENV}: {config} cannot be read — {exc}") from exc
        except yaml.YAMLError as exc:
            raise ValueError(f"{REPOS_ENV}: {config} is not valid YAML — {exc}") from exc
        repos = doc.get("repos") if isinstance(doc, dict) else None
        if not isinstance(repos, dict) or not repos:
            raise ValueError(
                f"{REPOS_ENV}: {config} needs a non-empty 'repos:' mapping of key to checkout path"
            )
        for key, raw in repos.items():
            if isinstance(raw, str) and _looks_like_url(raw.strip()):
                raise ValueError(
                    f"{REPOS_ENV}: {config}: repo {key!r} is a URL; list the path of a checkout the "
                    "server can read (git URLs are not served in this version)"
                )
        try:
            repo_set = load_repo_config(config)
        except RepoConfigError as exc:
            raise ValueError(f"{REPOS_ENV}: {exc}") from exc
        roots = {key: Path(os.path.realpath(repo_set.path(key))) for key in repo_set.keys}
        return cls(config=Path(os.path.realpath(config)), roots=roots)

    def key_for(self, path: Path) -> str | None:
        """The listed repository ``path`` (already real) is, or is inside; else ``None``."""
        for key, root in self.roots.items():
            if path == root or path.is_relative_to(root):
                return key
        return None


def allow_list_from_env() -> RepoAllowList | None:
    """The list ``ORCHESTRATOR_MCP_REPOS`` names, or ``None`` when it is unset (a laptop)."""
    path = os.getenv(REPOS_ENV, "").strip()
    return RepoAllowList.load(path) if path else None


_cache: dict[tuple[str, int, int], RepoAllowList] = {}


def current_allow_list() -> RepoAllowList | None:
    """The list for this call: re-read when the file changes. Raises ``ValueError`` when the
    list is set but cannot be used — the caller refuses the call."""
    path = os.getenv(REPOS_ENV, "").strip()
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError as exc:
        raise ValueError(f"{REPOS_ENV}: {path} cannot be read — {exc}") from exc
    cache_key = (path, stat.st_size, stat.st_mtime_ns)
    found = _cache.get(cache_key)
    if found is None:
        found = RepoAllowList.load(path)
        _cache.clear()
        _cache[cache_key] = found
    return found


def _named(arguments: Mapping[str, Any], kinds: set[str], decl: Mapping[str, str]) -> bool:
    return any(decl[name] in kinds and arguments.get(name) for name in decl)


def _check_one(allow: RepoAllowList, kind: str, value: Any, *, has_repos_file: bool) -> bool:
    """Whether one argument value is allowed. ``KeyError`` for a kind nobody defined."""
    if kind not in KINDS:
        raise KeyError(kind)
    if value is None:
        return True  # not supplied: the tool's own default, which the admin configured
    if not isinstance(value, str) or value != value.strip():
        return False  # padded: the tool would read a different string from the one checked
    text = value
    if kind == "repos_file":
        return not _unsafe_spelling(text) and _real(text) == allow.config
    if kind == "source":
        if not text.startswith(_FILE_SCHEME):
            return False
        text = text[len(_FILE_SCHEME) :]
        return bool(text) and not _unsafe_spelling(text) and allow.key_for(_real(text)) is not None
    if kind == "repo_relative":
        if not text:
            return True  # the tool answers "provide a path"
        if _unsafe_spelling(text):
            return False
        return not os.path.isabs(text) or allow.key_for(_real(text)) is not None
    if kind in ("repo", "repo_optional"):
        if not text:
            return kind == "repo_optional" or has_repos_file
        if _looks_like_url(text):
            return False
        if text.rstrip("/") in (".", ""):
            return False
    elif not text:  # inside
        return False
    if _unsafe_spelling(text):
        return False
    return allow.key_for(_real(text)) is not None


def escaping_link(root: Path) -> Path | None:
    """The first link under ``root`` whose target is outside it — followed or dangling, file or
    directory — or ``None``. ``root`` is real. ``os.walk`` does not descend a directory link, and
    ``.git`` is skipped: git keeps its own links to itself."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted([*dirnames, *filenames]):
            path = Path(dirpath, name)
            if path.is_symlink() and not Path(os.path.realpath(path)).is_relative_to(root):
                return path
    return None


def _touched(
    allow: RepoAllowList, decl: Mapping[str, str], arguments: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """``(argument, repository key)`` for every listed repository the call reaches."""
    out: list[tuple[str, str]] = []
    for name, kind in decl.items():
        value = arguments.get(name)
        if not isinstance(value, str) or not value:
            continue
        if kind == "repos_file":
            out.extend((name, key) for key in allow.keys)
            continue
        text = value[len(_FILE_SCHEME) :] if kind == "source" else value
        if kind == "repo_relative" and not os.path.isabs(text):
            continue  # inside the repository another argument names, which is touched there
        key = allow.key_for(_real(text))
        if key is not None:
            out.append((name, key))
    return out


def _assess(
    allow: RepoAllowList, decl: Mapping[str, str], arguments: Mapping[str, Any]
) -> tuple[str, str] | None:
    """``(argument, refusal)`` for the first thing the list does not allow, or ``None``."""
    has_repos_file = _named(arguments, {"repos_file"}, decl)
    for name, kind in decl.items():
        if not _check_one(allow, kind, arguments.get(name), has_repos_file=has_repos_file):
            return name, (
                f"{name}: not a repository this server serves. It serves {', '.join(allow.keys)} "
                f"— name one of those checkouts (or a path inside one); the list is the admin's ({REPOS_ENV})"
            )
    for name, key in _touched(allow, decl, arguments):
        root = allow.roots[key]
        link = escaping_link(root)
        if link is not None:
            return name, (
                f"{name}: {key} is not served while its checkout holds a link that leaves it "
                f"({link.relative_to(root)}): a tool would read or write through it. The server's "
                "admin must remove the link"
            )
    return None


def refused_argument(
    allow: RepoAllowList, decl: Mapping[str, str], arguments: Mapping[str, Any]
) -> str | None:
    """The argument :func:`refusal` refuses, for the audit line."""
    found = _assess(allow, decl, arguments)
    return found[0] if found else None


def refusal(allow: RepoAllowList, decl: Mapping[str, str], arguments: Mapping[str, Any]) -> str | None:
    """``None`` when the call may proceed; else why not, naming the argument and the listed keys
    — never a path on the server. A listed checkout holding a link that leaves it is refused
    whole: a committed link would carry any tool's reads and writes past the list."""
    found = _assess(allow, decl, arguments)
    return found[1] if found else None


__all__ = [
    "KINDS",
    "REPOS_ENV",
    "RepoAllowList",
    "allow_list_from_env",
    "current_allow_list",
    "escaping_link",
    "refusal",
    "refused_argument",
]
