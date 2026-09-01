"""G6 harness: the pinned corpus, and provenance validity.

The corpus manifest is the one artefact in this programme nothing else can cross-check, so it
is validated at the door. These tests are why: while writing the manifest, four abbreviated
SHAs were padded into plausible-looking 40-character strings. They were well formed, wrong, and
nothing would have caught them until a fetch failed on a machine with a network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.evals.comprehension import ANCHORED_KINDS, score_provenance
from orchestrator.evals.corpus_fetch import CorpusFetchError, load_manifest
from orchestrator.pkg import Node, NodeKind, Provenance, RepoCodeExtractor
from orchestrator.pkg.facts import FactBatch

# ---- the manifest -----------------------------------------------------------


def test_every_pin_is_a_full_commit_id() -> None:
    """An abbreviation reads as a SHA, resolves for a human, and cannot be fetched."""
    for repo in load_manifest():
        assert len(repo.sha) == 40, f"{repo.name} pins {repo.sha!r}"
        assert all(c in "0123456789abcdef" for c in repo.sha)


def test_the_corpus_is_not_python_heavy() -> None:
    """A Python-dominated corpus reports health the Python-only oracles never measured."""
    languages = [r.language for r in load_manifest()]
    assert languages.count("python") <= 1
    assert len(set(languages)) == len(languages), "one repository per front-end"


def test_every_entry_says_why_it_earns_a_slot() -> None:
    """Five slots for six front-ends: the trade has to be written down, not inferred."""
    assert all(r.why.strip() for r in load_manifest())


def test_an_abbreviated_pin_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "corpus.yaml"
    bad.write_text(
        "version: 1\nrepos:\n  - name: x\n    language: go\n    url: https://e/x.git\n"
        "    sha: dcaa4296d111\n    why: because\n",
        encoding="utf-8",
    )
    with pytest.raises(CorpusFetchError, match="full 40-character"):
        load_manifest(bad)


def test_a_missing_field_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "corpus.yaml"
    bad.write_text("version: 1\nrepos:\n  - name: x\n    language: go\n", encoding="utf-8")
    with pytest.raises(CorpusFetchError, match="missing"):
        load_manifest(bad)


def test_a_duplicate_name_is_refused(tmp_path: Path) -> None:
    """Two entries with one name means one silently overwrites the other's checkout."""
    entry = (
        "  - name: x\n    language: go\n    url: https://e/x.git\n"
        "    sha: " + "a" * 40 + "\n    why: because\n"
    )
    bad = tmp_path / "corpus.yaml"
    bad.write_text("version: 1\nrepos:\n" + entry + entry, encoding="utf-8")
    with pytest.raises(CorpusFetchError, match="duplicate"):
        load_manifest(bad)


# ---- provenance validity ----------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def alpha():\n    return 1\n\n\nclass Beta:\n    pass\n", encoding="utf-8")
    return repo


def test_facts_open_to_a_line_that_names_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = score_provenance(RepoCodeExtractor().extract(repo), repo)
    assert report.measured and report.rate == 1
    assert report.resolved == report.anchored


def test_a_wrong_line_is_caught(tmp_path: Path) -> None:
    """The point of the metric: an off-by-one in a front-end must show up here."""
    repo = _repo(tmp_path)
    batch = RepoCodeExtractor().extract(repo)
    moved = FactBatch()
    for n in batch.nodes:
        prov = n.provenance
        if n.kind is NodeKind.FUNCTION and prov is not None:
            moved.add_node(Node(n.id, n.kind, n.name, n.language, Provenance(prov.file, prov.line + 1)))
        else:
            moved.add_node(n)

    report = score_provenance(moved, repo)
    assert report.rate is not None and report.rate < 1


def test_kinds_named_by_construction_are_excluded_not_passed(tmp_path: Path) -> None:
    """Module/Endpoint/Entity are named for what they are, not by a token at the line.

    Scoring them would measure a naming convention and call it provenance; omitting them
    silently would let 'not scored' read as 'passed'. They are counted separately.
    """
    repo = _repo(tmp_path)
    report = score_provenance(RepoCodeExtractor().extract(repo), repo)
    assert report.excluded > 0
    assert NodeKind.MODULE not in ANCHORED_KINDS


def test_nothing_anchored_is_not_a_clean_result(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    report = score_provenance(FactBatch(), repo)
    assert not report.measured and report.rate is None


def test_an_unreadable_file_is_reported_never_counted_as_passing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    batch = RepoCodeExtractor().extract(repo)
    (repo / "m.py").unlink()  # the graph still claims it

    report = score_provenance(batch, repo)
    assert report.unreadable == report.anchored
    assert report.resolved == 0
