"""What the pkg walkers skip, the directory questions they ask, and what git ignores — a leaf module.

These lived in ``extractor.py`` and ``clang_includes.py`` imported them from there, which made a cycle
(``clang_includes`` -> ``extractor`` -> ... -> ``clang_includes``) that CodeQL's ``py/cyclic-import`` flags
even though every import was function-local, and a Code-scanning comment at Note severity blocks a release
promotion until its thread is resolved (alert 319, a re-fingerprint of 244 after B63 edited the line). The
names need nothing from the language front-ends, so they sit here, importing nothing from the package;
``extractor`` re-exports them and every other importer is unchanged.
"""

from __future__ import annotations

import errno
import fnmatch
import os
import subprocess
import unicodedata
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

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
#: as ignored -- a refuse-only rule hiding the repository's own code. git lists ``GIT_CONFIG*`` there
#: too; they are kept on purpose. They say how to read a repository, not which one, and the cache's own
#: git call (``persistence.repo_state``) sees them: a container's ``safe.directory`` or excludes file
#: handed over in ``GIT_CONFIG_COUNT`` made that call answer "clean" while this one walked ``out/``.
_GIT_REPO_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
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
    for d in (here, *here.parents):
        try:
            if (d / ".git").exists():
                return True
        except OSError:  # an unlistable directory: the walk reports it (B108), this must not crash first
            continue
    return False


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


#: Ignored directory names worth telling the user about: the ones that can hold real source.
#: ``node_modules`` and ``__pycache__`` are not a surprise anywhere, so naming them would only bury
#: the line.
NOTABLE_IGNORED_DIRS = frozenset({"bin", "build", "dist", "obj", "target", "vendor", "Pods"})


def symlink_admitted(
    path: Path,
    root_real: Path,
    ignore_dirs: Collection[str] = DEFAULT_IGNORE_DIRS,
    ignored: IgnoredPaths | None = None,
) -> bool:
    """A symlinked file is admitted under its own name, unless its target is somewhere the walk
    would never go: outside the root, inside an ignored, dot-prefixed or nested-checkout
    directory, or something git ignores and does not track (``ignored``, B119 -- after a build a
    link into ``out/`` brought the compiled copy back under the link's name). Otherwise
    `ios/Pods/Headers/Public/X.h` → `node_modules/react-native/X.h` puts an ignored tree back into
    the graph one file at a time.

    One rule for every walker that reads file *contents* or counts files by language — the code
    extractor, the doc walker (`doc_source.read_doc_pages`) and `profile.language_file_counts` —
    so they answer alike. A link that leaves the root is refused too: its text is not the
    repository's, and doc text can reach a codegen prompt. A dangling link is refused.
    ``root_real`` is the *resolved* root. Directory links need no rule: ``os.walk`` does not
    descend them. (Moved here from ``extractor`` with :func:`walk_repo`, which applies it;
    ``extractor`` re-exports it.)
    """
    try:
        target = path.resolve(strict=True)
        rel = target.relative_to(root_real)
    except (OSError, ValueError):
        return False
    parts = rel.parts[:-1]
    if any(p in ignore_dirs or p.startswith(".") for p in parts):
        return False
    if ignored is not None and ignored.covers(rel.as_posix(), is_dir=False):
        return False
    cursor = root_real
    for p in parts:
        if is_nested_repo(cursor, p) or is_dotnet_output_dir(cursor, p):
            return False
        cursor = cursor / p
    return True


class UnreadableRootError(PermissionError):
    """The root of a ``strict_root`` walk could not be listed.

    Raised, never reported, for the graph's own walk: a graph of a root nobody could read is not a
    partial graph, it is no graph, and an empty answer would read as "this code has no callers"
    (B108). Every other walk asks about a folder that may be locked, and gets nothing (B148).
    """


#: How many unreadable paths a report names; the rest are counted (invariant 7, bound honestly).
MAX_UNREADABLE_SHOWN = 20


