#!/usr/bin/env python3
"""Four repository shapes, one plan stage each — the SDLC pipeline's shape matrix.

The reusable workflow (`.github/workflows/spine-sdlc.yml`) promises to work on a single
repository, a monorepo package, a multi-repo workspace and a superproject with submodules.
A promise nothing runs is the kind of number STATE-OF-SPINE warns about, so this builds all
four shapes from scratch — real `git`, real commits, a real `submodule add` — and runs the
same commands the workflow's `plan` job runs on each, asserting what that shape must hold:

* every shape: `sdlc plan --spec` produces a build document, twice, **byte-identical** —
  the determinism the plan job is trusted for;
* multirepo and submodule: every declared root is a clean, populated checkout, the merged
  graph is trusted, and `investigate --repos` writes the cross-repo brief;
* multirepo: at least one edge leaves a repository (the `http` join the fixture is for);
* submodule: the submodule's symbols exist **once**, under its own scope — the
  double-count this shape used to produce (see `tests/pkg/test_nested_repos.py`).

No model, no credentials, no network. ~1 minute on a laptop.

    uv run python scripts/sdlc_shapes.py            # all four, in a temp dir
    uv run python scripts/sdlc_shapes.py --keep DIR # build into DIR and leave it for reading
    uv run python scripts/sdlc_shapes.py --shape submodule
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS_HTTP_JOIN = ROOT / "corpus" / "multirepo" / "http_join"

SHAPES = ("single", "monorepo", "multirepo", "submodule")

# One identity for every fixture commit, and the `file` transport that `submodule add` from a
# local path needs (git disables it by default since 2.38.1). Set through git's environment
# config so nothing here depends on the machine's global config.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "shapes",
    "GIT_AUTHOR_EMAIL": "shapes@example.com",
    "GIT_COMMITTER_NAME": "shapes",
    "GIT_COMMITTER_EMAIL": "shapes@example.com",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "protocol.file.allow",
    "GIT_CONFIG_VALUE_0": "always",
}


@dataclass(frozen=True)
class Shape:
    """One fixture: where the graph root is, what to plan, and what a merged graph would read."""

    name: str
    graph_root: Path
    spec: Path
    repos_file: Path | None = None


class ShapeError(AssertionError):
    """A shape did not hold what the pipeline promises for it."""


# ---- helpers ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **_GIT_ENV}, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {cwd} failed: {proc.stderr.strip()}")
    return proc.stdout


def _commit_all(repo: Path, message: str) -> None:
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", message, cwd=repo)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    return path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _spec(path: Path, intent: str, title: str, summary: str, criteria: list[str]) -> Path:
    _write(
        path,
        json.dumps(
            {"intent_id": intent, "title": title, "summary": summary, "acceptance_criteria": criteria},
            indent=2,
        )
        + "\n",
    )
    return path


def _orchestrator() -> list[str]:
    exe = shutil.which("orchestrator")
    if exe is None:
        sys.exit("`orchestrator` is not on PATH — run through `uv run python scripts/sdlc_shapes.py`")
    return [exe]


def _run(*args: str, cwd: Path) -> str:
    proc = subprocess.run([*_orchestrator(), *args], cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ShapeError(f"orchestrator {' '.join(args)} exited {proc.returncode}:\n{proc.stderr.strip()}")
    return proc.stdout


# ---- the four shapes ---------------------------------------------------------------------


def build_single(root: Path) -> Shape:
    """One repository, one package at its root — the shape everything was written for."""
    repo = _init_repo(root / "single")
    _write(repo / "shop" / "__init__.py", "")
    _write(repo / "shop" / "cart.py", "class Cart:\n    def total(self) -> int:\n        return 0\n")
    _write(repo / "README.md", "# shop\n\nA cart.\n")
    _commit_all(repo, "shop")
    spec = _spec(
        root / "single-spec.json",
        "SINGLE-1",
        "Add a discount to Cart.total",
        "Cart.total should apply a percentage discount.",
        ["Cart.total accepts an optional discount", "A discount of 0 leaves the total unchanged"],
    )
    return Shape("single", repo, spec)


def build_monorepo(root: Path) -> Shape:
    """One repository, two packages; the graph root is one package, not the repository."""
    repo = _init_repo(root / "monorepo")
    _write(repo / "packages" / "api" / "api" / "__init__.py", "")
    _write(
        repo / "packages" / "api" / "api" / "orders.py",
        "def place_order(sku: str) -> dict[str, str]:\n    return {'sku': sku}\n",
    )
    _write(repo / "packages" / "web" / "web" / "__init__.py", "")
    _write(repo / "packages" / "web" / "web" / "pages.py", "def home() -> str:\n    return 'home'\n")
    _write(repo / "README.md", "# monorepo\n\nTwo packages.\n")
    _commit_all(repo, "two packages")
    spec = _spec(
        root / "monorepo-spec.json",
        "MONO-1",
        "Validate the sku in place_order",
        "place_order should reject an empty sku.",
        ["place_order raises ValueError on an empty sku"],
    )
    return Shape("monorepo", repo / "packages" / "api", spec)


def build_multirepo(root: Path) -> Shape:
    """Two repositories side by side; the consumer declares the workspace in `.spine/repos.yaml`.

    The sources are the accuracy corpus's `http_join` case — a consumer calling paths the
    provider serves — so the join the multi-repo graph exists for has something to find.
    """
    ws = root / "multirepo"
    web = _init_repo(ws / "web")
    billing = _init_repo(ws / "billing")
    shutil.copytree(CORPUS_HTTP_JOIN / ".web", web, dirs_exist_ok=True)
    shutil.copytree(CORPUS_HTTP_JOIN / ".billing", billing, dirs_exist_ok=True)
    _commit_all(billing, "billing")
    # Relative paths resolve against the config's own directory (`.spine/`), so the repo
    # holding the file is `..` and a sibling checkout is `../../<name>`.
    _write(
        web / ".spine" / "repos.yaml",
        "repos:\n  web: ..\n  billing: ../../billing\n"
        "joins:\n  - kind: http\n    consumer: web\n    provider: billing\n",
    )
    _commit_all(web, "web + workspace")
    spec = _spec(
        root / "multirepo-spec.json",
        "MULTI-1",
        "Retry fetch_order on a 503",
        "web's fetch_order should retry once when billing answers 503.",
        ["fetch_order retries exactly once on 503", "Other status codes are not retried"],
    )
    return Shape("multirepo", web, spec, repos_file=web / ".spine" / "repos.yaml")


def build_submodule(root: Path) -> Shape:
    """A superproject pinning a library as a submodule, both declared in `.spine/repos.yaml`."""
    lib_remote = _init_repo(root / "submodule-remotes" / "lib")
    _write(lib_remote / "lib" / "__init__.py", "")
    _write(lib_remote / "lib" / "core.py", "def helper() -> int:\n    return 1\n")
    _write(lib_remote / "README.md", "# lib\n\nThe library.\n")
    _commit_all(lib_remote, "lib")

    super_ = _init_repo(root / "submodule")
    _write(super_ / "app" / "__init__.py", "")
    _write(
        super_ / "app" / "main.py",
        "from lib.core import helper\n\n\ndef run() -> int:\n    return helper()\n",
    )
    _write(super_ / "README.md", "# super\n\nThe application.\n")
    _commit_all(super_, "app")
    _git("submodule", "add", "-q", str(lib_remote), "libs/lib", cwd=super_)
    _write(super_ / ".spine" / "repos.yaml", "repos:\n  super: ..\n  lib: ../libs/lib\n")
    _commit_all(super_, "pin lib + workspace")
    spec = _spec(
        root / "submodule-spec.json",
        "SUB-1",
        "Log when run falls back",
        "run should log when helper returns a non-positive value.",
        ["run logs at WARNING when helper returns 0 or less"],
    )
    return Shape("submodule", super_, spec, repos_file=super_ / ".spine" / "repos.yaml")


BUILDERS = {
    "single": build_single,
    "monorepo": build_monorepo,
    "multirepo": build_multirepo,
    "submodule": build_submodule,
}


# ---- what each shape must hold -----------------------------------------------------------


def check_plan_is_deterministic(shape: Shape, out: Path) -> Path:
    """The plan job's whole claim: same commit in, same document out. Run it twice."""
    docs: list[bytes] = []
    intent = json.loads(shape.spec.read_text(encoding="utf-8"))["intent_id"]
    for attempt in (out / "first", out / "second"):
        # Planned where the gate reads it, as the workflow now does — `--out` is deprecated because
        # a plan written elsewhere is one `autorun` cannot build — then kept per attempt to compare.
        _run(
            "sdlc",
            "plan",
            "--spec",
            str(shape.spec),
            "--path",
            str(shape.graph_root),
            "--quiet",
            cwd=shape.graph_root,
        )
        written = shape.graph_root / ".spine" / "plans" / f"{intent}-build.md"
        if not written.is_file():
            raise ShapeError(f"{shape.name}: no build document at {written}")
        attempt.mkdir(parents=True, exist_ok=True)
        doc = attempt / written.name
        shutil.copyfile(written, doc)
        docs.append(doc.read_bytes())
    if docs[0] != docs[1]:
        raise ShapeError(f"{shape.name}: two plan runs on the same commit produced different documents")
    if b"**Validity:**" not in docs[0]:
        raise ShapeError(f"{shape.name}: the build document carries no validity verdict")
    return out / "first"


