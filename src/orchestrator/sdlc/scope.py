"""The files a change may edit, and whether that list is sure enough to enforce (B44).

Measured 2026-09-27: on OpenAI models Spine's codegen edited 6-9 unrelated tracked files per run,
every one of them named by its own design — word matches listed as "Files to touch", and the
blast radius harvested as files "this ticket is going to change". GPT models obeyed the list and
gave each file a justifying comment; Claude mostly ignored it, so the defect stayed latent.

The design now lists edit targets apart from files to read. ``EditScope`` carries the edit list
into codegen with the one bit of judgement the guard needs: whether the list is *confident* — a
path the ticket stated, or any create ticket (its new files are the change). A module the ticket
named is resolved by the graph and a word match is a guess, so neither is ever enforced.

Even a confident scope refuses only what B44 actually was: an edit outside it that changes no
code, only comments or docstrings (``codegen._refuse_out_of_scope``). A real code change outside
the scope is applied and reported — it may be wiring the change needs, and refusing it would
ship half a change.

Deterministic and dependency-free, so ``design`` and ``codegen`` can both import it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

#: The rendered heading for the files a design lists as context. ``codegen`` keys on it to keep
#: them out of the files it shows as ones "this ticket is going to change". Lives here, not in
#: ``design``, so ``codegen`` can read it without importing ``design`` (which imports ``codegen``).
READ_HEADING = "Files to read (reference — do not modify)"

#: Test files in the layouts the front-ends' languages use: ``test_x.py``/``x_test.py``,
#: ``x_test.go``, ``x.test.ts``/``x.spec.tsx``, ``FooTest.java``/``FooTests.cs``/``FooTest.kt``.
_TEST_FILE_RE = re.compile(
    r"(^test_.*\.py$)|(_test\.(py|go)$)|(\.(test|spec)\.[jt]sx?$)|([A-Z]\w*Tests?\.(java|kt|cs)$)|(^conftest\.py$)"
)
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})


def _norm(rel: str) -> str:
    rel = rel.replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


@dataclass(frozen=True)
class EditScope:
    """The pre-existing files one ticket's change may edit."""

    files: tuple[str, ...] = ()
    create: bool = False
    confident: bool = False

    @classmethod
    def from_design(cls, design: dict[str, Any], spec: dict[str, Any]) -> EditScope:
        """The scope a design and its spec imply — enforced only when it is sure (see module)."""
        kind = str(spec.get("kind") or "").strip().lower()
        files = tuple(_norm(str(f)) for f in (design.get("files_to_touch") or []) if str(f))
        # Only a path the ticket itself wrote is sure. A module it named is resolved by the graph
        # ("module"), and a word match is a guess — neither is enforced.
        stated = str(design.get("files_origin") or "") == "stated" and bool(files)
        return cls(files=files, create=kind == "create", confident=stated or kind == "create")

    def allows(self, rel: str, *, new_dirs: frozenset[str] = frozenset()) -> bool:
        """Whether the pre-existing file ``rel`` (repo-relative, ``/``-separated) is in scope.

        In scope: a listed file; a test file in any front-end's layout, or anything under a test
        directory (tests accompany any change); and the ``__init__.py`` of a package this change
        adds a file to, which is where a new module's exports go.
        """
        rel = _norm(rel)
        path = PurePosixPath(rel)
        if rel in self.files:
            return True
        if _TEST_DIRS.intersection(path.parts[:-1]) or _TEST_FILE_RE.search(path.name):
            return True
        return path.name == "__init__.py" and str(path.parent) in new_dirs


__all__ = ["READ_HEADING", "EditScope"]
