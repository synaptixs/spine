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


def _commit(root: Path, message: str) -> None:
    """Another commit on an existing fixture repo — for tests that need two distinct keys."""
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True, env=env)


def _scan(root: Path, *, prefixes: list[str] | None = None, **kwargs: Any) -> tuple[Any, Any]:
    """Scan with the prefix named, because these tests are about the **join**, not the policy.

    Since 2026-09-01 an unnamed prefix has to be inferred, and the inference declines a prefix
    seen with exactly one number — one is indistinguishable from `SHA-256` (spec §6.1). Every
    fixture here is a one-commit repository, so leaving it to the inference would make each of
    these assert the rejection rule rather than the thing it is named for. The default path is
    covered end to end in `test_graph_export.py`.
    """
    batch = RepoCodeExtractor().extract(root)
    return batch, link_intents(batch, root, prefixes=prefixes or ["SSPN"], **kwargs)


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
    link_intents(batch, root, prefixes=["ACME"])
    assert [n.id for n in batch.nodes if n.kind is NodeKind.INTENT] == ["intent:ACME-123"]

    batch2 = RepoCodeExtractor().extract(root)
    link_intents(batch2, root, key_pattern=r"\b(NOPE)-\d+\b", prefixes=["NOPE"])
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

    # Two distinct numbers under one prefix: `analyse` takes no prefix argument, so this is the
    # inference's own path, and one number is declined by design (spec §6.1).
    root = _repo(tmp_path, message="feat: x (SSPN-9)")
    (root / "app" / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    _commit(root, "feat: y (SSPN-10)")

    result = analyse(tmp_path, intents=True)
    found = sorted(n.id for n in result.batch.nodes if n.kind is NodeKind.INTENT)
    assert found == ["intent:SSPN-10", "intent:SSPN-9"]
    assert any(e.kind is EdgeKind.SERVES for e in result.batch.edges)


# ---- the query seam, and the consumer (spec phases 2 and 3) ------------------


def test_intents_for_and_symbols_serving_are_inverses(tmp_path: Path) -> None:
    from orchestrator.pkg.store import FactStore

    root = _repo(tmp_path, message="feat: x (SSPN-9)")
    (root / "app" / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    _commit(root, "feat: y (SSPN-10)")
    batch, _ = _scan(root)
    store = FactStore(batch)

    served = [n for n in batch.nodes if store.intents_for(n.id)]
    assert served, "the fixture must attribute something, or this proves nothing"

    for symbol in served:
        for intent in store.intents_for(symbol.id):
            assert symbol.id in {s.id for s in store.symbols_serving(intent.id)}


def test_an_unscanned_graph_reports_no_intents_rather_than_failing(tmp_path: Path) -> None:
    """Empty means "not scanned or not attributed", never "no prior work".

    `intents_for` is called unconditionally by `build_investigation`, so a graph that never had
    the tier run must answer quietly rather than raise.
    """
    from orchestrator.pkg.store import FactStore

    root = _repo(tmp_path, message="feat: x (SSPN-9)")
    batch = RepoCodeExtractor().extract(root)  # no link_intents
    store = FactStore(batch)

    assert all(store.intents_for(n.id) == [] for n in batch.nodes)


def test_a_landing_carries_the_tickets_it_was_last_changed_for(tmp_path: Path) -> None:
    from orchestrator.pkg.store import FactStore
    from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

    root = _repo(tmp_path, message="feat: handler (SSPN-9)")
    (root / "app" / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    _commit(root, "feat: more (SSPN-10)")
    batch, _ = _scan(root)

    # The fixture's symbols are `helper` and `Widget`; retrieval is lexical, so the query has
    # to name one or nothing lands and the assertion below would be about retrieval, not intent.
    inv = build_investigation("helper returns the wrong int", "fix helper", store=FactStore(batch), root=None)
    assert any(hit.intents for hit in inv.landing), "a landing should carry its ticket"

    md = render_investigation_md(inv)
    assert "last changed for" in md
    assert "Recorded intent covers" in md


def test_the_brief_says_nothing_about_intent_when_the_tier_did_not_run(tmp_path: Path) -> None:
    """A coverage note on an unscanned run would claim 0% where the truth is "not measured"."""
    from orchestrator.pkg.store import FactStore
    from orchestrator.sdlc.investigate import build_investigation, render_investigation_md

    root = _repo(tmp_path, message="feat: handler (SSPN-9)")
    batch = RepoCodeExtractor().extract(root)  # no link_intents

    md = render_investigation_md(
        build_investigation("helper returns the wrong int", "fix helper", store=FactStore(batch), root=None)
    )
    assert "last changed for" not in md
    assert "Recorded intent covers" not in md


def test_the_landing_order_is_stable_for_a_commit(tmp_path: Path) -> None:
    """`SERVES` comes out in blame order — stable, but not meaningful.

    A brief that reorders between runs on the same commit cannot be diffed, which is the
    property every comprehension surface here is built on.
    """
    from orchestrator.pkg.store import FactStore
    from orchestrator.sdlc.investigate import build_investigation

    root = _repo(tmp_path, message="feat: handler (SSPN-9)")
    (root / "app" / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    _commit(root, "feat: more (SSPN-10)")
    batch, _ = _scan(root)

    first = build_investigation("helper", "fix helper", store=FactStore(batch), root=None)
    second = build_investigation("helper", "fix helper", store=FactStore(batch), root=None)
    assert [h.intents for h in first.landing] == [h.intents for h in second.landing]
