"""The G6 gold set: what a label must prove before it can score anything.

The labels are the answer key, and an answer key is only worth having if a reader can check it
and if it did not come from the system under test. Both are enforced at load, because the point
where a bad label becomes a wrong number is much too late.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.evals.labels import (
    GoldSet,
    Label,
    LabelError,
    load_labels,
    unresolvable_paths,
)
from orchestrator.evals.localization import LocalizationReport, LocalizationResult, score_localization

GOOD = """
version: 1
labels:
  - repo: flask
    issue: https://github.com/pallets/flask/issues/1
    title: "send_file sets the wrong mimetype"
    fix_commit: d318b683471101618febed18996405ad26462110
    fix_sites:
      - path: src/flask/helpers.py
        symbol: send_file
excluded:
  - repo: gin
    issue: https://github.com/gin-gonic/gin/issues/2
    reason: the fix spans two repositories, so there is no single landing site
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "labels.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# ---- the shipped file -------------------------------------------------------


def test_the_shipped_gold_set_is_valid_and_empty() -> None:
    """Empty is the honest state: localization reports not_measured, never 0."""
    gold = load_labels()
    assert isinstance(gold, GoldSet)
    assert not gold.measured


# ---- what a label must prove ------------------------------------------------


def test_a_good_label_loads(tmp_path: Path) -> None:
    gold = load_labels(_write(tmp_path, GOOD), known_repos={"flask", "gin"})
    assert len(gold.labels) == 1
    assert gold.labels[0].paths == {"src/flask/helpers.py"}
    assert gold.measured


def test_an_abbreviated_fix_commit_is_refused(tmp_path: Path) -> None:
    """It reads as a commit, resolves for a human, and cannot be handed to git."""
    bad = GOOD.replace("d318b683471101618febed18996405ad26462110", "d318b6834711")
    with pytest.raises(LabelError, match="full 40-character"):
        load_labels(_write(tmp_path, bad), known_repos={"flask", "gin"})


def test_an_issue_that_is_not_a_url_is_refused(tmp_path: Path) -> None:
    """Every row has to be checkable upstream, or the gold set is unfalsifiable."""
    bad = GOOD.replace("https://github.com/pallets/flask/issues/1", "flask#1")
    with pytest.raises(LabelError, match="URL"):
        load_labels(_write(tmp_path, bad), known_repos={"flask", "gin"})


def test_a_repo_outside_the_corpus_is_refused(tmp_path: Path) -> None:
    """A typo would otherwise score zero issues for a repository that does not exist."""
    with pytest.raises(LabelError, match="not in the corpus manifest"):
        load_labels(_write(tmp_path, GOOD), known_repos={"gin"})


def test_a_label_with_no_fix_site_is_refused(tmp_path: Path) -> None:
    bad = GOOD.replace("      - path: src/flask/helpers.py\n        symbol: send_file\n", "")
    with pytest.raises(LabelError):
        load_labels(_write(tmp_path, bad), known_repos={"flask", "gin"})


def test_the_same_issue_cannot_be_labelled_twice(tmp_path: Path) -> None:
    """Two rows for one issue would weight it double and nobody would see it."""
    entry = GOOD.split("labels:")[1].split("excluded:")[0].rstrip() + "\n"
    doubled = GOOD.split("excluded:")[0] + entry
    with pytest.raises(LabelError, match="labelled twice"):
        load_labels(_write(tmp_path, doubled), known_repos={"flask", "gin"})


def test_an_exclusion_needs_a_reason(tmp_path: Path) -> None:
    """An exclusion states what the corpus does not cover; without a reason it states nothing."""
    bad = GOOD.replace("    reason: the fix spans two repositories, so there is no single landing site\n", "")
    with pytest.raises(LabelError, match="missing"):
        load_labels(_write(tmp_path, bad), known_repos={"flask", "gin"})


def test_a_path_the_fix_created_is_caught(tmp_path: Path) -> None:
    """The likeliest labelling mistake, and a silent one.

    The corpus is pinned BEFORE these fixes, so a file the fix created is not in the tree
    `investigate` searches — no run could ever have found it, and the label would quietly cost
    a point that was never available.
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "there.py").write_text("x = 1\n", encoding="utf-8")

    from orchestrator.evals.labels import FixSite

    def _label(path: str) -> Label:
        return Label(
            repo="flask",
            issue="https://example.com/1",
            title="t",
            fix_commit="a" * 40,
            fix_sites=(FixSite(path=path),),
        )

    ok = _label("src/there.py")
    missing = _label("src/created_by_the_fix.py")

    assert unresolvable_paths(ok, repo) == []
    assert unresolvable_paths(missing, repo) == ["src/created_by_the_fix.py"]


# ---- scoring ----------------------------------------------------------------


def test_nothing_labelled_is_not_measured_rather_than_zero() -> None:
    report = LocalizationReport(results=())
    assert not report.measured
    assert report.rate_at(1) is None


def test_top_k_counts_the_first_correct_file() -> None:
    report = LocalizationReport(
        results=(
            LocalizationResult(issue="a", repo="flask", rank=1, returned=10),
            LocalizationResult(issue="b", repo="flask", rank=4, returned=10),
            LocalizationResult(issue="c", repo="flask", rank=None, returned=10),
        )
    )
    assert report.hits_at(1) == 1
    assert report.hits_at(5) == 2
    assert report.hits_at(10) == 2  # the miss stays a miss at every k


def test_an_empty_result_is_distinguished_from_a_bad_ranking() -> None:
    """Returning nothing and ranking badly are different failures; averaging hides one."""
    report = LocalizationReport(
        results=(
            LocalizationResult(issue="a", repo="flask", rank=None, returned=0),
            LocalizationResult(issue="b", repo="flask", rank=None, returned=10),
        )
    )
    assert report.as_dict()["empty_results"] == 1


def test_a_repo_that_did_not_materialise_is_skipped_not_missed(tmp_path: Path) -> None:
    """A corpus that could not be fetched must not read as a tool that found nothing."""
    from orchestrator.evals.labels import FixSite

    gold = GoldSet(
        labels=(
            Label(
                repo="absent",
                issue="https://example.com/1",
                title="t",
                fix_commit="a" * 40,
                fix_sites=(FixSite(path="x.py"),),
            ),
        )
    )
    report = score_localization(gold, {})
    assert not report.measured  # skipped, so nothing is scored — not scored as a miss
