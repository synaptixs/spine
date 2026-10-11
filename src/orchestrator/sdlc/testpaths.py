"""Which paths are tests, and which of a worktree's changed files are.

A leaf module: ``feature_runner`` imports ``testrunner``, so the rule that both need cannot live
in either without a cycle. ``codegen._is_test_file`` is an older, narrower mirror of
``is_test_path`` kept for the same reason; it is left alone here.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.sdlc.process import ExecCapture, exec_capture

#: Directories whose contents are never a change's tests: version control, bytecode, vendored
#: packages, and what Spine itself writes into a checkout (plans and journals).
_SKIPPED_DIRS = frozenset({".git", "__pycache__", "node_modules", ".spine"})


def is_test_path(rel: str) -> bool:
    """Whether a repo-relative path is a test file, in any supported language.

    Two checks that guard generated work — ``_prove_the_tests_test_something`` and
    ``_files_no_test_exercises`` — are driven entirely by this, so a language it does not
    recognise has both of them silently switched off for every run. Kotlin was such a
    language: its tests are ``FooTest.kt`` under ``src/test/``, and neither the suffix list
    nor the ``tests`` (plural) directory check matched either half.
    """
    path = Path(rel.replace("\\", "/"))
    name = path.name
    stem = path.stem
    parts = {p.lower() for p in path.parts}
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", "Test.php", ".t"))
        # `FooTest.kt` / `FooTests.kt` / `FooSpec.kt` — the JVM and Go conventions
        or (path.suffix in (".kt", ".kts", ".java", ".go") and stem.endswith(("Test", "Tests", "Spec")))
        or path.suffix == ".go"
        and stem.endswith("_test")
        or path.suffix == ".rs"
        and (stem.endswith("_test") or "tests" in parts)
        # `tests/` is the Python/PHP convention; Gradle and Maven both use `src/test/`.
        or bool(parts & {"tests", "test", "androidtest"})
    )


async def changed_test_files(root: Path, *, capture: ExecCapture | None = None) -> list[str]:
    """Repo-relative test files this worktree has added or modified, sorted.

    Read from ``git status`` — staged, unstaged, renamed and individual untracked files — with
    ``-z`` so a name containing spaces or quotes arrives unmangled. A production file stashed
    away does not appear, but a test file beside it does, which is what lets the coverage probe
    re-run the same selection with the change set aside.
    """
    run_capture = capture or exec_capture
    rc, output = await run_capture(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"), cwd=str(root), timeout=60
    )
    if rc:
        raise RuntimeError(f"Cannot identify changed test files: {output.strip()}")
    records = iter(output.split("\0"))
    changed: set[str] = set()
    for record in records:
        if not record:
            continue
        status, name = record[:2], record[3:]
        if "R" in status or "C" in status:
            next(records, None)  # -z emits the destination first and the original second
        parts = Path(name).parts
        if any(part in _SKIPPED_DIRS for part in parts[:-1]):
            continue
        full = root / name
        if full.is_symlink() or not full.is_file():
            continue
        if is_test_path(name):
            changed.add(Path(name).as_posix())
    return sorted(changed)
