"""One analysis layer, two renderings — Phase 2 of the understand enhancement spec.

`state` computed sixteen sections while the committed episteme rendered four, so the
*ephemeral* report was richer than the *committed* knowledge base. These pin the fix:
both surfaces now read one analysis, and the sections `state` reports reach the pages a
team actually commits — without dragging in the ones that can't survive being committed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.knowledge.analysis import analyse
from orchestrator.knowledge.current_state import is_test_area, render_current_state
from orchestrator.knowledge.understand import build_memory_bank, check_memory_bank, render_memory_bank


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A package with a real test importing it, plus a manifest and an entry point."""
    pkg = tmp_path / "src" / "shop"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        "import json\nimport os\n\nfrom .tax import rate\n\n\n"
        "class Order:\n    def total(self) -> int:\n        return rate()\n\n\n"
        "def main() -> int:\n    return 0\n",
        encoding="utf-8",
    )
    (pkg / "tax.py").write_text("def rate() -> int:\n    return 7\n", encoding="utf-8")
    (pkg / "untested.py").write_text("class Forgotten:\n    pass\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        "from shop.core import Order\n\n\ndef test_total() -> None:\n    assert Order()\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "shop"\nversion = "1.0"\n\n[project.scripts]\nshop = "shop.core:main"\n',
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _bank(repo: Path) -> dict[str, str]:
    return render_memory_bank(repo, refresh=True).files


# ---- one analysis layer -----------------------------------------------------


def test_both_surfaces_read_the_same_analysis(repo: Path) -> None:
    """The printed report and the committed bank must describe one graph."""
    analysis = analyse(repo, refresh=True)
    printed = render_current_state(analysis.state)
    committed = _bank(repo)["architecture.md"]

    # the same coupling drives the architecture in both
    assert analysis.state.coupling
    for (src, dst), _ in analysis.state.coupling.most_common(3):
        assert f"`{src}` | → | `{dst}`" in printed
        assert f"`{src}` | → | `{dst}`" in committed


# ---- the sections episteme was missing --------------------------------------


def test_architecture_carries_diagram_layers_and_coverage(repo: Path) -> None:
    arch = _bank(repo)["architecture.md"]
    assert "## System architecture" in arch
    assert "```mermaid" in arch
    assert "### Strongest component dependencies" in arch
    assert "## Layers" in arch
    assert "## Test coverage" in arch


def test_tech_context_carries_entry_points_and_dependencies(repo: Path) -> None:
    tech = _bank(repo)["tech-context.md"]
    assert "## Entry points" in tech
    assert "shop.core:main" in tech  # the declared console script
    assert "## Most-used external imports" in tech


def test_progress_carries_recommendations_not_just_a_backlog_pointer(repo: Path) -> None:
    progress = _bank(repo)["progress.md"]
    assert "## Feature tracking" in progress
    analysis = analyse(repo, refresh=True)
    if analysis.state.recommendations:
        assert "## Suggested next steps" in progress


# ---- coverage means what it says --------------------------------------------


def test_coverage_counts_components_a_test_imports(repo: Path) -> None:
    """`shop.core` has a test importing it; `shop.untested` does not."""
    state = analyse(repo, refresh=True).state
    untested = {a for a, _ in state.untested_top}
    assert "src.shop" not in untested or state.tested_areas > 0
    assert state.production_areas >= 1
    # tests are not counted as things needing tests
    assert not any(is_test_area(a) for a, _ in state.untested_top)


def test_entry_points_exclude_tests(repo: Path) -> None:
    """A `main()` inside a test is a fixture, not how the system starts."""
    (repo / "tests" / "test_cli.py").write_text("def main() -> int:\n    return 1\n", encoding="utf-8")
    state = analyse(repo, refresh=True).state
    assert not any("tests/" in e for e in state.entry_points)


# ---- what must NOT be committed ---------------------------------------------


def test_git_churn_stays_out_of_the_committed_bank(repo: Path) -> None:
    """`state`'s "Recent activity" reads the last ~60 commits, so its value moves on
    every commit — including the commit that lands the bank. Rendering it would make
    episteme stale the moment it was committed and `--check` fail forever after."""
    state = analyse(repo, refresh=True).state
    printed = render_current_state(state)
    bank = _bank(repo)

    if state.recent_areas:  # only meaningful once there's history to churn
        assert "Recent activity" in printed
    assert not any("Recent activity" in page for page in bank.values())


def test_check_still_passes_after_committing_the_bank(repo: Path) -> None:
    """The end-to-end property Phase 1 bought and Phase 2 must not spend: the richer
    bank still has to survive being committed."""
    build_memory_bank(repo, refresh=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "episteme")

    assert check_memory_bank(repo).ok

    # …and after a commit that touches neither code nor docs. Nothing in the bank may
    # track HEAD itself. (A markdown file *would* legitimately invalidate it — docs are
    # graph input, so adding one changes what the bank describes.)
    (repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore")
    assert check_memory_bank(repo).ok
