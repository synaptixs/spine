"""What the pkg walkers skip, the directory questions they ask, and what git ignores — a leaf module.

These lived in ``extractor.py`` and ``clang_includes.py`` imported them from there, which made a cycle
(``clang_includes`` -> ``extractor`` -> ... -> ``clang_includes``) that CodeQL's ``py/cyclic-import`` flags
even though every import was function-local, and a Code-scanning comment at Note severity blocks a release
promotion until its thread is resolved (alert 319, a re-fingerprint of 244 after B63 edited the line). The
names need nothing from the language front-ends, so they sit here, importing nothing from the package;
``extractor`` re-exports them and every other importer is unchanged.
"""

from __future__ import annotations

import os
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        ".tox",
        "obj",  # .NET build output (generated *.g.cs, *.AssemblyInfo.cs)
        "bin",  # .NET build output
        "target",  # Cargo build output, including generated Rust sources
        # Composer's `node_modules` (D5, php-support-roadmap.md): a Laravel app carries
        # ~10k vendored .php files, which would otherwise present a dependency as part
        # of the repo. Checked against the five pinned comprehension repos before
        # adding — none has a `vendor/` tree, so this costs no anchored facts there.
        "vendor",
        # CocoaPods' checkout (`ios/Pods`): a React Native app carries boost, glog, hermes and
        # every React header under it — on CB-686 the design proposed `FBReactNativeSpec.h` and
        # `glog/logging.cc`, "125 untested symbols" were all boost templates, and a stray `.py`
        # under it made `--language auto` scaffold Python into a TypeScript app. Checked against
        # the five pinned comprehension repos before adding, as `vendor` was: none has a `Pods/`.
        "Pods",
        # Spine's OWN generated knowledge base (``knowledge/understand.py``'s
        # BANK_DIRNAME and its legacy name). It is output, not source: ingesting it
        # would count our own prose as the repo's documentation, inflate every graph
        # total, and — because writing the bank would then change the graph that
        # renders it — leave the artifact unable to ever describe its own repo
        # consistently. `understand --check` depends on that fixed point.
        "episteme",
        "memory-bank",
    }
)

#: What the pkg walkers (code, docs, C include admission) skip by name. ``DEFAULT_IGNORE_DIRS``
#: minus ``bin``: a ``bin/`` is .NET build output only where a project or solution file sits beside
#: it (:func:`is_dotnet_output_dir`), and skipping every ``bin/`` made sympy's ``bin/*.py`` and
#: django's ``django-admin.py`` vanish from the graph with no warning (B63). The other walkers keep
#: ``DEFAULT_IGNORE_DIRS`` as it is — they were never reported and are not measured.
PKG_IGNORE_DIRS = DEFAULT_IGNORE_DIRS - {"bin"}

_DOTNET_PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".sln", ".slnx")


def is_dotnet_output_dir(parent: Path, name: str) -> bool:
    """``parent/bin`` is .NET build output: ``parent`` holds a project or solution file.

    Compiled assemblies and, after a publish, copied static assets (``wwwroot/*.js``) — none of it
    source, and the assets would enter the graph as phantom JavaScript. A ``bin/`` with no project
    beside it (scripts, a Python package's entry points) is the repository's own code.
    """
    if name != "bin":
        return False
    try:
        with os.scandir(parent) as entries:
            return any(e.name.endswith(_DOTNET_PROJECT_SUFFIXES) and e.is_file() for e in entries)
    except OSError:
        return False


def is_nested_repo(parent: Path, name: str) -> bool:
    """``parent/name`` is another git checkout — a submodule, a worktree, a vendored clone.

    A submodule's ``.git`` is a *file* (a pointer into the superproject's object store), so a
    check for the directory alone would miss the common case. Either form marks a boundary.

    Why the walkers stop here: a nested checkout has its own HEAD, its own clean/dirty state
    and, in a multi-repo graph, its own scope key. Walking through it presents the inner
    repo's symbols as the outer repo's — and if the inner one is *also* declared in
    ``.spine/repos.yaml`` every symbol exists twice under two ids. Measured on a
    superproject with one submodule: ``py:lib@lib.core.helper`` and
    ``py:super@libs.lib.lib.core.helper`` in the same merged graph, nothing flagging it.
    """
    return (parent / name / ".git").exists()


@dataclass(frozen=True)
class IgnoredPaths:
    """What git ignores and does not track under one walk root, as root-relative POSIX paths.

    ``dirs`` are directories git reports whole (every path in them is ignored and untracked);
    ``files`` are single files, including ignored files inside a directory that also holds a
    tracked one -- git then lists the files, not the directory, so a ``git add -f`` file stays.

    Both are held, and compared, in Unicode NFC: macOS git reports names composed
    (``core.precomposeunicode``) while the filesystem hands the walk a name as it was written, so a
    decomposed ``café/`` never matched byte for byte and was walked.
    """

    dirs: frozenset[str]
    files: frozenset[str]

    def covers(self, rel: str, *, is_dir: bool) -> bool:
        """``rel`` (root-relative, POSIX) is ignored and untracked, or lies under a directory that is."""
        rel = unicodedata.normalize("NFC", rel)
        if not is_dir and rel in self.files:
            return True
        parts = rel.split("/")
        stop = len(parts) if is_dir else len(parts) - 1
        return any("/".join(parts[: i + 1]) in self.dirs for i in range(stop))


