"""`ExtractionRun` and the finalizer order — what one `RepoCodeExtractor.extract` shares between
front-ends, and the order their whole-repo passes run in (js-review-followup D11, D12).
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.extractor import ExtractionRun, RepoCodeExtractor
from orchestrator.pkg.facts import FactBatch


class _FrontEnd:
    """A front-end that reads nothing and records when it is bound and finalized."""

    def __init__(self, name: str, suffix: str, log: list[str]) -> None:
        self.language = name
        self.suffixes: tuple[str, ...] = (suffix,)
        self.log = log
        self.runs: list[ExtractionRun] = []
        self.bound_before_extract: list[bool] = []

    def module_name(self, path: Path, root: Path) -> str:
        return path.stem

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        self.bound_before_extract.append(bool(self.runs))
        return FactBatch()

    def bind_run(self, run: ExtractionRun) -> None:
        self.runs.append(run)

    def finalize(self, batch: FactBatch) -> FactBatch:
        self.log.append(self.language)
        return batch


def _files(root: Path, *names: str) -> Path:
    for name in names:
        (root / name).write_text("")
    return root


def test_finalizers_run_in_registration_order_not_walk_order(tmp_path: Path) -> None:
    """`0.b` is walked before `1.a`; the order the front-ends were registered in still decides."""
    root = _files(tmp_path, "0.b", "1.a")
    for names in (["A", "B"], ["B", "A"]):
        log: list[str] = []
        suffix = {"A": ".a", "B": ".b"}
        RepoCodeExtractor(extractors=[_FrontEnd(n, suffix[n], log) for n in names]).extract(root)
        assert log == names


def test_every_front_end_in_a_run_shares_one_context_bound_before_its_first_file(tmp_path: Path) -> None:
    root = _files(tmp_path, "x.a", "y.a", "z.b")
    log: list[str] = []
    a, b = _FrontEnd("A", ".a", log), _FrontEnd("B", ".b", log)
    repo = RepoCodeExtractor(extractors=[a, b])
    repo.extract(root)
    assert len(a.runs) == len(b.runs) == 1  # once per run, not once per file
    assert a.runs[0] is b.runs[0]
    assert a.bound_before_extract == [True, True] and b.bound_before_extract == [True]
    repo.extract(root)
    assert a.runs[1] is not a.runs[0]  # a fresh context per run: nothing carries over


def test_a_front_end_with_no_file_in_the_repo_is_never_bound(tmp_path: Path) -> None:
    """Bound on first use: one that never reads a file would never finalize and let go of it."""
    root = _files(tmp_path, "x.a")
    log: list[str] = []
    a, unused = _FrontEnd("A", ".a", log), _FrontEnd("C", ".c", log)
    RepoCodeExtractor(extractors=[a, unused]).extract(root)
    assert unused.runs == []
    assert log == ["A"]
