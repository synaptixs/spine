"""`pkg verify` — the Tier-1 invariants catch completeness failures, not just soundness ones."""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg import EdgeKind, FactBatch, NodeKind, RepoCodeExtractor
from orchestrator.pkg.facts import Edge, Node, Provenance
from orchestrator.pkg.verify import MIN_MODULES, verify_batch


def _module(i: int, *, external: bool = False) -> Node:
    mid = f"py:pkg.m{i}"
    prov = None if external else Provenance(f"pkg/m{i}.py", 1)
    return Node(mid, NodeKind.MODULE, f"pkg.m{i}", "python", prov, external=external)


def _write_module_files(root: Path, count: int) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (root / "pkg" / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")


def test_healthy_extracted_repo_passes(tmp_path: Path) -> None:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from . import core\n", encoding="utf-8")
    (pkg / "core.py").write_text("from .util import helper\n", encoding="utf-8")
    (pkg / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    report = verify_batch(RepoCodeExtractor().extract(tmp_path), tmp_path)
    assert report.ok, [i.message for i in report.issues]


def test_dangling_edge_is_an_error(tmp_path: Path) -> None:
    batch = FactBatch()
    _write_module_files(tmp_path, 1)
    batch.add_node(_module(0))
    batch.add_edge(Edge("py:pkg.m0", "py:ghost", EdgeKind.IMPORTS))

    report = verify_batch(batch, tmp_path)
    assert [i.check for i in report.errors] == ["dangling-edge"]


def test_stale_provenance_is_an_error(tmp_path: Path) -> None:
    batch = FactBatch()
    batch.add_node(Node("py:gone", NodeKind.MODULE, "gone", "python", Provenance("gone.py", 1)))
    report = verify_batch(batch, tmp_path)
    assert [i.check for i in report.errors] == ["stale-provenance"]

    (tmp_path / "short.py").write_text("x = 1\n", encoding="utf-8")
    batch2 = FactBatch()
    batch2.add_node(Node("py:short", NodeKind.MODULE, "short", "python", Provenance("short.py", 99)))
    report2 = verify_batch(batch2, tmp_path)
    assert [i.check for i in report2.errors] == ["stale-provenance"]


def test_unjoined_import_graph_fails_on_orphan_rate_and_external_ratio(tmp_path: Path) -> None:
    """The click-shaped failure: every module dangles → verify must fail."""
    count = max(MIN_MODULES, 10)
    _write_module_files(tmp_path, count)
    batch = FactBatch()
    for i in range(count):
        batch.add_node(_module(i))
    # every import points at an unjoined phantom (the pre-fix world)
    for i in range(count):
        for j in range(3):
            phantom = f"py:m{(i + j + 1) % count}"
            batch.add_node(Node(phantom, NodeKind.MODULE, f"m{j}", "python", external=True))
            batch.add_edge(Edge(f"py:pkg.m{i}", phantom, EdgeKind.IMPORTS, Provenance(f"pkg/m{i}.py", 1)))

    report = verify_batch(batch, tmp_path)
    checks = {i.check for i in report.errors}
    assert "orphan-rate" in checks
    assert "external-ratio" in checks
    assert not report.ok
    # and the phantoms are named for what they are
    assert any(i.check == "phantom-module" for i in report.warnings)


def test_joined_import_graph_passes_the_rates(tmp_path: Path) -> None:
    count = max(MIN_MODULES, 10)
    _write_module_files(tmp_path, count)
    batch = FactBatch()
    for i in range(count):
        batch.add_node(_module(i))
    # a ring of real module→module imports (plus a few stdlib externals)
    batch.add_node(Node("py:os", NodeKind.MODULE, "os", "python", external=True))
    for i in range(count):
        batch.add_edge(
            Edge(
                f"py:pkg.m{i}", f"py:pkg.m{(i + 1) % count}", EdgeKind.IMPORTS, Provenance(f"pkg/m{i}.py", 1)
            )
        )
        batch.add_edge(Edge(f"py:pkg.m{i}", "py:os", EdgeKind.IMPORTS, Provenance(f"pkg/m{i}.py", 2)))

    report = verify_batch(batch, tmp_path)
    assert report.ok, [i.message for i in report.issues]


def test_small_fixtures_are_exempt_from_rates(tmp_path: Path) -> None:
    _write_module_files(tmp_path, 2)
    batch = FactBatch()
    for i in range(2):
        batch.add_node(_module(i))
    batch.add_node(Node("py:os", NodeKind.MODULE, "os", "python", external=True))
    batch.add_edge(Edge("py:pkg.m0", "py:os", EdgeKind.IMPORTS, Provenance("pkg/m0.py", 1)))

    report = verify_batch(batch, tmp_path)
    assert report.ok  # 100% external, but far below the population minimums
