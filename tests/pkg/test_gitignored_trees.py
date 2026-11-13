"""B119: the pkg walkers skip what git ignores and does not track.

They skipped build output by *name* only (`build`, `dist`, `target`, ...). A TypeScript repo compiles
into a gitignored `out/`, which is not one of those names, so the compiled `.js` entered the graph as
source: measured on one extension repo at one commit, 581 nodes before `tsc` and 895 after, with
`blast_radius` listing the compiled copy first and `regression_gaps` answering "no test exercises it"
about a file git has never seen. A gitignored tree also leaves `git status` clean, so the commit-keyed
cache froze whichever graph came first (invariant 8).

The rule is refuse-only: with git, the walked set is a subset of the walk without it, and every file in
the difference is one `git ls-files -o -i --exclude-standard` reports, or a link whose *target* is one.
Tracked files inside an ignored directory stay, untracked files that are not ignored stay, and when git
cannot answer -- not a work tree, no `git` binary, or the root itself ignored -- the walk is exactly the
name rules' walk, and that walk is never cached: it holds the build tree a clean `git status` hides.

The fixtures make no commits (except the cache test), so CI's missing git identity cannot bite, and
every test runs with the developer's git configuration and any inherited repository variables shut out
(:func:`_isolated_git`): a global excludes file naming ``out/`` would otherwise pass a test on a laptop
that CI fails, or the reverse.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unicodedata
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.catalog.profile import language_file_counts
from orchestrator.cli import app
from orchestrator.pkg import walk_rules
from orchestrator.pkg.doc_source import read_doc_pages
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.persistence import load_or_extract, load_with_unbound
from orchestrator.pkg.walk_rules import IgnoredPaths, git_ignore_rule, git_ignored

_CORPUS = Path(__file__).resolve().parents[2] / "corpus"

#: What git itself calls "repository-local" variables (``git rev-parse --local-env-vars``), which a
#: git hook exports and which would make every git call below answer for another repository.
_REPO_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    # not repository-local, but configuration the developer's shell may carry into every git call
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No global or system git config (excludes file, hooks path, signing) and no inherited repo."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    for name in _REPO_ENV:
        monkeypatch.delenv(name, raising=False)


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


def _docs(root: Path) -> set[str]:
    return {p.source_file or p.title for p in read_doc_pages(root, sections=False)}


@pytest.mark.parametrize(
    ("walker", "expected"),
    [
        (_files, {"src/a.py", "output/b.py"}),
        (_docs, {"README.md", "output/NOTES.md"}),
        (language_file_counts, {"python": 2}),
    ],
    ids=["code", "docs", "language"],
)
def test_the_three_walkers_agree_on_ignored_trees(
    tmp_path: Path, walker: Callable[[Path], object], expected: object
) -> None:
    """Each walker's whole answer, not a membership check: a walker that skips too much fails here as
    surely as one that skips too little. ``output/`` sits beside the ignored ``out/`` and shares its
    first three letters, so a match that ignores path-segment boundaries loses it."""
    root = _repo(tmp_path)
    _write(root / "README.md", "# Repo\n")
    _write(root / "output" / "b.py")
    _write(root / "output" / "NOTES.md", "# Notes\n")
    assert walker(root) == expected


def test_a_git_hook_environment_does_not_answer_for_another_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A git hook exports ``GIT_DIR`` and ``GIT_INDEX_FILE``, and ``git -C root`` obeys them over
    ``-C``: run from a pre-commit hook in another repository, git read that repository's index
    against this tree and reported a tracked file as ignored."""
    other = tmp_path / "other"
    _write(other / "x.py")
    _git(other, "init", "-q")
    root = _repo(tmp_path)
    _write(root / "out" / "keep.py")
    _git(root, "add", "-f", "out/keep.py")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    assert "out/keep.py" in _files(root)
    assert "out/src/a.py" not in _files(root)


def test_a_whitelist_gitignore_still_skips_what_it_ignores(tmp_path: Path) -> None:
    """``*`` followed by ``!`` exceptions is a common way to write a ``.gitignore``. With it, ``git
    check-ignore .`` at the top level says the top level itself is ignored -- which a work tree's root
    never is -- and the rule switched itself off, putting the build tree back."""
    root = _repo(tmp_path, ignore="*\n!.gitignore\n!src/\n!src/**\n")
    assert _files(root) == {"src/a.py"}
    assert _docs(root) == set()


def test_a_link_into_an_ignored_tree_is_refused(tmp_path: Path) -> None:
    """A link is admitted under its own name only if its target is somewhere the walk would go
    (:func:`symlink_admitted`). After a build, a link into ``out/`` brings the compiled copy back."""
    root = _repo(tmp_path)
    (root / "src" / "compiled.py").symlink_to(Path("..") / "out" / "src" / "a.py")
    (root / "NOTES.md").symlink_to(Path("out") / "README.md")
    assert _files(root) == {"src/a.py"}
    assert _docs(root) == set()
    assert language_file_counts(root) == {"python": 1}


