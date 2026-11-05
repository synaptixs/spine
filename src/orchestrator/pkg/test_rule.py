"""The one answer to "is this file test code?": a path rule every surface shares.

Spine had nine answers to this question in seven packages, and they disagreed: on 17 of this
repository's own code files, and on 11 of 12 sample paths, one per language convention. One
called ``latest.py`` a test (B138: a case-insensitive ``Test.<ext>``), one called
``intake/specs.py`` and ``sdlc/own_tests.py`` tests (B139: a ``specs`` segment and an
``*_tests`` file name), three missed every TypeScript test file, and the ranker's missed
TypeScript, JavaScript and Go tests alike. Each fix landed in one copy and not the others.

This module is the rule. The graph stores its answer on every code node as ``Node.test``, set
once at extraction (``extractor.mark_tests``); code that asks about a path that is not a graph
node — a worktree's changed files, a pull request's diff — calls :func:`is_test_path` directly.

It is a **path** rule on purpose (D12). A name rule would make flask's public
``Flask.test_client`` a test and drop it from every page that describes the product. The one
exception is a language that marks tests *inside* the file: Rust's ``#[test]`` and
``#[cfg(test)] mod`` are read by the Rust front-end and OR'd into the same fact (D16).

**The rule, on a repository-relative path:**

* a *directory* on the path named ``test``, ``tests``, ``__tests__`` or ``androidTest``
  (any case), or a PascalCase test project or folder (``Shop.Tests``, ``UnitTests``,
  ``IntegrationTest``), matched case-sensitively;
* or a *file* named ``test_*``, ``*_test.<ext>``, ``*.test.<ext>``, ``*.spec.<ext>``,
  ``tests.<ext>``, ``conftest.py``, ``*.t`` (Perl), ``*_spec.rb`` (RSpec), a PascalCase
  ``FooTest`` / ``FooTests`` (case-sensitive), or a JVM ``FooSpec`` (Kotest, Spock).

**What it deliberately does not call a test**, each one a production file some copy misread:

* ``latest.py``, ``contest.py``, ``selftest.py``: the PascalCase clause is case-sensitive (B138);
* ``intake/specs.py``, ``src/specs/loader.py``, a ``spec/`` folder: outside Ruby, ``spec``
  names product code as often as tests, and RSpec's own file pattern is ``*_spec.rb`` (B139);
* ``sdlc/own_tests.py``: ``*_tests`` names a module *about* tests, not one (B139);
* ``AgentSpec.py``: ``Spec`` is a test suffix only in the JVM languages;
* ``testing/``, a ``test_data`` folder, ``testimonials``, anything that merely contains
  "test". A doubtful name is read as product, because calling product code a test is the
  error that hides it.

A leaf module: it imports nothing from ``orchestrator``, so every package can depend on it.
"""

from __future__ import annotations

import re

#: Directory names that hold tests in every convention Spine reads, compared lower-cased.
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "androidtest"})

#: A PascalCase test project or folder: `Shop.Tests`, `UnitTests`, `IntegrationTest`. Anchored on
#: a lower-case letter, digit or `.` before the capital `T`, so `Contests` and `Protest` stay
#: product code; case-sensitive, so `latest` never matches (B138).
_PASCAL_TEST_DIR = re.compile(r"[a-z0-9.]Tests?$")

#: The file-name half of the PascalCase clause: `CartTest.java`, `CartTests.cs`, `FooTest.php`.
_PASCAL_TEST_STEM = re.compile(r"[a-z0-9]Tests?$")

#: Kotest and Spock name a test class `FooSpec`; outside the JVM a `Spec` suffix is product code
#: (`AgentSpec`).
_JVM_SUFFIXES = frozenset({".kt", ".kts", ".java", ".groovy", ".scala"})
_JVM_SPEC_STEM = re.compile(r"[a-z0-9]Spec$")


def is_test_path(path: str) -> bool:
    """Does this repository-relative path name test code? The rule is in the module docstring."""
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return False
    *dirs, name = parts
    for directory in dirs:
        if directory.lower() in _TEST_DIRS or _PASCAL_TEST_DIR.search(directory):
            return True
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    suffix = f".{ext}" if ext else ""
    return (
        name.startswith("test_")
        or stem.endswith(("_test", ".test", ".spec"))
        or stem == "tests"
        or name == "conftest.py"
        or suffix == ".t"
        or (suffix == ".rb" and stem.endswith("_spec"))
        or _PASCAL_TEST_STEM.search(stem) is not None
        or (suffix in _JVM_SUFFIXES and _JVM_SPEC_STEM.search(stem) is not None)
    )


__all__ = ["is_test_path"]
