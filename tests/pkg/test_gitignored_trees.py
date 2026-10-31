"""B119: the pkg walkers skip what git ignores and does not track.

They skipped build output by *name* only (`build`, `dist`, `target`, ...). A TypeScript repo compiles
into a gitignored `out/`, which is not one of those names, so the compiled `.js` entered the graph as
source: measured on one extension repo at one commit, 581 nodes before `tsc` and 895 after, with
`blast_radius` listing the compiled copy first and `regression_gaps` answering "no test exercises it"
about a file git has never seen. A gitignored tree also leaves `git status` clean, so the commit-keyed
cache froze whichever graph came first (invariant 8).

The rule is refuse-only: with git, the walked set is a subset of the walk without it, and every path in
the difference is one `git ls-files -o -i --exclude-standard` reports. Tracked files inside an ignored
directory stay, untracked files that are not ignored stay, and when git cannot answer -- not a work
tree, no `git` binary, or the root itself ignored -- the walk is exactly the name rules' walk.

The fixtures make no commits (except the cache test), so CI's missing git identity cannot bite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.catalog.profile import language_file_counts
from orchestrator.cli import app
from orchestrator.pkg.doc_source import read_doc_pages
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.persistence import load_or_extract
from orchestrator.pkg.walk_rules import IgnoredPaths, git_ignored

_CORPUS = Path(__file__).resolve().parents[2] / "corpus"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


def _write(path: Path, text: str = "def f() -> int:\n    return 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path, ignore: str = "out/\n") -> Path:
    """`src/a.py` and its "compiled" copy `out/src/a.py`, with `.gitignore` naming the build tree."""
    root = tmp_path / "repo"
    _write(root / "src" / "a.py")
    _write(root / "out" / "src" / "a.py")
    _write(root / "out" / "README.md", "# Built\n\nGenerated prose about `f`.\n")
    _write(root / ".gitignore", ignore)
    _git(root, "init", "-q")
    return root


def _files(root: Path) -> set[str]:
    """Code-node files, the graph's own answer to "what was walked"."""
    return {n.provenance.file for n in RepoCodeExtractor().extract(root).nodes if n.provenance}


def _walked(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in RepoCodeExtractor()._iter_files(root)}


def _no_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A `PATH` with no `git` on it: the same failure as a machine without git installed."""
    empty = tmp_path / "no-git-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))


def test_a_gitignored_build_tree_is_not_in_the_graph(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert _files(root) == {"src/a.py"}


def _code(root: Path) -> bool:
    return "out/src/a.py" in _files(root)


def _docs(root: Path) -> bool:
    return any((p.source_file or p.title).startswith("out/") for p in read_doc_pages(root, sections=False))


def _counts(root: Path) -> bool:
    return language_file_counts(root).get("python", 0) == 2


@pytest.mark.parametrize("walker", [_code, _docs, _counts], ids=["code", "docs", "language"])
def test_the_three_walkers_agree_on_ignored_trees(tmp_path: Path, walker: object) -> None:
    root = _repo(tmp_path)
    assert callable(walker)
    assert walker(root) is False


def test_an_ignored_file_outside_an_ignored_dir_is_skipped(tmp_path: Path) -> None:
    root = _repo(tmp_path, ignore="out/\n*_generated.py\n")
    _write(root / "src" / "schema_generated.py")
    assert _files(root) == {"src/a.py"}


def test_a_tracked_file_inside_an_ignored_dir_stays(tmp_path: Path) -> None:
    """`git add -f` makes a file the repository's, ignore rule or not."""
    root = _repo(tmp_path)
    _write(root / "out" / "keep.py")
    _git(root, "add", "-f", "out/keep.py")
    assert "out/keep.py" in _files(root)


def test_an_untracked_but_not_ignored_file_stays(tmp_path: Path) -> None:
    """A new file the developer has not added yet is still their code (D1: not "tracked only")."""
    root = _repo(tmp_path)
    _write(root / "src" / "new.py")
    assert "src/new.py" in _files(root)


def test_outside_git_nothing_changes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "src" / "a.py")
    _write(root / "out" / "src" / "a.py")
    _write(root / ".gitignore", "out/\n")
    assert _files(root) == {"src/a.py", "out/src/a.py"}


def test_an_ignored_root_is_walked_as_asked(tmp_path: Path) -> None:
    """A scratch clone inside another repo's ignored directory is walked in full, not emptied (D3)."""
    outer = tmp_path / "outer"
    _write(outer / ".gitignore", "scratch/*\n")
    _git(outer, "init", "-q")
    root = outer / "scratch" / "clone"
    _write(root / "a.py")
    _write(root / "src" / "b.py")
    assert _files(root) == {"a.py", "src/b.py"}