_GIT_TIMEOUT_S = 30

#: The variables git calls repository-local (``git rev-parse --local-env-vars``). A git hook exports
#: ``GIT_DIR`` and ``GIT_INDEX_FILE``, and git obeys them over ``-C``: run from a pre-commit hook in
#: another repository, ``ls-files`` read *that* index against this tree and reported a tracked file
#: as ignored -- a refuse-only rule hiding the repository's own code.
_GIT_REPO_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_INTERNAL_SUPER_PREFIX",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_REPO_ENV}
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, timeout=_GIT_TIMEOUT_S, check=False, env=env
    )


def _first_line(stderr: bytes) -> str:
    lines = stderr.decode("utf-8", "replace").strip().splitlines()
    return lines[0] if lines else "git failed"


def _inside_a_checkout(root: Path) -> bool:
    """A ``.git`` at ``root`` or above: what git would have found, asked without git."""
    try:
        here = root.resolve()
    except OSError:
        return False
    return any((d / ".git").exists() for d in (here, *here.parents))


def git_ignore_rule(root: Path) -> tuple[IgnoredPaths | None, str | None]:
    """What git ignores and does not track under ``root`` (B119), and why not when it cannot say.

    The pkg walkers skip build output by *name* (``build``, ``dist``, ...). A TypeScript repo's
    ``tsc`` output goes to a gitignored ``out/``, which is no such name, and its compiled ``.js``
    entered the graph as source: every function twice, the copy first, and a gitignored tree leaves
    ``git status`` clean, so the commit-keyed cache froze whichever graph came first.

    Refuse-only by construction: a walker uses this to *skip* paths, never to add one, so the walk
    with git is a subset of the walk without it. ``(paths, None)`` when git answered. Otherwise the
    paths are ``None`` -- the walk is the name rules' walk -- and the reason says which case it was:

    - ``None`` by design (D3): ``root`` is not in a git work tree, or ``root`` itself is ignored (a
      scratch clone inside another repo's ignored directory). Run from in there, ``ls-files -o -i``
      reports the root itself (``./``) as ignored, or fails, depending on the pattern (git 2.54):
      one path normalisation away from skipping every file. The person pointed Spine at that
      folder on purpose. Whether the root is ignored is asked only below the top level: at the top,
      ``check-ignore .`` matches an unanchored ``*`` (a whitelist ``.gitignore``) although a work
      tree's root is never ignored, and the rule switched itself off.
    - a line of text when there is a ``.git`` at or above ``root`` but git is missing, fails (a
      "dubious ownership" refusal in a container) or times out. The build tree is back in the
      graph, and that must not look the same as "git ignored nothing".

    ``--exclude-standard`` also reads the user's global excludes file. That only ever removes
    *untracked* files, which a CI checkout never has, so it moves a laptop's graph towards CI's.
    """
    try:
        where = _git(root, "rev-parse", "--show-prefix")
        if where.returncode != 0:
            return None, _first_line(where.stderr) if _inside_a_checkout(root) else None
        if where.stdout.strip():  # below the top level: is the root itself ignored?
            probe = _git(root, "check-ignore", "-q", ".")
            if probe.returncode == 0:
                return None, None
            if probe.returncode != 1:
                return None, _first_line(probe.stderr)
        listed = _git(root, "ls-files", "-o", "-i", "--exclude-standard", "--directory", "-z")
    except FileNotFoundError:
        return None, "git not found" if _inside_a_checkout(root) else None
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git failed: {type(exc).__name__}" if _inside_a_checkout(root) else None
    if listed.returncode != 0:
        return None, _first_line(listed.stderr)
    dirs: set[str] = set()
    files: set[str] = set()
    for entry in listed.stdout.decode("utf-8", "surrogateescape").split("\0"):
        name = unicodedata.normalize("NFC", entry.rstrip("/"))
        if entry.endswith("/"):
            dirs.add(name)
        elif entry:
            files.add(name)
    return IgnoredPaths(frozenset(dirs), frozenset(files)), None


def git_ignored(root: Path) -> IgnoredPaths | None:
    """:func:`git_ignore_rule` without the reason: ``None`` whenever git cannot say."""
    return git_ignore_rule(root)[0]


__all__ = [
    "DEFAULT_IGNORE_DIRS",
    "PKG_IGNORE_DIRS",
    "IgnoredPaths",
    "git_ignore_rule",
    "git_ignored",
    "is_dotnet_output_dir",
    "is_nested_repo",
]