@dataclass
class WalkHealth:
    """What a repository walk skipped or could not read — reported beside the graph, never as facts.

    An empty or partial graph looks exactly like a repository with less code unless something says
    why (D9). Three things make a graph smaller than the repository and are *problems*:

    * ``unreadable`` — directories the walk could not list (B108). A walk with any is ``partial``,
      and a partial walk is never cached (D14): cached, it would be this commit's graph after the
      folder became readable.
    * ``missing_grammars`` — files of a language no installed front-end reads, counted by the
      extra that would read them (B107: ``{"typescript": 2}``).
    * ``gitignore_off`` — why git could not say what it ignores, so build output was walked (B119/B125).

    Two more are counts of what the rules skipped *on purpose* (``gitignored_skipped``,
    ``ignored_dirs_seen``): context, not problems, so a walk with only those is still ``clean``.
    """

    unreadable: tuple[str, ...] = ()
    missing_grammars: dict[str, int] = field(default_factory=dict)
    gitignore_off: str | None = None
    gitignored_skipped: dict[str, int] = field(default_factory=dict)
    ignored_dirs_seen: dict[str, int] = field(default_factory=dict)

    @property
    def partial(self) -> bool:
        return bool(self.unreadable)

    @property
    def clean(self) -> bool:
        return not (self.unreadable or self.missing_grammars or self.gitignore_off)

    def as_dict(self) -> dict[str, Any]:
        """The problems only, for an answer a person or an agent reads: ``{}`` when there are none.
        ``unreadable`` is capped at :data:`MAX_UNREADABLE_SHOWN` with the total beside it."""
        out: dict[str, Any] = {}
        if self.unreadable:
            out["unreadable"] = list(self.unreadable[:MAX_UNREADABLE_SHOWN])
            out["unreadable_count"] = len(self.unreadable)
            out["partial"] = True
        if self.missing_grammars:
            out["missing_grammars"] = dict(sorted(self.missing_grammars.items()))
        if self.gitignore_off:
            out["gitignore_off"] = self.gitignore_off
        return out

    def to_payload(self) -> dict[str, Any]:
        """Everything, for the commit-keyed cache: a cache hit must report what the run that wrote it saw."""
        return {
            "unreadable": list(self.unreadable),
            "missing_grammars": dict(sorted(self.missing_grammars.items())),
            "gitignore_off": self.gitignore_off,
            "gitignored_skipped": dict(sorted(self.gitignored_skipped.items())),
            "ignored_dirs_seen": dict(sorted(self.ignored_dirs_seen.items())),
        }

    @classmethod
    def from_payload(cls, raw: dict[str, Any]) -> WalkHealth:
        return cls(
            unreadable=tuple(str(p) for p in raw.get("unreadable") or ()),
            missing_grammars={str(k): int(v) for k, v in (raw.get("missing_grammars") or {}).items()},
            gitignore_off=raw.get("gitignore_off") or None,
            gitignored_skipped={str(k): int(v) for k, v in (raw.get("gitignored_skipped") or {}).items()},
            ignored_dirs_seen={str(k): int(v) for k, v in (raw.get("ignored_dirs_seen") or {}).items()},
        )


_COMPUTE: Any = object()


def walk_repo(
    root: Path | str,
    *,
    ignore_dirs: Collection[str] = PKG_IGNORE_DIRS,
    ignored: IgnoredPaths | None = _COMPUTE,
    health: WalkHealth | None = None,
    strict_root: bool = False,
) -> Iterator[tuple[Path, list[str], list[str]]]:
    """Walk a repository the way the graph does: ``(dirpath, dirnames, filenames)``, like ``os.walk``.

    The one walker (D13). Pruned by the graph's full rule set before the caller sees a level:
    dot-directories, ``ignore_dirs`` (``bin`` only beside a .NET project, :func:`is_dotnet_output_dir`),
    what git ignores and does not track (:func:`git_ignore_rule`), nested checkouts
    (:func:`is_nested_repo`); a symlinked file only if :func:`symlink_admitted`. Names are matched
    on the root-relative path, never the absolute one (B141).

    **Sorted**, so the order is the same on every filesystem (``understand``'s determinism).
    ``dirnames`` is the list ``os.walk`` descends, so a caller may prune it further in place,
    top-down (D21). ``ignored`` may be passed in to spare a subtree caller the git call;
    ``None`` switches the git rule off.

    Directories that cannot be listed are recorded in ``health.unreadable`` and the walk goes on.
    A root that exists but cannot be listed yields nothing and is recorded as ``"."``, as
    ``os.walk`` answered: about fifty callers walk a sub-folder (``tests/``, ``src/main``) and
    one locked folder must not crash a test-environment setup (B148). With ``strict_root`` --
    the graph's extractor only -- it raises :class:`UnreadableRootError` instead (B108).
    A root that does not exist, or is not a directory, yields nothing: about forty walkers ask
    "what is under ``root/lib``?" of a directory that may not be there, and ``os.walk`` answered
    "nothing" -- an absent directory hides no code.
    """
    root = Path(root)
    report = health if health is not None else WalkHealth()
    if ignored is _COMPUTE:
        ignored, report.gitignore_off = git_ignore_rule(root)
    root_real = root.resolve()
    failed: list[OSError] = []

    def _note(exc: OSError) -> None:
        failed.append(exc)

    yielded_root = False
    for dirpath, dirnames, filenames in os.walk(root, onerror=_note):
        while failed:
            _record_unreadable(failed.pop(0), root, report)
        here = Path(dirpath)
        rel = here.relative_to(root).as_posix()
        prefix = "" if rel == "." else rel + "/"
        kept: list[str] = []
        for d in sorted(dirnames):
            if d.startswith("."):
                continue
            if d in ignore_dirs or is_dotnet_output_dir(here, d):
                if d in NOTABLE_IGNORED_DIRS:
                    report.ignored_dirs_seen[d] = report.ignored_dirs_seen.get(d, 0) + 1
                continue
            if ignored is not None and ignored.covers(prefix + d, is_dir=True):
                _count_gitignored(report, prefix + d)
                continue
            try:
                nested = is_nested_repo(here, d)
            except OSError:
                nested = False  # unlistable: let os.walk try it, so it is recorded as unreadable
            if not nested:
                kept.append(d)
        dirnames[:] = kept
        files: list[str] = []
        for name in sorted(filenames):
            if ignored is not None and ignored.covers(prefix + name, is_dir=False):
                _count_gitignored(report, prefix + name)
                continue
            path = here / name
            if path.is_symlink() and not symlink_admitted(path, root_real, ignore_dirs, ignored):
                continue
            files.append(name)
        yielded_root = True
        yield here, dirnames, files
    for exc in failed:
        if not yielded_root:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                return  # no such directory: nothing to read, as `os.walk` and `rglob` answer
            if strict_root:
                raise UnreadableRootError(exc.errno or 13, f"cannot list {root}: {exc.strerror}", str(root))
        _record_unreadable(exc, root, report)


