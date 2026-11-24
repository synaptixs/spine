"""One git ignore listing for many walks under the same base (B150, G6 D6).

``walk_repo`` asks git what it ignores each time it is called, 2-3 processes per walk. Three callers
walk once per candidate in a loop: ``layout.choose_project`` counted source in each of 40 projects
with 120 git calls (1.44 s), ``conventions._top_package`` made 123 for 40 children, and
``layout._sam_layout`` 30 for 10 functions (all measured in G6 P0). The fix computes the listing
once at the loop's base and hands each walk its slice, re-rooted by :meth:`IgnoredPaths.below`.

The property that makes that safe is tested directly: for every directory under a repository with
nested ``.gitignore`` files, negations, a force-added file, a decomposed name, an ignored project and
a nested checkout, the walk with the shared slice equals the walk that asked git itself.
"""

from __future__ import annotations

import os
import subprocess
import unicodedata
from pathlib import Path

import pytest

from orchestrator.pkg import walk_rules
from orchestrator.pkg.walk_rules import IgnoredPaths, walk_repo


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


def _write(path: Path, text: str = "x = 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")


class _Calls:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.n = 0
        real = walk_rules.run_git

        def counting(
            root: Path | str, *args: str, timeout: float | None = None
        ) -> subprocess.CompletedProcess[bytes]:
            self.n += 1
            return real(root, *args, timeout=timeout)

        monkeypatch.setattr(walk_rules, "run_git", counting)


# ---- IgnoredPaths.below ---------------------------------------------------------------------------


def test_below_rebases_onto_the_subdirectory() -> None:
    paths = IgnoredPaths(frozenset({"a/bin", "a/b/out", "c/bin"}), frozenset({"a/x.log", "c/y.log"}))
    assert paths.below("a") == IgnoredPaths(frozenset({"bin", "b/out"}), frozenset({"x.log"}))
    assert paths.below("a/b") == IgnoredPaths(frozenset({"out"}), frozenset())


def test_below_the_root_is_the_same_set() -> None:
    paths = IgnoredPaths(frozenset({"bin"}), frozenset({"x.log"}))
    assert paths.below("") == paths
    assert paths.below(".") == paths


def test_below_an_ignored_directory_switches_the_rule_off_as_git_would() -> None:
    """``git_ignore_rule`` answers ``None`` for a root that is itself ignored (D3): the person pointed
    at it on purpose. A slice for such a directory must say the same, not "everything is ignored"."""
    paths = IgnoredPaths(frozenset({"vendor"}), frozenset())
    assert paths.below("vendor") is None
    assert paths.below("vendor/lib") is None


def test_below_compares_in_nfc() -> None:
    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    paths = IgnoredPaths(frozenset({f"{nfc}/out"}), frozenset())
    assert paths.below(nfd) == IgnoredPaths(frozenset({"out"}), frozenset())


# ---- the property: a shared slice walks exactly like a fresh git call ----------------------------


@pytest.fixture
def busy_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(root / ".gitignore", "bin/\nout/\n*.log\nignored_proj/\n")
    for proj in ("App", "Lib", unicodedata.normalize("NFD", "Café")):
        _write(root / proj / "src" / "main.py")
        _write(root / proj / "bin" / "built.py")
        _write(root / proj / "out" / "gen.py")
        _write(root / proj / "run.log", "log\n")
    _write(root / "Lib" / ".gitignore", "gen/\n!keep.log\n")
    _write(root / "Lib" / "gen" / "made.py")
    _write(root / "Lib" / "keep.log", "kept\n")
    _write(root / "App" / "out" / "forced.py")
    _write(root / "ignored_proj" / "x.py")
    _git(root, "init", "-q")
    _commit(root)
    _git(root, "add", "-f", "App/out/forced.py")
    _commit(root)
    nested = root / "Nested"
    _write(nested / ".gitignore", "generated/\ngen2/\n")  # not name rules: only git can skip them
    _write(nested / "pkg" / "a.py")
    _write(nested / "pkg" / "gen2" / "c.py")  # below a nested checkout's own sub-folder
    _write(nested / "generated" / "b.py")
    _git(nested, "init", "-q")
    _commit(nested)
    # The PR-1 review's counterexamples: an untracked folder holding only ignored files (git's
    # --directory lists the folder whole, though the folder itself is not ignored) and a
    # symlinked folder whose target holds a gitignored tree.
    _write(root / "fresh" / "inner" / "a.log", "x\n")
    _write(root / "fresh" / "inner" / "b.log", "y\n")
    _write(root / "tools" / "gen" / "made.py")
    _write(root / ".gitignore", "bin/\nout/\n*.log\nignored_proj/\ntools/gen/\n")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "alink").symlink_to(root / "tools", target_is_directory=True)
    return root


