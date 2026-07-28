"""The trust layer: the provenance stamp, and `understand --check`.

Finding 7 of the understand enhancement spec — a committed knowledge base whose
whole value is being code-true could not tell a reader whether it described HEAD
or a commit from six months ago. These pin the two halves of the fix: the stamp
says where the bank came from, and `--check` proves whether it still holds.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.knowledge.renderers import STAMP_CLOSE, STAMP_OPEN, render_stamp, strip_stamp
from orchestrator.knowledge.understand import (
    build_memory_bank,
    check_memory_bank,
    render_memory_bank,
    spine_version,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path) -> Path:
    """A committed git repo with a little package to describe."""
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        "from .util import helper\n\n\nclass Widget:\n    def run(self) -> int:\n        return helper()\n",
        encoding="utf-8",
    )
    (pkg / "util.py").write_text("def helper() -> int:\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _build_and_commit(repo: Path) -> None:
    build_memory_bank(repo, refresh=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "episteme")


# ---- the stamp --------------------------------------------------------------


def test_stamp_records_commit_and_version(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    build_memory_bank(repo, refresh=True)
    readme = (repo / "episteme" / "README.md").read_text(encoding="utf-8")

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head in readme
    assert spine_version() in readme
    assert STAMP_OPEN in readme and STAMP_CLOSE in readme


def test_stamp_carries_no_timestamp_so_output_stays_byte_identical(tmp_path: Path) -> None:
    """Invariant #2: same code in → same output out. A date would break it."""
    repo = _repo(tmp_path)
    first = render_memory_bank(repo, refresh=True).files
    second = render_memory_bank(repo, refresh=True).files
    assert first == second


def test_stamp_says_so_when_there_is_no_commit(tmp_path: Path) -> None:
    """Outside git there is nothing to cite — say that, don't imply currency."""
    (tmp_path / "app.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    build_memory_bank(tmp_path, refresh=True)
    readme = (tmp_path / "episteme" / "README.md").read_text(encoding="utf-8")
    assert "isn't a git repository" in readme


def test_dirty_tree_is_marked_in_the_stamp(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src" / "app" / "util.py").write_text("def helper() -> int:\n    return 2\n", encoding="utf-8")
    build_memory_bank(repo, refresh=True)
    readme = (repo / "episteme" / "README.md").read_text(encoding="utf-8")
    assert "plus uncommitted changes" in readme


def test_strip_stamp_removes_the_block_and_nothing_else() -> None:
    stamp = render_stamp(commit="a" * 40, dirty=False, version="9.9.9")
    text = f"before\n\n{stamp}\n\nafter\n"
    stripped = strip_stamp(text)
    assert "a" * 40 not in stripped
    assert "before" in stripped and "after" in stripped
    assert strip_stamp("no stamp here\n") == "no stamp here\n"


# ---- --check ----------------------------------------------------------------


def test_check_passes_on_a_freshly_committed_bank(tmp_path: Path) -> None:
    """The self-reference case: committing the bank moves HEAD past its own stamp."""
    repo = _repo(tmp_path)
    _build_and_commit(repo)
    report = check_memory_bank(repo)
    assert report.ok, report.summary_line()
    assert "current" in report.summary_line()


def test_check_ignores_a_stamp_naming_an_older_commit(tmp_path: Path) -> None:
    """Content proves currency; the stamp is for the reader and must not fail CI."""
    repo = _repo(tmp_path)
    _build_and_commit(repo)
    readme = repo / "episteme" / "README.md"
    before, _, rest = readme.read_text(encoding="utf-8").partition(STAMP_OPEN)
    _, _, after = rest.partition(STAMP_CLOSE)
    stale_stamp = render_stamp(commit="f" * 40, dirty=False, version="0.0.1")
    readme.write_text(before + stale_stamp + after, encoding="utf-8")

    assert check_memory_bank(repo).ok


def test_check_fails_when_the_code_changed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _build_and_commit(repo)
    (repo / "src" / "app" / "util.py").write_text(
        "def helper() -> int:\n    return 1\n\n\ndef added() -> int:\n    return 2\n", encoding="utf-8"
    )
    report = check_memory_bank(repo)
    assert not report.ok
    assert "modules/app.util.md" in report.stale
    assert "stale" in report.summary_line()


def test_check_fails_on_a_hand_edited_page(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _build_and_commit(repo)
    glossary = repo / "episteme" / "glossary.md"
    glossary.write_text(glossary.read_text(encoding="utf-8") + "\nhand written\n", encoding="utf-8")
    report = check_memory_bank(repo)
    assert report.stale == ("glossary.md",)


def test_check_fails_on_a_missing_page(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _build_and_commit(repo)
    (repo / "episteme" / "glossary.md").unlink()
    report = check_memory_bank(repo)
    assert report.missing == ("glossary.md",)


def test_check_fails_on_a_page_describing_deleted_code(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _build_and_commit(repo)
    (repo / "episteme" / "modules" / "app.gone.md").write_text("# gone\n", encoding="utf-8")
    report = check_memory_bank(repo)
    assert report.orphaned == ("modules/app.gone.md",)


def test_check_reports_an_absent_bank_distinctly(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = check_memory_bank(repo)
    assert not report.ok
    assert report.absent
    assert "No knowledge base" in report.summary_line()


def test_check_writes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _build_and_commit(repo)
    before = {p: p.read_bytes() for p in (repo / "episteme").rglob("*.md")}
    check_memory_bank(repo)
    after = {p: p.read_bytes() for p in (repo / "episteme").rglob("*.md")}
    assert before == after


# ---- the fixed point --------------------------------------------------------


def test_the_bank_is_not_ingested_as_its_own_documentation(tmp_path: Path) -> None:
    """Generated output is not source. Reading it back would inflate every count —
    and writing the bank would change the graph that renders it, so no bank could
    ever describe its own repo consistently."""
    repo = _repo(tmp_path)
    before = render_memory_bank(repo, refresh=True).summary
    build_memory_bank(repo, refresh=True)
    after = render_memory_bank(repo, refresh=True).summary
    assert before == after


def test_build_is_idempotent_so_check_holds_after_a_rebuild(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _build_and_commit(repo)
    build_memory_bank(repo, refresh=True)  # a second build must change nothing
    assert check_memory_bank(repo).ok
