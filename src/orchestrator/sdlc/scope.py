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

Test files are always in scope, by the one rule (``pkg.test_rule.is_test_path``). Through P4 this
module kept a tenth copy of that rule: it let a product ``src/spec/`` folder into a confident scope
and kept real tests out in Perl (``t/basic.t``), Ruby (``cart_spec.rb``), ``.mts`` and ``*.UnitTests``
projects (B146). One filter sits on top of the rule, like ``test_location``'s ``conftest.py`` one:
a Ruby file under ``spec/`` is in scope, because RSpec's ``spec_helper.rb`` and ``spec/support/``
are not tests by the rule (D24) yet a test change edits them (D43).

Deterministic, and it imports only the leaf rule, so ``design`` and ``codegen`` can both import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from orchestrator.pkg.test_rule import is_test_path

#: The rendered heading for the files a design lists as context. ``codegen`` keys on it to keep
#: them out of the files it shows as ones "this ticket is going to change". Lives here, not in
#: ``design``, so ``codegen`` can read it without importing ``design`` (which imports ``codegen``).
READ_HEADING = "Files to read (reference — do not modify)"


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

        In scope: a listed file; test code by the one rule, and a Ruby file under ``spec/``
        (tests accompany any change); and the ``__init__.py`` of a package this change adds a
        file to, which is where a new module's exports go.
        """
        rel = _norm(rel)
        path = PurePosixPath(rel)
        if rel in self.files:
            return True
        if is_test_path(rel) or _rspec_support(path):
            return True
        return path.name == "__init__.py" and str(path.parent) in new_dirs


def _rspec_support(path: PurePosixPath) -> bool:
    """A Ruby file under a ``spec/`` folder: RSpec's helpers and support code (D43)."""
    return path.suffix == ".rb" and "spec" in path.parts[:-1]


__all__ = ["READ_HEADING", "EditScope"]
