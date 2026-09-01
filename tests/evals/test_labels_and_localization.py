"""The G6 gold set: what a label must prove before it can score anything.

The labels are the answer key, and an answer key is only worth having if a reader can check it
and if it did not come from the system under test. Both are enforced at load, because the point
where a bad label becomes a wrong number is much too late.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_the_shipped_gold_set_is_valid() -> None:
    """It parses, and every row carries what a reader needs to check it upstream."""
    from orchestrator.evals.corpus_fetch import load_manifest

    gold = load_labels(known_repos={r.name for r in load_manifest()})
    assert isinstance(gold, GoldSet)
    for label in gold.labels:
        assert len(label.fix_commit) == 40
        assert label.issue.startswith("https://")
        assert label.title.strip()
        assert label.paths


def test_the_gold_set_is_not_dominated_by_one_repository() -> None:
    """Six labels from one repo and one from the rest measures that repo, not the tool.

    D3 chose five repositories for language spread; a gold set that drifts back into one of
    them quietly undoes that, and the number would read as general.
    """
    from collections import Counter

    from orchestrator.evals.corpus_fetch import load_manifest

    gold = load_labels(known_repos={r.name for r in load_manifest()})
    if not gold.labels:
        return  # nothing to skew yet
    counts = Counter(label.repo for label in gold.labels)
    assert max(counts.values()) <= len(gold.labels) // 2, f"one repository dominates: {dict(counts)}"


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


def test_a_root_that_no_longer_exists_is_skipped_not_scored(tmp_path: Path) -> None:
    """A plumbing failure must not be reportable as a measurement.

    The first run of this scorer reported 0.00 at every k across 24 labels — a clean,
    publishable-looking number — because the caller held the checkouts in a TemporaryDirectory
    that had already exited. Every path was gone, every extraction returned an empty graph, and
    every label scored as "no landing site". Nothing distinguished that from the tool genuinely
    finding nothing.
    """
    from orchestrator.evals.labels import FixSite

    gold = GoldSet(
        labels=(
            Label(
                repo="flask",
                issue="https://example.com/1",
                title="t",
                fix_commit="a" * 40,
                fix_sites=(FixSite(path="x.py"),),
            ),
        )
    )
    vanished = tmp_path / "deleted-by-the-context-manager"
    report = score_localization(gold, {"flask": vanished})

    assert not report.measured  # not measured — NOT 0/1
    assert report.rate_at(1) is None


# ---- the gate (G6 phase 2) ---------------------------------------------------


def _board(status: str, digest: str, top1: int, top10: int, n: int = 38) -> dict[str, Any]:
    return {
        "metrics": {
            "comprehension": {
                "localization": {
                    "status": status,
                    "gold_digest": digest,
                    "labelled": n,
                    "top_k": {"1": top1, "10": top10},
                }
            }
        }
    }


def test_a_real_regression_fails_the_gate() -> None:
    from orchestrator.pkg.accuracy import compare_scoreboard

    hits = [r for r in compare_scoreboard(_board("measured", "A", 12, 27), _board("measured", "A", 9, 27))]
    assert len(hits) == 1
    assert "top-1 localization fell" in hits[0].detail


def test_a_changed_gold_set_is_not_a_regression() -> None:
    """The mistake that killed the doc-drift gate, refused here by construction.

    Hits can legitimately FALL when the labels change — swap five easy issues for five hard ones
    and the count drops without `investigate` having moved at all. Gating across different label
    sets would fail a pull request for reshaping the corpus, which is the work the gate exists to
    protect, not to punish. The digest is what tells the two apart.
    """
    from orchestrator.pkg.accuracy import compare_scoreboard

    was = _board("measured", "A", 12, 27, n=38)
    # Different gold set AND fewer hits — the shape that must not fail.
    changed = _board("measured", "B", 8, 20, n=38)
    assert compare_scoreboard(was, changed) == []

    # ... and the identical drop on the SAME gold set must fail, or the guard is vacuous.
    same = _board("measured", "A", 8, 20, n=38)
    assert [r for r in compare_scoreboard(was, same)]


def test_an_offline_run_cannot_ratchet_the_gate_down() -> None:
    """Localization needs the network; its absence must never read as zero."""
    from orchestrator.pkg.accuracy import compare_scoreboard

    was = _board("measured", "A", 12, 27)

    # The ordinary offline shape: no numbers at all.
    bare = {
        "metrics": {"comprehension": {"localization": {"status": "not_measured", "reason": "no corpus here"}}}
    }
    assert compare_scoreboard(was, bare) == []

    # And the dangerous shape: `not_measured`, but carrying numbers anyway — a partially written
    # entry, or a hand-edited baseline. `status` is what must decide, not the presence of a
    # `top_k`, because zeros from a run that never happened are the failure this whole session
    # kept finding.
    stale = _board("not_measured", "A", 0, 0)
    assert compare_scoreboard(was, stale) == []


def test_the_digest_ignores_ordering_but_not_content() -> None:
    from orchestrator.evals.labels import FixSite, GoldSet, gold_digest

    def label(issue: str, path: str) -> Label:
        return Label(
            repo="flask", issue=issue, title="t", fix_commit="a" * 40, fix_sites=(FixSite(path=path),)
        )

    a, b = label("https://e/1", "x.py"), label("https://e/2", "y.py")
    assert gold_digest(GoldSet(labels=(a, b))) == gold_digest(GoldSet(labels=(b, a)))
    assert gold_digest(GoldSet(labels=(a,))) != gold_digest(GoldSet(labels=(a, b)))
    assert gold_digest(GoldSet(labels=(a,))) != gold_digest(GoldSet(labels=(label("https://e/1", "z.py"),)))


def test_a_corpus_that_will_not_fetch_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """None, never an empty report — an empty report is a number, and it would ratchet."""
    from orchestrator.evals import localization as loc
    from orchestrator.evals.corpus_fetch import CorpusFetchError

    def boom(*_a: object, **_k: object) -> None:
        raise CorpusFetchError("network is down")

    monkeypatch.setattr(loc, "score_localization", boom)
    monkeypatch.setattr("orchestrator.evals.corpus_fetch.materialize", boom)
    assert loc.measure_pinned() is None
