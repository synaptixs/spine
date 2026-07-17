"""Repo sources for capability jobs — a local path OR a remote git URL.

``understand`` / ``state`` / ``pkg`` analyse a repo on the server's filesystem, so
a web request must not be able to reach arbitrary content. Two source kinds,
both validated here:

* **Local path** — resolved *relative to* a configured workspace root
  (``ORCHESTRATOR_WORKSPACE_ROOT``); traversal / paths outside the root are
  rejected (unless ``repo_allow_any_local`` opts into any absolute path for a
  trusted single-user deployment). Unset root → the server's working directory.
* **Git URL** (``resolve_repo_source``) — GitHub / Bitbucket / GitLab / enterprise,
  restricted to an allow-list of hosts (``ORCHESTRATOR_REPO_ALLOWED_HOSTS``, ``*``
  for any) with ``file://`` / ``http://`` / localhost / private-IP always blocked
  (SSRF guard). Cloned shallowly on demand into a temp dir that's removed after;
  GitHub is authenticated via ``gitauth``, other hosts use ambient git creds. The
  token never reaches logs, the audit record, or error messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from orchestrator.registry.api.config import Settings


class RepoPathError(ValueError):
    """The requested repo path is missing, not a directory, or escapes the root."""


class RepoSourceError(ValueError):
    """The requested repo source (URL) is disallowed, malformed, or failed to clone."""


def workspace_root(settings: Settings) -> Path:
    """The absolute, symlink-resolved root repos must live under."""
    raw = getattr(settings, "workspace_root", None)
    root = Path(raw) if raw else Path.cwd()
    return root.resolve()


def resolve_repo_path(spec: str | None, settings: Settings) -> Path:
    """Resolve a request's repo ``spec`` to an absolute path inside the workspace.

    ``spec`` is a path relative to the workspace root (``"."`` = the root itself);
    an absolute path is accepted only if it resolves inside the root. Raises
    ``RepoPathError`` on traversal, a path outside the root, or a non-directory.
    """
    root = workspace_root(settings)
    candidate_spec = (spec or ".").strip() or "."
    candidate = Path(candidate_spec)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()

    if resolved != root and root not in resolved.parents:
        raise RepoPathError(f"repo path {candidate_spec!r} resolves outside the workspace root")
    if not resolved.exists():
        raise RepoPathError(f"repo path {candidate_spec!r} does not exist")
    if not resolved.is_dir():
        raise RepoPathError(f"repo path {candidate_spec!r} is not a directory")
    return resolved


# --------------------------------------------------------------------------- #
# Repo sources: a local path OR a remote git URL (cloned on demand)
# --------------------------------------------------------------------------- #
_CLONE_TIMEOUT_S = 180
_ALLOWED_SCHEMES = ("https", "ssh", "git")  # not file:// (local read) or http:// (plaintext)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


@dataclass(frozen=True)
class RepoSource:
    """Where to get a repo to analyse. ``kind`` is ``local`` (an on-disk path,
    already resolved) or ``git`` (a validated URL cloned on demand)."""

    kind: str  # "local" | "git"
    display: str  # the original spec (safe to log — never carries a token)
    path: Path | None = None  # set when kind == "local"
    url: str | None = None  # set when kind == "git"


def _is_git_url(spec: str) -> bool:
    s = spec.strip()
    return s.startswith("git@") or bool(_SCHEME_RE.match(s))


def _allowed_hosts(settings: Settings) -> set[str]:
    raw = getattr(settings, "repo_allowed_hosts", "") or ""
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _ip_is_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


def _is_internal_host(host: str) -> bool:
    """Block localhost, cloud-metadata, and private/loopback/link-local IPs —
    the SSRF backstop that applies even when the host allow-list is ``*``."""
    h = host.lower().strip("[]")
    if h in ("localhost", "metadata", "metadata.google.internal") or h.endswith((".local", ".internal")):
        return True
    # Standard IP literal (dotted-quad IPv4 / IPv6, incl. IPv4-mapped).
    with contextlib.suppress(ValueError):
        return _ip_is_internal(ipaddress.ip_address(h))
    # Obfuscated IPv4 the standard parser rejects but libc (hence git/curl) accepts:
    # integer (2130706433), hex (0x7f000001), octal (0177.0.0.1), short (127.1). Without
    # this, an allow-list=* operator could reach loopback/metadata through them. inet_aton
    # normalises exactly these forms and rejects real hostnames (github.com → OSError).
    with contextlib.suppress(OSError):
        return _ip_is_internal(ipaddress.ip_address(socket.inet_aton(h)))
    return False


def _host_allowed(host: str, allowed: set[str]) -> bool:
    if "*" in allowed:
        return True
    # exact match or a subdomain of an allow-listed host (e.g. git.acme.com ⊂ acme.com)
    return host in allowed or any(host.endswith("." + a) for a in allowed)


def _validate_git_url(spec: str, settings: Settings) -> RepoSource:
    s = spec.strip()
    if s.startswith("git@"):  # scp-like ssh: git@host:org/repo
        scheme = "ssh"
        host = s[len("git@") :].split(":", 1)[0]
    else:
        parts = urlsplit(s)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname or ""
    host = host.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise RepoSourceError(f"unsupported URL scheme {scheme!r} — use https, ssh, or git (not file/http)")
    if not host:
        raise RepoSourceError("could not parse a host from the repo URL")
    if _is_internal_host(host):
        raise RepoSourceError(f"host {host!r} is internal/loopback and not allowed")
    if not _host_allowed(host, _allowed_hosts(settings)):
        raise RepoSourceError(
            f"host {host!r} is not in the allow-list — add it to ORCHESTRATOR_REPO_ALLOWED_HOSTS"
        )
    return RepoSource(kind="git", display=s, url=s)


def _resolve_local(spec: str, settings: Settings) -> RepoSource:
    if getattr(settings, "repo_allow_any_local", False):
        resolved = Path(spec or ".").expanduser().resolve()
        if not resolved.exists():
            raise RepoPathError(f"local path {spec!r} does not exist")
        if not resolved.is_dir():
            raise RepoPathError(f"local path {spec!r} is not a directory")
        return RepoSource(kind="local", display=spec or ".", path=resolved)
    return RepoSource(kind="local", display=spec or ".", path=resolve_repo_path(spec, settings))


def resolve_repo_source(spec: str | None, settings: Settings) -> RepoSource:
    """Classify + validate a repo spec into a ``RepoSource``.

    A git URL (``https://``/``ssh://``/``git://``/``git@host:…``) is validated
    against the host allow-list (+ SSRF guards) and cloned on demand; anything
    else is treated as a local path (under the workspace root unless
    ``repo_allow_any_local``). Cheap + synchronous — no clone happens here."""
    spec = (spec or ".").strip() or "."
    if _is_git_url(spec):
        return _validate_git_url(spec, settings)
    return _resolve_local(spec, settings)


def _sanitize(message: str, *secrets: str) -> str:
    out = message
    for s in secrets:
        if s:
            out = out.replace(s, "<redacted>")
    # also scrub any token embedded as userinfo (x-access-token:TOKEN@host)
    return re.sub(r"//[^/@\s]+:[^/@\s]+@", "//<redacted>@", out)


def _git_clone(url: str, dest: Path) -> None:
    """Shallow-clone ``url`` into ``dest``. GitHub URLs get a token injected via
    gitauth; other hosts use ambient git credentials (SSH agent / credential
    helper / public). The token never reaches logs or error messages."""
    from orchestrator.sdlc.gitauth import authenticate_repo_url

    clone_url = url
    with contextlib.suppress(Exception):  # auth is best-effort; fall back to ambient creds
        resolved = asyncio.run(authenticate_repo_url(url))
        if resolved:
            clone_url = resolved
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # never block on an interactive prompt
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", clone_url, str(dest)],
            env=env,
            capture_output=True,
            text=True,
            timeout=_CLONE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoSourceError(f"clone of {url!r} timed out after {_CLONE_TIMEOUT_S}s") from exc
    if proc.returncode != 0:
        detail = _sanitize((proc.stderr or proc.stdout or "").strip(), clone_url)[:300]
        raise RepoSourceError(f"clone failed: {detail}")


@contextlib.contextmanager
def materialize_repo_source(
    source: RepoSource, *, log: Callable[[str], None] | None = None
) -> Iterator[Path]:
    """Yield an on-disk path for ``source``. Local sources yield their path
    directly; git sources are shallow-cloned into a temp dir that is removed on
    exit. Runs synchronously (call it from a worker thread for URL sources)."""
    if source.kind == "local":
        assert source.path is not None
        yield source.path
        return
    assert source.url is not None
    tmp = Path(tempfile.mkdtemp(prefix="spine-repo-"))
    try:
        if log:
            log(f"cloning {source.display} …")
        dest = tmp / "repo"
        _git_clone(source.url, dest)
        yield dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


__all__ = [
    "RepoPathError",
    "RepoSource",
    "RepoSourceError",
    "materialize_repo_source",
    "resolve_repo_path",
    "resolve_repo_source",
    "workspace_root",
]
