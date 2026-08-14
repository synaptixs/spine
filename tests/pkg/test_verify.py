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


def _lang_repo(root: Path, lang: str, name: str, body: str) -> FactBatch:
    """A one-module batch for ``lang`` whose source file says ``body``.

    Built by hand rather than extracted so the non-Python cases don't depend on an
    optional tree-sitter extra being installed.
    """
    (root / name).write_text(body, encoding="utf-8")
    batch = FactBatch()
    batch.add_node(Node(f"{lang}:m", NodeKind.MODULE, "m", lang, Provenance(name, 1)))
    return batch


def test_python_route_decorators_no_longer_trip_the_check(tmp_path: Path) -> None:
    """The gap this check was written to expose — 77 routes in source, zero Endpoint nodes —
    is closed for Python: the front-end now emits them, so the warning must fall silent.

    The warning path is still exercised, on TypeScript, which has the identical gap:
    ``test_typescript_nest_decorators_warn`` / ``test_typescript_express_routes_warn``.
    """
    report = verify_batch(_app(tmp_path, '@router.get("/items")\ndef items():\n    return []\n'), tmp_path)
    assert _parity(report) == []


def test_python_tablenames_no_longer_trip_the_check(tmp_path: Path) -> None:
    """SSPN-3 closed the other half of the gap: a declared table now yields an ``Entity``,
    so the warning must fall silent for Python too.

    The Entity warning path stays covered on TypeScript, which still has the gap —
    see ``test_typeorm_entity_decorator_warns``.
    """
    body = 'class Row(Base):\n    __tablename__ = "rows"\n'
    assert _parity(verify_batch(_app(tmp_path, body), tmp_path)) == []


def test_parity_is_a_warning_never_an_error(tmp_path: Path) -> None:
    """A front-end that hasn't learned a framework must not fail someone's build —
    a check people switch off catches nothing.

    On TypeScript now that Python emits endpoints; the severity rule is what's under test,
    not which language happens to be behind.
    """
    batch = _lang_repo(tmp_path, "typescript", "server.ts", 'router.get("/users", handler)\n')
    report = verify_batch(batch, tmp_path)
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


def test_typescript_nest_decorators_warn(tmp_path: Path) -> None:
    """TypeScript emits no Endpoint node either, so a Nest service is just as
    invisible as a FastAPI one — the check has to watch both front-ends, not one."""
    batch = _lang_repo(tmp_path, "typescript", "app.ts", "@Get('/items')\nfindAll() {}\n")
    assert len(_parity(verify_batch(batch, tmp_path))) == 1


def test_typescript_express_routes_warn(tmp_path: Path) -> None:
    batch = _lang_repo(tmp_path, "typescript", "server.ts", 'router.get("/users", handler)\n')
    assert len(_parity(verify_batch(batch, tmp_path))) == 1


def test_typescript_map_get_is_not_a_route(tmp_path: Path) -> None:
    """The leading slash is the whole precision rule: without it every `Map.get`
    in a TypeScript codebase would look like an HTTP route."""
    body = 'const v = cache.get(key);\nconst w = headers.get("content-type");\n'
    assert _parity(verify_batch(_lang_repo(tmp_path, "typescript", "u.ts", body), tmp_path)) == []


def test_typeorm_entity_decorator_warns(tmp_path: Path) -> None:
    batch = _lang_repo(tmp_path, "typescript", "user.entity.ts", "@Entity()\nclass User {}\n")
    messages = _parity(verify_batch(batch, tmp_path))
    assert len(messages) == 1
    assert "Entity" in messages[0]


# ---- per-construct parity (phase 3) --------------------------------------


