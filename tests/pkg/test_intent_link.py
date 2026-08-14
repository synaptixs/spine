"""The recorded intent tier — git history joined to symbols.

Tested against real git repositories built in `tmp_path`, not mocked subprocess output. The
whole feature is an assertion about what git says, and a fake `git blame` would only assert
what the author believed it says.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from orchestrator.pkg import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, NodeKind
from orchestrator.pkg.intent_link import link_intents
from orchestrator.pkg.verify import verify_batch

SOURCE = "def helper() -> int:\n    return 1\n\n\nclass Widget:\n    name: str = 'w'\n"


def _repo(root: Path, *, message: str, source: str = SOURCE) -> Path:
    """A real one-commit git repository."""
    pkg = root / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(source, encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True, env=env)
    return root


def _scan(root: Path) -> tuple[Any, Any]:
    batch = RepoCodeExtractor().extract(root)
    return batch, link_intents(batch, root)


def test_a_keyed_commit_yields_an_intent_and_serves_edges(tmp_path: Path) -> None:
    batch, cov = _scan(_repo(tmp_path, message="feat(app): do the thing (SSPN-49)"))

    intents = [n for n in batch.nodes if n.kind is NodeKind.INTENT]
    assert [n.id for n in intents] == ["intent:SSPN-49"]
    assert cov.intents == 1
    serves = [e for e in batch.edges if e.kind is EdgeKind.SERVES]
    assert serves, "symbols touched by a keyed commit must serve it"
    assert all(e.dst == "intent:SSPN-49" for e in serves)


def test_an_intent_node_carries_no_provenance(tmp_path: Path) -> None:
    """An Intent is a reason, not a place in a file.

    `grounded` is therefore False, so `pkg verify`'s provenance check skips it rather than
    failing on a locator that was never meant to resolve.
    """
    batch, _ = _scan(_repo(tmp_path, message="fix: something (SSPN-7)"))
    (intent,) = [n for n in batch.nodes if n.kind is NodeKind.INTENT]
    assert intent.provenance is None
    assert not intent.grounded


def test_the_serves_edge_carries_the_symbol_s_provenance(tmp_path: Path) -> None:
    """The edge points at where the symbol is, which is the only file:line involved."""
    batch, _ = _scan(_repo(tmp_path, message="feat: x (SSPN-1)"))
    (edge,) = [e for e in batch.edges if e.kind is EdgeKind.SERVES and e.src.endswith(".helper")]
    assert edge.provenance is not None
    assert edge.provenance.file == "app/mod.py"


def test_modules_are_not_attributed(tmp_path: Path) -> None:
    """Attributing a whole file to whichever ticket last touched line 1 is noise wearing a
    provenance label."""
    batch, _ = _scan(_repo(tmp_path, message="feat: x (SSPN-2)"))
    module_ids = {n.id for n in batch.nodes if n.kind is NodeKind.MODULE}
    assert not [e for e in batch.edges if e.kind is EdgeKind.SERVES and e.src in module_ids]


def test_a_history_with_no_issue_keys_yields_nothing(tmp_path: Path) -> None:
    """Most repositories do not use issue keys. That is silence, not an error."""
    batch, cov = _scan(_repo(tmp_path, message="just a normal commit message"))
    assert cov.intents == 0
    assert cov.symbols_attributed == 0
    assert [n for n in batch.nodes if n.kind is NodeKind.INTENT] == []


def test_a_directory_that_is_not_a_repository_yields_nothing(tmp_path: Path) -> None:
    """A shallow clone, a tarball, a vendored tree — all degrade to zero intents, never a crash."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(SOURCE, encoding="utf-8")

    batch = RepoCodeExtractor().extract(tmp_path)
    cov = link_intents(batch, tmp_path)
    assert cov.intents == 0
    assert cov.commits_scanned == 0


def test_the_scan_is_deterministic(tmp_path: Path) -> None:
    """Same history, same facts — this runs inside a pipeline whose value is reproducibility."""
    root = _repo(tmp_path, message="feat: x (SSPN-3)")
    first = RepoCodeExtractor().extract(root)
    link_intents(first, root)
    second = RepoCodeExtractor().extract(root)
    link_intents(second, root)

    def key(b: Any) -> list[Any]:
        return sorted(e.key() for e in b.edges if e.kind is EdgeKind.SERVES)

    assert key(first) == key(second)


def test_the_key_pattern_is_configurable(tmp_path: Path) -> None:
    """No repository outside this one uses this project's prefix."""
    root = _repo(tmp_path, message="feat: x (ACME-123)")
    batch = RepoCodeExtractor().extract(root)
    link_intents(batch, root)
    assert [n.id for n in batch.nodes if n.kind is NodeKind.INTENT] == ["intent:ACME-123"]

    batch2 = RepoCodeExtractor().extract(root)
    link_intents(batch2, root, key_pattern=r"\bNOPE-\d+\b")
    assert [n for n in batch2.nodes if n.kind is NodeKind.INTENT] == []


def test_coverage_reports_a_rate_not_just_a_count(tmp_path: Path) -> None:
    """A tier that attributes 12 of 4,000 symbols is working as designed and still says almost
    nothing. Only the ratio makes that visible."""
    _, cov = _scan(_repo(tmp_path, message="feat: x (SSPN-4)"))
    assert cov.symbols_total > 0
    assert cov.rate == cov.symbols_attributed / cov.symbols_total


def test_verify_passes_with_intent_facts_present(tmp_path: Path) -> None:
    """No dangling SERVES endpoint, and no stale provenance from a node that has none."""
    root = _repo(tmp_path, message="feat: x (SSPN-5)")
    batch = RepoCodeExtractor().extract(root)
    link_intents(batch, root)
    report = verify_batch(batch, root)
    assert not [i for i in report.issues if i.check in {"dangling-edge", "stale-provenance"}]


# ---- opt-in wiring ---------------------------------------------------------


def test_analysis_does_not_scan_intents_by_default(tmp_path: Path) -> None:
    """`understand` and `state` cost ~8s more with the scan, and nothing renders the facts.

    Default-on would charge every user for output that is byte-identical. It becomes a
    default when something reads it — see `analysis.py`.
    """
    from orchestrator.knowledge.analysis import analyse

    _repo(tmp_path, message="feat: x (SSPN-9)")
    result = analyse(tmp_path)
    assert [n for n in result.batch.nodes if n.kind is NodeKind.INTENT] == []


def test_analysis_scans_intents_when_asked(tmp_path: Path) -> None:
    from orchestrator.knowledge.analysis import analyse

    _repo(tmp_path, message="feat: x (SSPN-9)")
    result = analyse(tmp_path, intents=True)
    assert [n.id for n in result.batch.nodes if n.kind is NodeKind.INTENT] == ["intent:SSPN-9"]
    assert any(e.kind is EdgeKind.SERVES for e in result.batch.edges)
