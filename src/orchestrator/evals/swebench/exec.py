"""Run a project's tests inside an instance image, with this run's changes laid over it.

This is what ``SDLC_TEST_COMMAND`` points at for a benchmark run: ``CommandTestRunner`` calls it from
the worktree with the changed test files, and it prints the test output and exits with the tests' own
code.

**Why an overlay and not a mount.** The first design mounted the worktree over ``/testbed``. Measured
on a scikit-learn instance, that fails at collection: the image's compiled extensions are untracked
build output, so a checkout of tracked files has none of them. The image's own tree is therefore kept
exactly as built and only what the run changed is streamed in — modified and new files, plus a list of
deletions — into a fresh, offline container that is thrown away afterwards (about two seconds a run).

Limits, stated: a C or Cython source edit is not rebuilt, so it changes nothing the tests import; the
official grader rebuilds, so this weakens only the in-loop signal.

Exit codes are the tests' own, except: 97 the image's environment could not be activated, 98 the
overlay could not be extracted, 124 the time limit. Those three are the harness failing, not the
tests, and the adapter files them as such. And 5, which is pytest's code for "no tests collected": a
Django or sympy run that exits 0 after executing zero tests is reported as 5 (B74), because a run
that proves nothing must not read as green.
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tarfile
from collections.abc import Callable, Sequence
from pathlib import Path

#: Member of the overlay that lists, NUL-separated, the paths the run deleted.
DELETED_MANIFEST = ".spine-overlay-deleted"

#: Paths that are Spine's own bookkeeping or bytecode, never part of a change.
_SKIPPED = (".spine/", "BACKLOG.md", ".git/")
_SKIPPED_PARTS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache"})

#: Seconds added to the in-container limit for the outer clock, which only catches a hung docker.
_OUTER_SLACK = 120.0

#: pytest's exit status for "no tests were collected".
NO_TESTS_EXIT = 5

#: What a runner prints when it executed no tests (B74). Only the two runners that still exit 0 in that
#: case are listed: pytest returns 5 itself. A run that executed tests and skipped them is not matched
#: (Django counts skipped tests in "Ran N"; sympy prints "N skipped" after the zero), because the runner
#: did find them. The zero-test wordings are the runners' own, from recorded output; the sympy skipped
#: wording is not verified against a real run, and a variant that does not match leaves the status alone.
_RAN_NOTHING = {
    "django": re.compile(r"^Ran 0 tests\b", re.MULTILINE),
    "sympy": re.compile(r"tests finished: 0 passed, in "),
}

#: Appended to the output of a run whose exit status was changed, so the model is told why.
NO_TESTS_NOTE = (
    "\n[spine] the runner executed 0 tests, so this run proves nothing; exit status changed from 0 to "
    f"{NO_TESTS_EXIT} (pytest's code for 'no tests collected').\n"
)


def ran_nothing(kind: str, output: str) -> bool:
    """Whether the runner itself says it executed no tests. ``False`` for a kind that reports it by exit."""
    pattern = _RAN_NOTHING.get(kind)
    return bool(pattern and pattern.search(output))


#: Bash run in the container. ``SPINE_ACTIVATE`` and ``SPINE_TESTBED`` default to the images' paths and
#: are overridden only by tests, so the script can be run for real under a fake image.
#: File names and labels arrive as arguments (``"$@"``), never as text
#: in this script, and ``timeout -k`` enforces the limit *inside* the container — killing the docker
#: client alone would leave the tests running there.
SCRIPT = r"""
set -u
limit="$1"; kind="$2"; shift 2
args=("$@")
set --   # `source` passes the caller's arguments to the sourced file; conda rejects them
source "${SPINE_ACTIVATE:-/opt/miniconda3/bin/activate}" && conda activate testbed || exit 97
cd "${SPINE_TESTBED:-/testbed}" || exit 97
tar -x -C . || exit 98
if [ -f .spine-overlay-deleted ]; then
  while IFS= read -r -d '' f; do rm -f -- "$f"; done < .spine-overlay-deleted
  rm -f .spine-overlay-deleted
fi
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
go() { exec timeout -k 5 "$limit" "$@" ${args[@]+"${args[@]}"}; }
case "$kind" in
  django) go ./tests/runtests.py --settings=test_sqlite --parallel 1 ;;
  sympy)  go python bin/test -C --verbose -- ;;
  *)      go python -m pytest -q -rfE -p no:cacheprovider -- ;;