def test_a_route_decorator_inside_a_string_is_not_counted(tmp_path: Path) -> None:
    """The false-signal class that made counting necessary in the first place.

    Existence-checking tolerated these — one real node anywhere in the language silenced the
    warning. Counting turns each one into a phantom missing route: measured on this repo, the
    regex found 96 "routes" against 71 real ones, and 19 of the 25 apparent misses were
    decorators quoted in test fixtures and docstrings. To an AST, a decorator in a string is
    a string.
    """
    body = 'FIXTURE = """\n@router.get("/items")\ndef items():\n    return []\n"""\n'
    assert _parity(verify_batch(_app(tmp_path, body), tmp_path)) == []


def test_a_computed_path_is_counted_as_declared(tmp_path: Path) -> None:
    """The counter is deliberately WIDER than the extractor, and that gap is the measurement.

    `python_routes` skips an f-string path on purpose — silence rather than a guessed route.
    Counting it here is what turns that documented silence into a number.
    """
    body = 'import x\n\n\n@router.get(f"/items/{x}")\ndef items():\n    return []\n'
    messages = _parity(verify_batch(_app(tmp_path, body), tmp_path))
    assert len(messages) == 1
    assert "declares 1 route declaration(s)" in messages[0]
    assert "under-reports by 1" in messages[0]


def test_the_warning_names_the_file_and_line(tmp_path: Path) -> None:
    body = 'import x\n\n\n@router.get(f"/a/{x}")\ndef a():\n    return []\n'
    (message,) = _parity(verify_batch(_app(tmp_path, body), tmp_path))
    assert "app/api.py:4" in message


def test_more_nodes_than_declarations_never_warns(tmp_path: Path) -> None:
    """A router mounted twice yields two Endpoints from one decorator — correct, not a defect.

    Warning on it would cry wolf on right answers, which is how a check gets switched off.
    """
    batch = _app(tmp_path, '@router.get("/items")\ndef items():\n    return []\n')
    batch.add_node(
        Node(
            "py:endpoint:GET /v2/items",
            NodeKind.ENDPOINT,
            "GET /v2/items",
            "python",
            Provenance("app/api.py", 1),
        )
    )
    assert _parity(verify_batch(batch, tmp_path)) == []


def test_regex_counted_languages_are_labelled_approximate(tmp_path: Path) -> None:
    """A pattern-derived count is a weaker claim than a parsed one and says so."""
    batch = _lang_repo(tmp_path, "typescript", "app.ts", "@Get('/items')\nfindAll() {}\n")
    (message,) = _parity(verify_batch(batch, tmp_path))
    assert "approximate" in message


def test_counts_are_per_file_not_per_language(tmp_path: Path) -> None:
    """The whole point of the phase: two files, only the deficient one is named."""
    pkg = tmp_path / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "good.py").write_text('@router.get("/ok")\ndef ok():\n    return []\n', encoding="utf-8")
    (pkg / "bad.py").write_text(
        'import x\n\n\n@router.get(f"/no/{x}")\ndef no():\n    return []\n', encoding="utf-8"
    )

    messages = _parity(verify_batch(RepoCodeExtractor().extract(tmp_path), tmp_path))
    assert len(messages) == 1
    assert "app/bad.py" in messages[0]


def test_an_endpoint_declared_in_two_files_is_credited_to_both(tmp_path: Path) -> None:
    """The artifact that made this repo report 5 missing routes that were never missing.

    `Endpoint` ids are keyed on verb+path, so two services each serving `/healthz` collapse
    into ONE node, provenanced to whichever file was walked first. Counting endpoints by node
    provenance credited that file and reported the other as short. Attribution follows the
    EXPOSES edge instead, which carries each handler's own file.
    """
    pkg = tmp_path / "app"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in ("one.py", "two.py"):
        (pkg / name).write_text(
            "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n"
            '@router.get("/healthz")\ndef healthz() -> dict:\n    return {}\n',
            encoding="utf-8",
        )

    batch = RepoCodeExtractor().extract(tmp_path)
    endpoints = [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT]
    assert len(endpoints) == 1, "verb+path keying collapses them — that is the premise"

    assert _parity(verify_batch(batch, tmp_path)) == [], (
        "both files declare the route and both expose a handler; neither is short"
    )
