"""The files a change may edit, and whether that list is sure enough to enforce (B44).

Measured 2026-09-27: on OpenAI models Spine's codegen edited 6-9 unrelated tracked files per run,
every one of them named by its own design — word matches listed as "Files to touch", and the
blast radius harvested as files "this ticket is going to change". GPT models obeyed the list and
gave each file a justifying comment; Claude mostly ignored it, so the defect stayed latent.

The design now lists edit targets apart from files to read. ``EditScope`` carries the edit list
into codegen with the one bit of judgement the guard needs: whether the list is *confident*.
A list the ticket stated, or any create ticket (its new files are the change), is enforced: a
pre-existing file outside it is refused before it is written. A list built from word matches is
only a guess, so it is never enforced — a guard that reverted a correct fix the guess did not
anticipate would be worse than no guard.

Deterministic and dependency-free, so ``design`` and ``codegen`` can both import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


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
        files = tuple(str(f) for f in (design.get("files_to_touch") or []) if str(f))
        stated = str(design.get("files_origin") or "") == "stated" and bool(files)
        return cls(files=files, create=kind == "create", confident=stated or kind == "create")

    def allows(self, rel: str, *, new_dirs: frozenset[str] = frozenset()) -> bool:
        """Whether the pre-existing file ``rel`` (repo-relative, ``/``-separated) is in scope.

        In scope: a listed file; anything under a ``tests`` directory or named ``test_*`` (tests
        accompany any change); and the ``__init__.py`` of a package this change adds a file to,
        which is where a new module's exports go.
        """
        path = PurePosixPath(rel)
        if rel in self.files:
            return True
        if "tests" in path.parts[:-1] or path.name.startswith("test_") or path.name == "conftest.py":
            return True
        return path.name == "__init__.py" and str(path.parent) in new_dirs


__all__ = ["EditScope"]