def test_git_ignored_declines_rather_than_answering_for_an_ignored_root(tmp_path: Path) -> None:
    """D3 at the helper: an ignored root, or one outside git, gets ``None`` -- "git cannot say" -- not a
    listing. From inside an ignored root git reports the root itself (``./``) as ignored, and one path
    normalisation away that skips every file."""
    outer = tmp_path / "outer"
    _write(outer / ".gitignore", "scratch/*\n")
    _git(outer, "init", "-q")
    root = outer / "scratch" / "clone"
    _write(root / "a.py")
    assert git_ignored(root) is None
    (tmp_path / "elsewhere").mkdir()
    assert git_ignored(tmp_path / "elsewhere") is None
    from_outer = git_ignored(outer)
    assert isinstance(from_outer, IgnoredPaths)
    assert from_outer.covers("scratch/clone/a.py", is_dir=False)
    assert not from_outer.covers(".gitignore", is_dir=False)


def test_a_root_below_the_toplevel_reads_paths_relative_to_itself(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / ".gitignore", "pkg/out/\n")
    _write(repo / "pkg" / "src" / "a.py")
    _write(repo / "pkg" / "out" / "a.py")
    _git(repo, "init", "-q")
    assert _files(repo / "pkg") == {"src/a.py"}


def test_skips_are_counted(tmp_path: Path) -> None:
    """D4: an omission is only honest if it is visible. One entry per pruned directory or skipped
    file, keyed by its top-level directory, and reset on every walk."""
    root = _repo(tmp_path, ignore="out/\n*_generated.py\n")
    _write(root / "src" / "x_generated.py")
    _write(root / "src" / "y_generated.py")
    extractor = RepoCodeExtractor()
    for _ in range(2):
        extractor.extract(root)
        assert extractor.gitignored_skipped == {"out": 1, "src": 2}


def test_pkg_extract_names_what_git_ignored(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    for i in range(6):
        _write(root / f"gen{i}" / "a.py")
    with (root / ".gitignore").open("a", encoding="utf-8") as fh:
        fh.write("gen*/\n")
    result = CliRunner().invoke(app, ["pkg", "extract", str(root)])
    assert result.exit_code == 0, result.output
    assert (
        "gitignored, not walked: gen0 (1), gen1 (1), gen2 (1), gen3 (1), gen4 (1), and 2 more"
        in result.output
    )


def test_pkg_extract_says_nothing_when_git_ignored_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path, ignore="")
    result = CliRunner().invoke(app, ["pkg", "extract", str(root)])
    assert result.exit_code == 0, result.output
    assert "gitignored" not in result.output


def _ignored_by_git(root: Path) -> set[str]:
    """Every untracked, ignored *file* under ``root``, expanded (no ``--directory``)."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-o", "-i", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    ).stdout.decode()
    return {p for p in out.split("\0") if p}


@pytest.mark.parametrize("case", ["python/plain", "python/src_package", "go/plain"])
def test_the_rule_only_ever_removes_what_git_ignores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """The refuse-only property, on copies of real corpus fixtures with a planted build tree: the
    walk with git is a subset of the walk without it, and the difference is exactly the ignored
    files. The walk without git is the name rules alone -- develop's walk."""
    root = tmp_path / "repo"
    shutil.copytree(_CORPUS / case / ".repo", root)
    _write(root / "out" / "gen.py")
    _write(root / "coverage" / "lcov" / "index.md", "# Coverage\n")
    with (root / ".gitignore").open("a", encoding="utf-8") as fh:
        fh.write("\nout/\ncoverage/\n")
    _git(root, "init", "-q")
    with_git = _walked(root)
    with monkeypatch.context() as m:
        _no_git(m, tmp_path)
        without_git = _walked(root)
    assert with_git <= without_git
    assert without_git - with_git == _ignored_by_git(root) & without_git
    assert without_git - with_git >= {"out/gen.py", "coverage/lcov/index.md"}


def test_the_same_commit_gives_the_same_graph_with_or_without_a_build(tmp_path: Path) -> None:
    """Invariant 8: a clean tree at a SHA means the facts are the commit's. A gitignored build leaves
    the tree clean, so it must leave the graph alone too."""
    root = tmp_path / "repo"
    _write(root / "src" / "a.py")
    _write(root / ".gitignore", "out/\n")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    before = {n.id for n in load_or_extract(root, cache_dir=tmp_path / "cache-a").nodes}
    _write(root / "out" / "src" / "a.py")
    after = {n.id for n in load_or_extract(root, cache_dir=tmp_path / "cache-b").nodes}
    assert after == before


def test_a_git_failure_falls_back_to_the_name_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `git` binary inside a git work tree: the walk is the name rules' walk, never an empty
    graph and never a guess (D3)."""
    root = _repo(tmp_path)
    _no_git(monkeypatch, tmp_path)
    assert _files(root) == {"src/a.py", "out/src/a.py"}