def test_a_link_to_a_walked_file_is_still_admitted(tmp_path: Path) -> None:
    """The positive control for the refusal above: with git answering, a link whose target the walk
    would reach is admitted by all three walkers, so the refusal is about ignored targets only."""
    root = _repo(tmp_path)
    _write(root / "README.md", "# Repo\n")
    (root / "src" / "alias.py").symlink_to("a.py")
    (root / "ALIAS.md").symlink_to("README.md")
    assert _files(root) == {"src/a.py", "src/alias.py"}
    assert _docs(root) == {"README.md", "ALIAS.md"}
    assert language_file_counts(root) == {"python": 2}


@pytest.mark.parametrize("precompose", ["true", "false"])
def test_a_decomposed_name_matches_gits_composed_one(tmp_path: Path, precompose: str) -> None:
    """macOS git reports ``café/`` composed (NFC, ``core.precomposeunicode``) while the filesystem
    hands the walk the name as it was written, here decomposed (NFD). Compared byte for byte the
    two never match and the ignored tree is walked. With the setting off -- as on Linux, where git
    reports the bytes it found -- git's side is the decomposed one, so both sides are normalised."""
    root = _repo(tmp_path, ignore="out/\ncaf*/\n")
    _git(root, "config", "core.precomposeunicode", precompose)
    _write(root / unicodedata.normalize("NFD", "café") / "x.py")
    assert _files(root) == {"src/a.py"}


@pytest.mark.parametrize(
    ("walker", "expected"),
    [(_files, {"src/a.py"}), (_docs, {"README.md"}), (language_file_counts, {"python": 1})],
    ids=["code", "docs", "language"],
)
def test_an_ignored_file_outside_an_ignored_dir_is_skipped(
    tmp_path: Path, walker: Callable[[Path], object], expected: object
) -> None:
    """A file pattern, not a directory: each walker checks the file itself, not only its folders."""
    root = _repo(tmp_path, ignore="out/\n*_generated.*\n")
    _write(root / "README.md", "# Repo\n")
    _write(root / "src" / "schema_generated.py")
    _write(root / "NOTES_generated.md", "# Generated\n")
    assert walker(root) == expected


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
    """Most first, then by name; five shown and the rest counted."""
    root = _repo(tmp_path, ignore="out/\ngen*/\n*_generated.py\n")
    for i in range(6):
        _write(root / f"gen{i}" / "a.py")
    _write(root / "src" / "x_generated.py")
    _write(root / "src" / "y_generated.py")
    result = CliRunner().invoke(app, ["pkg", "extract", str(root)])
    assert result.exit_code == 0, result.output
    assert (
        "gitignored, not walked: src (2), gen0 (1), gen1 (1), gen2 (1), gen3 (1), and 3 more" in result.output
    )


def _failing_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A ``git`` that refuses the repository, as git does for one owned by another user."""
    bin_dir = tmp_path / "failing-git-bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "git"
    fake.write_text('#!/bin/sh\necho "fatal: detected dubious ownership in repository" >&2\nexit 128\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def test_a_rule_git_could_not_apply_is_reported_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside a work tree, a git that fails leaves the walk at the name rules -- the build tree back in
    the graph -- and that must not look the same as "git ignored nothing"."""
    root = _repo(tmp_path)
    _failing_git(monkeypatch, tmp_path)
    assert git_ignore_rule(root) == (None, "fatal: detected dubious ownership in repository")
    result = CliRunner().invoke(app, ["pkg", "extract", str(root)])
    assert result.exit_code == 0, result.output
    assert "git could not list what it ignores, so it was walked: fatal: detected dubious" in result.output


@pytest.mark.parametrize(
    ("failing", "prefix", "walk_root"),
    [("ls-files", "", "."), ("check-ignore", "src/", "src")],
    ids=["listing", "root-probe"],
)
def test_a_git_that_fails_after_it_found_the_work_tree_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str, prefix: str, walk_root: str
) -> None:
    """``rev-parse`` answers, then the call that matters fails: that is not an empty listing."""
    root = _repo(tmp_path)
    bin_dir = tmp_path / "half-git-bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(
        "#!/bin/sh\n"
        f'case "$3" in\n  rev-parse) echo "{prefix}"; exit 0;;\n'
        f'  {failing}) echo "fatal: {failing} broke" >&2; exit 128;;\nesac\nexit 1\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    assert git_ignore_rule(root / walk_root) == (None, f"fatal: {failing} broke")


def test_a_git_that_hangs_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    bin_dir = tmp_path / "slow-git-bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text("#!/bin/sh\nsleep 5\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(walk_rules, "_GIT_TIMEOUT_S", 0.2)
    assert git_ignore_rule(root) == (None, "git failed: TimeoutExpired")


def test_no_git_inside_a_work_tree_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    _no_git(monkeypatch, tmp_path)
    paths, reason = git_ignore_rule(root / "src")
    assert paths is None
    assert reason is not None and "git" in reason


