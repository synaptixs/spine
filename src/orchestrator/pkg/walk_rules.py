"""What the pkg walkers skip, and the two directory questions they ask — a leaf module.

These lived in ``extractor.py`` and ``clang_includes.py`` imported them from there, which made a cycle
(``clang_includes`` -> ``extractor`` -> ... -> ``clang_includes``) that CodeQL's ``py/cyclic-import`` flags
even though every import was function-local, and a Code-scanning comment at Note severity blocks a release
promotion until its thread is resolved (alert 319, a re-fingerprint of 244 after B63 edited the line). The
names need nothing from the language front-ends, so they sit here, importing nothing from the package;
``extractor`` re-exports them and every other importer is unchanged.
"""

from __future__ import annotations

import os
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


__all__ = [
    "DEFAULT_IGNORE_DIRS",
    "PKG_IGNORE_DIRS",
    "is_dotnet_output_dir",
    "is_nested_repo",
]