esac
"""

Runner = Callable[[list[str], bytes, float], tuple[int, str]]


def kind_for(repo: str) -> str:
    """Which runner a repository's tests use: ``django``, ``sympy``, or plain ``pytest``."""
    return {"django/django": "django", "sympy/sympy": "sympy"}.get(repo, "pytest")


def args_for(kind: str, files: Sequence[str]) -> list[str]:
    """The test files as the runner wants them: Django takes dotted labels, the others take paths."""
    if kind != "django":
        return list(files)
    labels = []
    for f in files:
        rel = f[len("tests/") :] if f.startswith("tests/") else f
        labels.append(rel.removesuffix(".py").replace("/", "."))
    return labels


def _git(cwd: Path, *args: str) -> list[str]:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if done.returncode:
        raise RuntimeError(f"Cannot identify changed files: {done.stderr.decode('utf-8', 'replace').strip()}")
    return [n for n in done.stdout.decode("utf-8", "surrogateescape").split("\0") if n]


def _wanted(name: str) -> bool:
    return not (name.startswith(_SKIPPED) or name in _SKIPPED or _SKIPPED_PARTS & set(Path(name).parts))


def overlay(worktree: Path, base: str) -> bytes:
    """A tar of every file changed since ``base`` (committed or not) plus the deletion manifest.

    ``--no-renames`` is load-bearing: with rename detection a moved file is one ``R`` entry whose old
    path is in neither list, so the container would keep the source the run removed.
    """
    changed = set(
        _git(worktree, "diff", "--no-renames", "--name-only", "-z", "--diff-filter=ACMR", base, "--")
    )
    changed |= set(_git(worktree, "ls-files", "--others", "--exclude-standard", "-z"))
    deleted = _git(worktree, "diff", "--no-renames", "--name-only", "-z", "--diff-filter=D", base, "--")

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name in sorted(n for n in changed if _wanted(n)):
            path = worktree / name
            if path.is_file() or path.is_symlink():
                tar.add(path, arcname=name, recursive=False)
        manifest = b"".join(
            n.encode("utf-8", "surrogateescape") + b"\0" for n in sorted(deleted) if _wanted(n)
        )
        info = tarfile.TarInfo(DELETED_MANIFEST)
        info.size = len(manifest)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(manifest))
    return out.getvalue()


def docker_argv(image_ref: str, kind: str, args: Sequence[str], *, timeout_s: int) -> list[str]:
    """The ``docker run`` that executes :data:`SCRIPT`. Offline, throwaway, and pinned by digest."""
    return [
        "docker",
        "run",
        "-i",
        "--rm",
        "--network",
        "none",
        "--platform",
        "linux/amd64",
        # The images ship a POSIX locale, under which old Django fails tests on file names; the
        # official grader is separate, so this only keeps the in-loop signal free of that noise.
        "-e",
        "LANG=C.UTF-8",
        "-e",
        "LC_ALL=C.UTF-8",
        image_ref,
        "bash",
        "-c",
        SCRIPT,
        "spine-exec",
        str(timeout_s),
        kind,
        *args,
    ]


def _docker(argv: list[str], data: bytes, timeout: float) -> tuple[int, str]:
    done = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    return done.returncode, (done.stdout + done.stderr).decode("utf-8", "replace")


def run(
    image_ref: str,
    base: str,
    kind: str,
    files: Sequence[str],
    *,
    cwd: Path,
    timeout_s: int = 600,
    runner: Runner = _docker,
) -> tuple[int, str]:
    """Overlay ``cwd``'s changes since ``base`` onto the image and run ``files``; ``(code, output)``."""
    argv = docker_argv(image_ref, kind, args_for(kind, files), timeout_s=timeout_s)
    try:
        code, output = runner(argv, overlay(cwd, base), timeout_s + _OUTER_SLACK)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout_s}s (the container did not return)"
    if code == 0 and ran_nothing(kind, output):
        # B74. Refuse-only: this can turn a false green into a failure, never the reverse, and it
        # touches only an exit status of 0 that the runner's own output contradicts.
        return NO_TESTS_EXIT, output + NO_TESTS_NOTE
    return code, output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a project's tests in an instance image.")
    parser.add_argument("--image", required=True, help="image reference, pinned by digest")
    parser.add_argument("--base", required=True, help="the commit the run started from")
    parser.add_argument("--kind", required=True, choices=("django", "sympy", "pytest"))
    parser.add_argument("--timeout", type=int, default=600, help="seconds, enforced inside the container")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    code, output = run(args.image, args.base, args.kind, args.files, cwd=Path.cwd(), timeout_s=args.timeout)
    sys.stdout.write(output)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