def check_declared_repos(shape: Shape) -> None:
    """Every declared root is a populated, clean checkout — the workflow's pre-plan guard."""
    from orchestrator.pkg.persistence import repo_state
    from orchestrator.pkg.repos import load_repo_config

    assert shape.repos_file is not None
    for key, root in load_repo_config(shape.repos_file):
        sha, dirty = repo_state(root)
        if not sha or dirty or not any(root.iterdir()):
            raise ShapeError(
                f"{shape.name}: declared repo {key!r} at {root} is not a clean populated checkout"
            )


def check_merged_graph(shape: Shape, cache: Path) -> None:
    """The merged graph is trusted, and holds what this shape exists to show."""
    from orchestrator.pkg.persistence import load_or_extract_repos
    from orchestrator.pkg.repos import load_repo_config

    assert shape.repos_file is not None
    merged = load_or_extract_repos(load_repo_config(shape.repos_file), cache_dir=cache)
    if not merged.trusted:
        raise ShapeError(f"{shape.name}: merged graph untrusted — {merged.untrusted_keys}")
    if shape.name == "multirepo":
        crossing = [
            e for e in merged.batch.edges if e.src.startswith("py:web@") and e.dst.startswith("py:billing@")
        ]
        if not crossing:
            raise ShapeError("multirepo: no edge leaves `web` for `billing` — the http join found nothing")
    if shape.name == "submodule":
        core = sorted(
            n.id
            for n in merged.batch.nodes
            if n.provenance and n.provenance.file and n.provenance.file.endswith("core.py")
        )
        if core != ["py:lib@lib.core", "py:lib@lib.core.helper"]:
            raise ShapeError(f"submodule: the submodule's symbols are not scoped exactly once: {core}")


