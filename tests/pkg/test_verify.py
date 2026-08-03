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


# ---- source-parity: is the graph complete with respect to the source? ------


def _app(root: Path, body: str, *, name: str = "api.py") -> FactBatch:
    """A one-module repo whose source says ``body``, extracted normally."""
    pkg = root / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / name).write_text(body, encoding="utf-8")
    return RepoCodeExtractor().extract(root)


def _parity(report: object) -> list[str]:
    return [i.message for i in report.issues if i.check == "source-parity"]  # type: ignore[attr-defined]


def test_route_decorators_with_no_endpoint_node_warn(tmp_path: Path) -> None:
    """The failure this check exists for: 77 routes in source, zero Endpoint nodes,
    and every other invariant green because the graph is self-consistent."""
    report = verify_batch(_app(tmp_path, '@router.get("/items")\ndef items():\n    return []\n'), tmp_path)
    assert len(_parity(report)) == 1
    assert "Endpoint" in _parity(report)[0]


def test_tablename_with_no_entity_node_warns(tmp_path: Path) -> None:
    body = 'class Row(Base):\n    __tablename__ = "rows"\n'
    report = verify_batch(_app(tmp_path, body), tmp_path)
    assert len(_parity(report)) == 1
    assert "Entity" in _parity(report)[0]


def test_parity_is_a_warning_never_an_error(tmp_path: Path) -> None:
    """A front-end that hasn't learned a framework must not fail someone's build —
    a check people switch off catches nothing."""
    report = verify_batch(_app(tmp_path, '@app.post("/x")\ndef x():\n    return 1\n'), tmp_path)
    assert report.ok
    assert [i.severity for i in report.issues if i.check == "source-parity"] == ["warning"]


def test_silent_when_the_graph_already_has_the_kind(tmp_path: Path) -> None:
    """Once the front-end extracts endpoints, the check must go quiet."""
    batch = _app(tmp_path, '@router.get("/items")\ndef items():\n    return []\n')
    batch.add_node(
        Node("py:endpoint:GET /items", NodeKind.ENDPOINT, "GET /items", "python", Provenance("app/api.py", 1))
    )
    assert _parity(verify_batch(batch, tmp_path)) == []


def test_no_false_positive_on_a_plain_library(tmp_path: Path) -> None:
    report = verify_batch(_app(tmp_path, "def add(a, b):\n    return a + b\n"), tmp_path)
    assert _parity(report) == []


def test_non_route_attribute_calls_are_not_routes(tmp_path: Path) -> None:
    """``@cache.get(key)`` takes a name, not a path literal — the same precision rule
    the extractors hold to, so the check can't cry wolf on ordinary decorators."""
    body = "@cache.get(key)\ndef fetch():\n    return 1\n"
    assert _parity(verify_batch(_app(tmp_path, body), tmp_path)) == []


def test_computed_tablename_is_not_a_declaration(tmp_path: Path) -> None:
    body = "class Row(Base):\n    __tablename__ = derive_name()\n"
    assert _parity(verify_batch(_app(tmp_path, body), tmp_path)) == []


def test_a_language_with_no_patterns_is_never_flagged(tmp_path: Path) -> None:
    """Only languages with declared syntax participate; the rest are silent, not guessed at."""
    batch = FactBatch()
    batch.add_node(Node("go:m", NodeKind.MODULE, "m", "go", Provenance("m.go", 1)))
    (tmp_path / "m.go").write_text("package m\n", encoding="utf-8")
    assert _parity(verify_batch(batch, tmp_path)) == []