def repo_files(
    root: Path | str,
    *patterns: str,
    ignore_dirs: Collection[str] = PKG_IGNORE_DIRS,
    ignored: IgnoredPaths | None = _COMPUTE,
    health: WalkHealth | None = None,
) -> Iterator[Path]:
    """The files :func:`walk_repo` admits under ``root`` whose name matches one of ``patterns``.

    For the walkers that only want files (``rglob("*.py")``'s callers): the same rule set, in the
    same sorted top-down order, so a caller that needs path order still sorts. ``patterns`` are
    shell patterns matched case-sensitively against the file *name*, as ``rglob`` matches them;
    none means every file. ``root`` may be a subtree of a checkout: git answers for the subtree,
    relative to it, so the gitignore rule holds below the top level too.
    """
    for here, _dirnames, filenames in walk_repo(
        root, ignore_dirs=ignore_dirs, ignored=ignored, health=health
    ):
        for name in filenames:
            if not patterns or any(fnmatch.fnmatchcase(name, pat) for pat in patterns):
                yield here / name


def in_skipped_dir(root: Path, rel: str, *, ignore_dirs: Collection[str] = PKG_IGNORE_DIRS) -> bool:
    """``rel`` (root-relative) lies in a directory :func:`walk_repo` never enters, by its name rules.

    For a caller that holds *names* rather than a walk -- the files ``git diff`` lists -- and must
    drop the same ones: a dot-directory, an ``ignore_dirs`` name, a .NET ``bin/``, a nested
    checkout. Git's own ignore rule is not asked: a name git lists as changed is tracked or not
    ignored.
    """
    cursor = root
    for part in PurePosixPath(rel.replace("\\", "/")).parts[:-1]:
        if part.startswith(".") or part in ignore_dirs:
            return True
        if is_dotnet_output_dir(cursor, part) or is_nested_repo(cursor, part):
            return True
        cursor = cursor / part
    return False


def _record_unreadable(exc: OSError, root: Path, report: WalkHealth) -> None:
    where = Path(exc.filename) if exc.filename else root
    try:
        rel = where.relative_to(root).as_posix()
    except ValueError:
        rel = str(where)
    if rel not in report.unreadable:
        report.unreadable = (*report.unreadable, rel)


def _count_gitignored(report: WalkHealth, rel: str) -> None:
    top = rel.split("/", 1)[0]
    report.gitignored_skipped[top] = report.gitignored_skipped.get(top, 0) + 1


__all__ = [
    "DEFAULT_IGNORE_DIRS",
    "NOTABLE_IGNORED_DIRS",
    "PKG_IGNORE_DIRS",
    "IgnoredPaths",
    "UnreadableRootError",
    "WalkHealth",
    "git_ignore_rule",
    "git_ignored",
    "in_skipped_dir",
    "is_dotnet_output_dir",
    "is_nested_repo",
    "repo_files",
    "symlink_admitted",
    "walk_repo",
]
