"""Where THIS repository keeps the tests for the modules a change touches (SSPN-223).

**The defect this closes.** P6 stopped 9 of its 32 no-patch runs with the test runner executing zero
tests. The layout block told the model, as "authoritative — overrides any default path guidance", to
put tests under `tests/test_<name>.py`; sympy keeps its tests beside the code
(`sympy/core/tests/test_sympify.py`, run by `bin/test -- <path>`) and django in `tests/<app>_tests/`
(run by dotted label), and neither collects a file at the repository-root `tests/`. Spine saw the
result ("the runner executed 0 tests") but nothing told the retry loop where tests *do* live, so
`sympy-19637` retried six times without ever executing one.

**What this does.** It reads the repository, never guesses. A candidate directory must already exist
and already hold a test file; none is invented. Ranked, best first:

1. **covering** — a test file the PKG says reaches the touched code through the call graph;
2. **sibling** — a `tests/` (or `test/`) directory beside the touched module, the sympy shape;
3. **name** — a test file named for the module (`test_html.py` for `html.py`), the django shape.

A directory found more than one way is listed once, under its best reason. Ties break by path, so the
same repository gives the same answer.

**When it speaks: only as a recovery.** The caller shows the block in `refine`, and only after a run
that executed nothing (:func:`ran_no_tests`). A free replay over P6 showed why: an always-on block
would have changed the prompt of 39 of the 55 runs whose tests executed, 19 of the 26 resolved ones,
with weak suggestions for django. A run whose tests execute is working and never sees it.
:func:`placement_block` is also empty when the best directory is the layout's own, and when nothing
was found.

Pure functions over the file system; no model, no network, no graph (the PKG signal arrives as a list
of paths from the caller).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__all__ = ["Placement", "locate_tests", "placement_block", "ran_no_tests"]

#: A file that is a test by name: `test_x.py`, `x_test.go`, `tests.py`, `x.spec.ts`, `XTests.cs`.
_TEST_FILE = re.compile(r"^(?:test_.+|.+_test|[Tt]ests?|.+\.(?:test|spec)|.+[a-z0-9]Tests?)\.[A-Za-z0-9]+$")
#: Directories that never hold the repository's own tests, and are not worth walking.
_SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", "site-packages", "build", "dist", "venv"})
#: A run that executed nothing, in the four wordings Spine meets: the benchmark wrapper's own note
#: (pinned by a test, because `sdlc/` may not import `evals/`), pytest's, and unittest's.
_NO_TESTS = re.compile(r"the runner executed 0 tests|\bno tests ran\b|\bcollected 0 items\b|\bRan 0 tests\b")
_REASON_RANK = {"covering": 0, "sibling": 1, "name": 2}
_REASON_TEXT = {
    "covering": "tests that reach this code",
    "sibling": "tests beside the module",
    "name": "a test file named for the module",
}
_MAX_LISTED = 3


@dataclass(frozen=True)
class Placement:
    """A directory that already holds tests for the touched code, why, and one test file in it."""

    directory: str
    reason: str
    example: str


def ran_no_tests(output: str) -> bool:
    """Does this test-run output say the runner executed no tests at all?"""
    return _NO_TESTS.search(output) is not None


def _is_test_file(name: str) -> bool:
    return _TEST_FILE.match(name) is not None


def _looks_like_a_test(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return _is_test_file(parts[-1]) or any(p in ("tests", "test", "__tests__") for p in parts[:-1])


def _tests_in(directory: Path) -> list[str]:
    """The test files directly in ``directory``, sorted by name; ``[]`` if it is not one."""
    try:
        return sorted(e.name for e in directory.iterdir() if e.is_file() and _is_test_file(e.name))
    except OSError:
        return []


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _covering(root: Path, paths: Iterable[str]) -> list[Placement]:
    """Directories of the covering test files the caller names, most files first, then by path."""
    per_dir: dict[str, list[str]] = {}
    for raw in paths:
        candidate = PurePosixPath(raw.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            continue
        full = root / candidate
        if not full.is_file():
            continue
        per_dir.setdefault(_rel(root, full.parent), []).append(_rel(root, full))
    ordered = sorted(per_dir.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [Placement(d, "covering", sorted(files)[0]) for d, files in ordered]


def _sibling(root: Path, module: str) -> list[Placement]:
    parent = PurePosixPath(module).parent
    stem = PurePosixPath(module).stem
    found: list[Placement] = []
    for name in ("tests", "test"):
        directory = root / parent / name
        files = _tests_in(directory)
        if not files:
            continue
        own = [f for f in files if f.lower().startswith(f"test_{stem.lower()}.")]
        example = (own or files)[0]
        found.append(Placement(_rel(root, directory), "sibling", _rel(root, directory / example)))
    return found


def _named_for(root: Path, stems: set[str]) -> dict[str, list[str]]:
    """Test files anywhere in the repository named for one of ``stems``, by stem."""
    wanted: dict[str, list[str]] = {}
    lowered = {s.lower(): s for s in stems}
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIPPED_DIRS)
        for name in sorted(files):
            if not _is_test_file(name):
                continue
            base = name.rsplit(".", 1)[0].lower()
            for stem in (base.removeprefix("test_"), base.removesuffix("_test"), base.split(".")[0]):
                if stem in lowered:
                    wanted.setdefault(lowered[stem], []).append(_rel(root, Path(current) / name))
    return wanted


def locate_tests(root: Path, touched: Sequence[str], *, covering: Sequence[str] = ()) -> list[Placement]:
    """Where the repository keeps tests for ``touched`` (repo-relative paths), best first.

    ``covering`` is the PKG's list of test files that reach the touched code.
    """
    root = root.resolve()
    modules = [
        m.replace("\\", "/")
        for m in dict.fromkeys(touched)
        if m and not _looks_like_a_test(m.replace("\\", "/"))
    ]
    candidates: list[Placement] = _covering(root, covering)
    for module in modules:
        candidates.extend(_sibling(root, module))
    stems = {PurePosixPath(m).stem for m in modules if PurePosixPath(m).stem not in ("", "__init__")}
    if stems:
        named = _named_for(root, stems)
        for module in modules:
            for rel in named.get(PurePosixPath(module).stem, []):
                candidates.append(Placement(PurePosixPath(rel).parent.as_posix(), "name", rel))
    # One entry per directory, under its best reason; `covering` keeps the caller's order (most
    # files first), every other reason is ordered by path so the same repository answers the same.
    best: dict[str, tuple[int, Placement]] = {}
    for index, placement in enumerate(candidates):
        held = best.get(placement.directory)
        if held is None or _REASON_RANK[placement.reason] < _REASON_RANK[held[1].reason]:
            best[placement.directory] = (index, placement)

    def order(item: tuple[int, Placement]) -> tuple[int, int, str]:
        index, placement = item
        return (
            _REASON_RANK[placement.reason],
            index if placement.reason == "covering" else 0,
            placement.directory,
        )

    return [placement for _, placement in sorted(best.values(), key=order)]


def _normalised(directory: str) -> str:
    out = directory.strip().replace("\\", "/")
    while out.startswith("./"):
        out = out[2:]
    return out.rstrip("/")


def placement_block(placements: Sequence[Placement], *, tests_dir: str = "tests") -> str:
    """The prompt block, or ``''`` when there is nothing to add.

    Empty when nothing was found, and when the best directory is the layout's own: the block exists
    to correct a placement, never to restate one.
    """
    if not placements:
        return ""
    default = _normalised(tests_dir)
    if _normalised(placements[0].directory) == default:
        return ""
    lines = [
        f"- `{p.directory}/` — {_REASON_TEXT.get(p.reason, p.reason)} (for example `{p.example}`)"
        for p in placements[:_MAX_LISTED]
    ]
    return (
        "\n\nWHERE THIS REPOSITORY KEEPS TESTS FOR THESE MODULES — this takes precedence over the "
        f"generic `{default}/` sentence in the layout above, for these modules only. The repository's "
        "own tests for the code you are changing are in:\n"
        + "\n".join(lines)
        + f"\nPut your new tests there instead of `{default}/`, in a new file named the way its "
        f"neighbours are: a test file in `{default}/` is not collected by this repository's runner and "
        "executes zero tests. Do not overwrite an existing test file.\n\n"
    )
