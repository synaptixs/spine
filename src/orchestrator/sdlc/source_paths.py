"""Source paths a ticket or a design names — one regex, one resolver, two consumers.

``design._stated_paths`` and ``codegen._paths_from`` each carried a copy of
``\\b((?:src/|tests/)[\\w./-]+\\.py)\\b`` — "duplicated rather than shared: a six-character regex
is cheaper to repeat than a new coupling". Two things changed. The regex is no longer six
characters: it has to accept every suffix a front-end extracts, a Windows separator, and a
**bare basename**, because that is how tickets name files — NSS-1231 wrote
``EBSOrderApiClient.cs``, no directory, and on a .NET repository neither copy could see it. The
one lever a ticket author has to override a keyword guess was dead outside Python. And two
copies of a regex that must agree is the drift every plan's §6.1 exists to prevent.

Deterministic and stdlib-only: same text and tree in, same paths out.
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.pkg.walk_rules import walk_repo

#: Every suffix a front-end extracts, plus the Razor/cshtml pair the C# front-end reads.
#: ``.t`` (Perl tests) and ``.h`` are left out on purpose: "e.g. c.h" and "v1.t" are prose, and a
#: header a ticket names will be named next to its ``.c``/``.cpp``.
SOURCE_SUFFIXES: tuple[str, ...] = (
    "py",
    "cs",
    "razor",
    "cshtml",
    "ts",
    "tsx",
    "java",
    "kt",
    "kts",
    "go",
    "rs",
    "php",
    "pl",
    "pm",
    "sql",
    "c",
    "cpp",
    "cc",
    "cxx",
    "hpp",
    "hh",
    "hxx",
)

#: A repo-relative path or a bare filename with a source suffix. Either separator, because a
#: .NET shop's ticket says ``Shared\\Enums\\ProductGroup.cs``; normalised by :func:`normalise`.
PATH_RE = re.compile(r"\b((?:[\w.-]+[\\/])*[\w.-]+\.(?:" + "|".join(SOURCE_SUFFIXES) + r"))\b")


def normalise(rel: str) -> str:
    """``/``-separated, no leading ``./``, no surrounding whitespace."""
    rel = rel.replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _path_shaped(rel: str) -> bool:
    """Refuse what the regex accepts but prose produces: ``3.c``, ``Fig. 2.c``, ``a.b.c``, ``v1.2.pl``.

    A bare name's stem must start with a letter and hold no dot unless that dot introduces
    another source suffix (``App.razor.cs`` is a code-behind file; ``a.b.c`` is not a file).
    """
    name = rel.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    if not stem or not (stem[0].isalpha() or stem[0] == "_"):
        return False
    return "/" in rel or "." not in stem or stem.rsplit(".", 1)[-1] in SOURCE_SUFFIXES


def named_paths(text: str) -> list[str]:
    """The paths ``text`` names, normalised, first-appearance order, deduplicated."""
    out: list[str] = []
    for raw in PATH_RE.findall(text):
        rel = normalise(raw)
        if rel and _path_shaped(rel) and rel not in out:
            out.append(rel)
    return out


def find_by_basename(root: Path, name: str, *, limit: int | None = 2) -> list[str]:
    """Repo-relative paths under ``root`` whose basename is ``name`` — at most ``limit`` of them.

    Walks the way the graph does (:func:`~orchestrator.pkg.walk_rules.walk_repo`), in sorted
    order so the answer is byte-stable. Pruning by name only, a ticket naming ``calls.js`` on a
    TypeScript repository after ``tsc`` resolved to the compiled ``out/src/calls.js`` (B126): git
    ignores ``out/``, and so does this walk. Stops as soon as ``limit`` is reached, so an
    ambiguous name costs two hits, not a full tree. ``limit=None`` finds every location, which a
    partial path needs: its suffix filter must see all of them (B159).
    """
    hits: list[str] = []
    for here, _dirnames, filenames in walk_repo(root):
        if name in filenames:
            hits.append((here / name).relative_to(root).as_posix())
            if limit is not None and len(hits) >= limit:
                break
    return hits


def basename_index(root: Path, *, limit: int | None = None) -> dict[str, list[str]]:
    """Every basename under ``root`` → its repo-relative paths, from one walk.

    :func:`find_by_basename` stops at two hits, which bounds an *ambiguous* name — but a name
    that does not exist walks the whole tree, and a ticket naming three files that were
    renamed walked it three times. A caller resolving several names builds this once and
    hands it to :func:`resolve`. Same walk, same order, same skips as the single lookup.

    Every location is kept unless ``limit`` says otherwise. The walk visits the whole tree
    either way, so a cap saved memory only — and capped at two it made a partial path look
    unique: ``a/pkg/util.py`` and ``b/util.py`` kept, ``c/pkg/util.py`` dropped, and the
    suffix filter saw one match for ``pkg/util.py`` where there are two (B159).
    """
    index: dict[str, list[str]] = {}
    for here, _dirnames, filenames in walk_repo(root):
        for name in filenames:
            hits = index.setdefault(name, [])
            if limit is None or len(hits) < limit:
                hits.append((here / name).relative_to(root).as_posix())
    return index


def resolve(rel: str, root: Path, *, index: dict[str, list[str]] | None = None) -> str | None:
    """``rel`` as a repo-relative path that exists under ``root``, or ``None``.

    A path is taken as written when it exists. A bare basename is resolved to its one
    location; two locations is a guess, and a guessed target is worse than a missing one.
    A partial path is resolved the same way, by its tail: ``pkg/walk_rules.py`` is the one
    file whose path ends in ``/pkg/walk_rules.py``, on whole directory names, so ``mypkg/`` is
    never ``pkg/``. Tickets name files package-relative, and refusing every ``/`` that was not
    the path from the root put SSPN-286's real fix files under "a file to create" (B159).
    A path whose first directory exists at the root is written from the root, and is never
    matched by suffix. With an ``index`` from :func:`basename_index` the lookup costs no walk.
    """
    rel = normalise(rel)
    if not rel or ".." in rel.split("/"):
        return None
    candidate = root / rel
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return None  # a path that leaves the root is not a path under it
    if candidate.is_file():
        return rel
    name = rel.rsplit("/", 1)[-1]
    partial = name != rel
    if partial and (root / rel.split("/", 1)[0]).is_dir():
        # Written from the root: `tests/test_walk.py` with a root `tests/` is a file to create
        # there, never `plugins/foo/tests/test_walk.py` — that redirect told codegen to change a
        # plugin's test and dropped the new file from §7 (B159 review).
        return None
    if index is not None:
        hits = index.get(name, [])
    else:
        hits = find_by_basename(root, name, limit=None if partial else 2)
    if partial:
        hits = [h for h in hits if h.endswith("/" + rel)]
    return hits[0] if len(hits) == 1 else None


__all__ = [
    "PATH_RE",
    "SOURCE_SUFFIXES",
    "basename_index",
    "find_by_basename",
    "named_paths",
    "normalise",
    "resolve",
]