def _walk(directory: Path, **kw: object) -> list[tuple[str, list[str], list[str]]]:
    return [(h.relative_to(directory).as_posix(), list(d), list(f)) for h, d, f in walk_repo(directory, **kw)]  # type: ignore[arg-type]


def test_a_shared_slice_walks_like_a_fresh_git_call(busy_repo: Path) -> None:
    rule = walk_rules.SharedIgnoreRule(busy_repo)
    dirs = [busy_repo] + sorted(
        p for p in busy_repo.rglob("*") if p.is_dir() and ".git" not in p.relative_to(busy_repo).parts
    )
    assert len(dirs) > 15
    for directory in dirs:
        assert _walk(directory, ignored=rule.for_dir(directory)) == _walk(directory), directory


def test_a_differently_spelled_path_asks_git_itself(busy_repo: Path) -> None:
    """On a case-insensitive filesystem `App` and `app` are one folder, but git's listing is spelled
    one way. A path spelled another way gets the walk's own git call, never a mismatched slice."""
    spelled = busy_repo / "APP"
    if not spelled.exists():
        pytest.skip("case-sensitive filesystem")
    rule = walk_rules.SharedIgnoreRule(busy_repo)
    assert _walk(spelled, ignored=rule.for_dir(spelled)) == _walk(spelled)


def test_a_base_git_cannot_list_leaves_each_walk_to_say_so(
    busy_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When git cannot list the base, each walk asks for itself and records why (`gitignore_off`)."""
    monkeypatch.setattr(walk_rules, "git_ignore_rule", lambda root: (None, "git failed: test"))
    rule = walk_rules.SharedIgnoreRule(busy_repo)
    health = walk_rules.WalkHealth()
    list(walk_repo(busy_repo / "App", ignored=rule.for_dir(busy_repo / "App"), health=health))
    assert health.gitignore_off == "git failed: test"


def test_a_rule_is_computed_once_and_only_when_asked(
    busy_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _Calls(monkeypatch)
    rule = walk_rules.SharedIgnoreRule(busy_repo)
    assert calls.n == 0
    for proj in ("App", "Lib"):
        list(walk_repo(busy_repo / proj, ignored=rule.for_dir(busy_repo / proj)))
    assert calls.n == 2  # rev-parse at the top level (no check-ignore needed), then ls-files


# ---- the three loop sites ------------------------------------------------------------------------


def test_choose_project_asks_git_once_for_forty_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc.layout import choose_project

    root = tmp_path / "sln"
    cands = []
    for i in range(40):
        proj = root / f"Proj{i:02d}"
        _write(proj / f"Proj{i:02d}.csproj", "<Project/>")
        for j in range(i % 4 + 1):
            _write(proj / f"C{j}.cs", "class C {}")
        cands.append(proj / f"Proj{i:02d}.csproj")
    _write(root / ".gitignore", "generated/\n")  # not a name rule: only git's listing skips it
    for k in range(6):
        _write(root / "Proj39" / "generated" / f"Gen{k}.cs", "class G {}")
    _git(root, "init", "-q")
    _commit(root)
    calls = _Calls(monkeypatch)
    winner = choose_project(cands, root=root, language="csharp")
    assert winner == root / "Proj03" / "Proj03.csproj"  # 4 files; Proj39's gitignored generated/ never votes
    assert calls.n <= 3, calls.n


def test_top_package_asks_git_once_for_forty_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc.conventions import _top_package

    base = tmp_path / "repo"
    for i in range(40):
        _write(base / "src" / f"c{i:02d}" / "README", "x")
    _write(base / "src" / "c39" / "m.py")
    _git(base, "init", "-q")
    _commit(base)
    calls = _Calls(monkeypatch)
    assert _top_package(base) == "c39"
    assert calls.n <= 3, calls.n


def test_sam_layout_asks_git_once_for_ten_functions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.layout import _sam_layout

    root = tmp_path / "sam"
    template = ["Resources:"]
    for i in range(10):
        for j in range(i + 1):
            _write(root / f"fn{i}" / f"m{j}.py")
        template += [
            f"  Fn{i}:",
            "    Type: AWS::Serverless::Function",
            "    Properties:",
            f"      CodeUri: fn{i}/",
        ]
    _write(root / "template.yaml", "\n".join(template) + "\n")
    _git(root, "init", "-q")
    _commit(root)
    calls = _Calls(monkeypatch)
    layout = _sam_layout(root, ())
    assert layout is not None and layout.package_name == "fn9"
    assert calls.n <= 3, calls.n