def test_outside_git_or_in_an_ignored_root_the_rule_is_off_quietly(tmp_path: Path) -> None:
    """By design (D3), not a failure: nothing to report."""
    (tmp_path / "elsewhere").mkdir()
    assert git_ignore_rule(tmp_path / "elsewhere") == (None, None)
    outer = tmp_path / "outer"
    _write(outer / ".gitignore", "scratch/*\n")
    _git(outer, "init", "-q")
    _write(outer / "scratch" / "clone" / "a.py")
    assert git_ignore_rule(outer / "scratch" / "clone") == (None, None)
    result = CliRunner().invoke(app, ["pkg", "extract", str(tmp_path / "elsewhere")])
    assert "git could not" not in result.output


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
    ignored = _ignored_by_git(root)

    def walks() -> tuple[set[str], set[str], dict[str, int]]:
        return _walked(root), _docs(root), language_file_counts(root)

    with_git, docs_with, counts_with = walks()
    with monkeypatch.context() as m:
        _no_git(m, tmp_path)
        without_git, docs_without, counts_without = walks()
    assert with_git <= without_git
    assert without_git - with_git == ignored & without_git
    assert without_git - with_git >= {"out/gen.py", "coverage/lcov/index.md"}
    assert docs_with <= docs_without
    assert docs_without - docs_with == ignored & docs_without
    assert "coverage/lcov/index.md" in docs_without - docs_with
    # the one planted source file is the only vote that moves
    assert {
        k: v - counts_with.get(k, 0) for k, v in counts_without.items() if v != counts_with.get(k, 0)
    } == {"python": 1}


def _committed(tmp_path: Path, ignore: str = "out/\n") -> Path:
    """`src/a.py` and `.gitignore`, committed: a clean tree, so the commit-keyed cache is trusted."""
    root = tmp_path / "repo"
    _write(root / "src" / "a.py")
    _write(root / ".gitignore", ignore)
    _git(root, "init", "-q")
    ident = ("-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false")
    _git(root, *ident, "add", "-A")
    _git(root, *ident, "commit", "--no-verify", "-qm", "init")
    return root


def _graph_files(root: Path, cache: Path) -> set[str]:
    return {n.provenance.file for n in load_or_extract(root, cache_dir=cache).nodes if n.provenance}


def test_a_walk_git_could_not_filter_is_never_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the rule off the walk holds the build tree, and a gitignored tree leaves the tree clean:
    cached, that graph would be served at this commit after git recovers -- B119 by another route."""
    root = _committed(tmp_path)
    _write(root / "out" / "src" / "a.py")
    cache = tmp_path / "cache"
    with monkeypatch.context() as m:
        # The walk asks `walk_rules` itself since the one walker moved there (KG P1, `walk_repo`).
        m.setattr(walk_rules, "git_ignore_rule", lambda _root: (None, "git failed: test"))
        assert _graph_files(root, cache) == {"src/a.py", "out/src/a.py"}
        load_with_unbound(root, cache_dir=cache)  # the untraced-calls list beside it, likewise
    assert not list(cache.glob("**/*.json"))
    assert _graph_files(root, cache) == {"src/a.py"}
    assert list(cache.glob("**/*.json"))  # the control: an answered rule on a clean tree is cached


def test_configuration_passed_by_environment_reaches_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GIT_CONFIG_COUNT`` is how a container hands git ``safe.directory`` or an excludes file. The
    cache's own git call sees it; if the walk's did not, the two disagree about what is ignored --
    here the cache calls the tree clean while the walk reads ``out/`` -- and the cache keeps it."""
    excludes = tmp_path / "excludes"
    excludes.write_text("out/\n", encoding="utf-8")
    root = _committed(tmp_path, ignore="")
    _write(root / "out" / "src" / "a.py")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.excludesFile")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(excludes))
    assert git_ignore_rule(root)[1] is None
    assert _graph_files(root, tmp_path / "cache") == {"src/a.py"}


def test_the_same_commit_gives_the_same_graph_with_or_without_a_build(tmp_path: Path) -> None:
    """Invariant 8: a clean tree at a SHA means the facts are the commit's. A gitignored build leaves
    the tree clean, so it must leave the graph alone too."""
    root = _committed(tmp_path)
    before = load_or_extract(root, cache_dir=tmp_path / "cache-a").nodes
    _write(root / "out" / "src" / "a.py")
    after = load_or_extract(root, cache_dir=tmp_path / "cache-b").nodes
    assert {n.provenance.file for n in before if n.provenance} == {"src/a.py"}
    assert {n.id for n in after} == {n.id for n in before}


def test_a_git_failure_falls_back_to_the_name_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `git` binary inside a git work tree: the walk is the name rules' walk, never an empty
    graph and never a guess (D3)."""
    root = _repo(tmp_path)
    _no_git(monkeypatch, tmp_path)
    assert _files(root) == {"src/a.py", "out/src/a.py"}