def check_cross_repo_brief(shape: Shape, out: Path) -> None:
    """`investigate --repos` — the workflow's cross-repo step — writes a brief.

    `investigate` takes a title and body, not a spec file; the workflow lifts them from the
    spec the same way.
    """
    assert shape.repos_file is not None
    brief = out / "brief-across-repos.md"
    spec = json.loads(shape.spec.read_text(encoding="utf-8"))
    _run(
        "investigate",
        ".",
        "--title",
        spec["title"],
        "--text",
        spec.get("summary", ""),
        "--repos",
        str(shape.repos_file),
        "--out",
        str(brief),
        cwd=shape.graph_root,
    )
    if not brief.is_file() or not brief.read_text(encoding="utf-8").strip():
        raise ShapeError(f"{shape.name}: investigate --repos wrote no brief")


def run_shape(shape: Shape, work: Path) -> str:
    out = work / f"{shape.name}-out"
    out.mkdir(parents=True, exist_ok=True)
    check_plan_is_deterministic(shape, out)
    notes = ["plan deterministic"]
    if shape.repos_file is not None:
        check_declared_repos(shape)
        check_merged_graph(shape, work / "cache")
        check_cross_repo_brief(shape, out)
        notes += ["declared repos clean", "merged graph trusted", "cross-repo brief"]
    return ", ".join(notes)


# ---- entry point -------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--shape", choices=SHAPES, action="append", help="Run only this shape (repeatable).")
    parser.add_argument("--keep", type=Path, help="Build into this directory and leave it in place.")
    args = parser.parse_args(argv)

    if shutil.which("git") is None:
        sys.exit("git is required")
    chosen = tuple(args.shape) if args.shape else SHAPES
    work = args.keep.resolve() if args.keep else Path(tempfile.mkdtemp(prefix="sdlc-shapes-"))
    if args.keep:
        work.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    print(f"building {len(chosen)} shape(s) under {work}")
    for name in chosen:
        shape = BUILDERS[name](work)
        try:
            note = run_shape(shape, work)
        except ShapeError as exc:
            failures.append(name)
            print(f"  {name:<10} FAIL  {exc}")
        else:
            print(f"  {name:<10} ok    {note}")

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} shape(s) failed: {', '.join(failures)}")
        return 1
    print("\nall shapes hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
