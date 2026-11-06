"""Which of a worktree's changed files are tests.

Which *paths* are tests is the one rule, :func:`orchestrator.pkg.test_rule.is_test_path`; this
module kept its own copy, and ``codegen`` an older, narrower one, until the KG track's P4 put
every caller on the shared rule. It is re-exported here for the runners that import it.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.test_rule import is_test_path
from orchestrator.pkg.walk_rules import in_skipped_dir
from orchestrator.sdlc.process import ExecCapture, exec_capture


async def changed_test_files(
    root: Path,
    *,
    base: str | None = None,
    suffixes: tuple[str, ...] | None = None,
    capture: ExecCapture | None = None,
) -> list[str]:
    """Repo-relative test files changed since ``base`` (default ``HEAD``), sorted.

    "Since ``base``" is what lets a runner keep selecting a run's tests after its build has been
    committed: ``autorun`` commits the build and then re-runs the same runner after every review
    fix, and a selection read from the working tree alone finds nothing at that point. Staged,
    unstaged, renamed and untracked files all count, and ``-z`` keeps a name containing spaces or
    quotes intact. A production file stashed away does not appear, but a test file beside it does,
    which is what lets the coverage probe re-run the same selection with the change set aside.

    ``suffixes`` narrows the selection to files the caller's test command can take: a fixture
    under ``tests/`` is a test *path* but not a test file, and handing it to ``pytest`` is an
    error that no refine pass can fix.
    """
    run_capture = capture or exec_capture
    rc, output = await run_capture(
        ("git", "diff", "--name-only", "-z", "--diff-filter=ACMR", base or "HEAD", "--"),
        cwd=str(root),
        timeout=60,
    )
    if rc:
        raise RuntimeError(f"Cannot identify changed test files: {output.strip()}")
    names = {name for name in output.split("\0") if name}
    rc, output = await run_capture(
        ("git", "ls-files", "--others", "--exclude-standard", "-z"), cwd=str(root), timeout=60
    )
    if rc:
        raise RuntimeError(f"Cannot identify changed test files: {output.strip()}")
    names |= {name for name in output.split("\0") if name}

    selected: set[str] = set()
    for name in names:
        # walk_repo's name rules, not a private set: a test the graph never sees is not selected
        if in_skipped_dir(root, name):
            continue
        full = root / name
        if full.is_symlink() or not full.is_file():
            continue
        if suffixes is not None and not name.endswith(suffixes):
            continue
        if is_test_path(name):
            selected.add(Path(name).as_posix())
    return sorted(selected)
