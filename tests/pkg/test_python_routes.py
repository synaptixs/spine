"""Python routes → ``Endpoint`` + ``EXPOSES``.

Two things are being pinned: that real routes are found across the frameworks, and that
*computed* ones are not. The second matters more. A missing endpoint is a gap; a wrong one is
a grounded-looking lie, and every surface downstream presents it as fact.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from orchestrator.pkg.extractor import PythonExtractor, RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind


def _extract(tmp_path: Path, files: dict[str, str]) -> tuple[list[Node], list[Edge]]:
    """Write ``files`` into a repo and run the whole-repo walk (so ``finalize`` runs)."""
    for name, source in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    endpoints = [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT]
    exposes = [e for e in batch.edges if e.kind is EdgeKind.EXPOSES]
    return endpoints, exposes


def _names(endpoints: list[Node]) -> set[str]:
    return {n.name for n in endpoints}


# ---- FastAPI ---------------------------------------------------------------


def test_router_prefix_and_mount_compose_across_files(tmp_path: Path) -> None:
    """The whole point of deferring emission: the mount lives in another file."""
    endpoints, exposes = _extract(
        tmp_path,
        {
            "runs.py": (
                "from fastapi import APIRouter\n\n"
                'router = APIRouter(prefix="/runs")\n\n\n'
                '@router.get("/{run_id}")\n'
                "async def get_run(run_id: str) -> dict:\n    return {}\n"
            ),
            "app.py": (
                "from fastapi import FastAPI\n\nimport runs\n\n"
                "app = FastAPI()\n"
                'app.include_router(runs.router, prefix="/v1")\n'
            ),
        },
    )
    assert _names(endpoints) == {"GET /v1/runs/{run_id}"}
    assert [e.dst for e in exposes] == ["py:runs.get_run"]


def test_a_router_mounted_twice_yields_both_paths(tmp_path: Path) -> None:
    """Two mounts is two real paths to the same handler — not a duplicate to collapse."""
    endpoints, exposes = _extract(
        tmp_path,
        {
            "api.py": (
                "from fastapi import APIRouter\n\n"
                "router = APIRouter()\n\n\n"
                '@router.post("/ping")\n'
                "def ping() -> None:\n    return None\n"
            ),
            "app.py": (
                "from fastapi import FastAPI\n\nimport api\n\n"
                "app = FastAPI()\n"
                'app.include_router(api.router, prefix="/v1")\n'
                'app.include_router(api.router, prefix="/v2")\n'
            ),
        },
    )
    assert _names(endpoints) == {"POST /v1/ping", "POST /v2/ping"}
    assert len(exposes) == 2


def test_an_unmounted_router_still_emits_at_its_local_path(tmp_path: Path) -> None:
    """Settled by the spec: a partially-known path beats no inbound edge at all, because
    a handler with no edge is what makes ``impact_of`` call a public route safe to change."""
    endpoints, exposes = _extract(
        tmp_path,
        {
            "solo.py": (
                "from fastapi import APIRouter\n\n"
                'router = APIRouter(prefix="/things")\n\n\n'
                '@router.delete("/{tid}")\n'
                "def drop(tid: str) -> None:\n    return None\n"
            )
        },
    )
    assert _names(endpoints) == {"DELETE /things/{tid}"}
    assert len(exposes) == 1


def test_routes_declared_inside_an_app_factory_are_found(tmp_path: Path) -> None:
    """``def create_app(): @app.get(...)`` — 12 of this repo's 77 routes live here."""
    endpoints, exposes = _extract(
        tmp_path,
        {
            "factory.py": (
                "from fastapi import FastAPI\n\n\n"
                "def create_app() -> FastAPI:\n"
                "    app = FastAPI()\n\n"
                '    @app.get("/healthz")\n'
                "    async def healthz() -> dict:\n        return {}\n\n"
                "    return app\n"
            )
        },
    )
    assert _names(endpoints) == {"GET /healthz"}
    # The handler id must match the front-end's nesting scheme, or EXPOSES dangles.
    assert [e.dst for e in exposes] == ["py:factory.create_app.healthz"]


# ---- Flask -----------------------------------------------------------------


def test_flask_blueprint_prefix_and_methods(tmp_path: Path) -> None:
    endpoints, _ = _extract(
        tmp_path,
        {
            "views.py": (
                "from flask import Blueprint\n\n"
                'bp = Blueprint("bp", __name__, url_prefix="/admin")\n\n\n'
                '@bp.route("/users", methods=["POST", "PUT"])\n'
                "def save_user():\n    return ''\n"
            )
        },
    )
    assert _names(endpoints) == {"POST /admin/users", "PUT /admin/users"}


def test_flask_route_without_methods_defaults_to_get(tmp_path: Path) -> None:
    endpoints, _ = _extract(
        tmp_path,
        {
            "v.py": (
                "from flask import Flask\n\napp = Flask(__name__)\n\n\n"
                "@app.route('/x')\ndef x():\n    return ''\n"
            )
        },
    )
    assert _names(endpoints) == {"GET /x"}


# ---- Django ----------------------------------------------------------------


def test_django_urlconf_binds_the_view(tmp_path: Path) -> None:
    """No verb is knowable at a URL conf, so the endpoint says ``ANY`` rather than guessing."""
    endpoints, exposes = _extract(
        tmp_path,
        {
            "views.py": "def list_runs(request):\n    return None\n",
            "urls.py": (
                "from django.urls import path\n\nimport views\n\n"
                'urlpatterns = [path("runs/", views.list_runs)]\n'
            ),
        },
    )
    assert _names(endpoints) == {"ANY /runs"}
    assert [e.dst for e in exposes] == ["py:views.list_runs"]


def test_path_outside_a_urls_file_is_not_a_route(tmp_path: Path) -> None:
    """``path`` is far too common a name to treat as a route anywhere else."""
    endpoints, _ = _extract(
        tmp_path,
        {"helper.py": ('from pathlib import path\n\ndef build():\n    return path("runs/", build)\n')},
    )
    assert endpoints == []


def test_class_based_views_are_skipped(tmp_path: Path) -> None:
    """``TemplateView.as_view()`` names no function, so there is nothing to bind to."""
    endpoints, _ = _extract(
        tmp_path,
        {
            "urls.py": (
                "from django.urls import path\n\nfrom django.views.generic import TemplateView\n\n"
                'urlpatterns = [path("home/", TemplateView.as_view())]\n'
            )
        },
    )
    assert endpoints == []


# ---- precision: resolve what is literal, skip what is computed --------------


@pytest.mark.parametrize(
    "decorator",
    [
        '@router.get(f"/runs/{PREFIX}")',  # f-string
        "@router.get(PATH)",  # name
        '@router.get("/runs/" + suffix)',  # partly computed concat
    ],
)
def test_a_computed_path_emits_no_endpoint(decorator: str, tmp_path: Path) -> None:
    endpoints, exposes = _extract(
        tmp_path,
        {
            "m.py": (
                "from fastapi import APIRouter\n\n"
                'PREFIX = "x"\nPATH = "/y"\nsuffix = "z"\n'
                "router = APIRouter()\n\n\n"
                f"{decorator}\n"
                "def handler():\n    return None\n"
            )
        },
    )
    assert endpoints == [] and exposes == []


def test_a_literal_concatenation_does_resolve(tmp_path: Path) -> None:
    """Two literals joined is still literal — skipping it would be over-caution."""
    endpoints, _ = _extract(
        tmp_path,
        {
            "m.py": (
                "from fastapi import APIRouter\n\n"
                "router = APIRouter()\n\n\n"
                '@router.get("/a" + "/b")\n'
                "def handler():\n    return None\n"
            )
        },
    )
    assert _names(endpoints) == {"GET /a/b"}


def test_a_computed_methods_list_emits_nothing(tmp_path: Path) -> None:
    """Defaulting a computed ``methods=`` to GET would invent a route that may not exist."""
    endpoints, _ = _extract(
        tmp_path,
        {
            "m.py": (
                "from flask import Flask\n\n"
                "app = Flask(__name__)\nVERBS = ['GET']\n\n\n"
                '@app.route("/x", methods=VERBS)\n'
                "def handler():\n    return ''\n"
            )
        },
    )
    assert endpoints == []


# ---- state hygiene ---------------------------------------------------------


def test_finalize_clears_state_between_walks(tmp_path: Path) -> None:
    """One extractor instance, two walks: the second must not re-emit the first's routes."""
    (tmp_path / "m.py").write_text(
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n"
        '@router.get("/x")\ndef h():\n    return None\n',
        encoding="utf-8",
    )
    extractor = PythonExtractor()
    repo = RepoCodeExtractor([extractor])
    first = repo.extract(tmp_path)
    second = repo.extract(tmp_path)

    def count(batch: FactBatch) -> int:
        return len([n for n in batch.nodes if n.kind is NodeKind.ENDPOINT])

    assert count(first) == count(second) == 1


# ---- the exit criterion, measured on this repo -----------------------------


def test_every_declared_route_in_this_repo_has_an_inbound_exposes() -> None:
    """The gap this track exists to close, asserted against the real source tree.

    Self-adjusting rather than a fixed count: it re-reads the decorators from `src/`, so it
    keeps holding as routes are added. A handler that loses its edge is exactly the failure
    that made `impact_of` report a public endpoint as safe to change.
    """
    root = Path(__file__).resolve().parents[2]
    verbs = {"get", "post", "put", "patch", "delete", "head", "options", "trace", "route"}
    declared: list[str] = []
    for path in (root / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in verbs
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    declared.append(node.name)

    batch = RepoCodeExtractor().extract(root / "src")
    exposed = {e.dst for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    missing = [name for name in declared if not any(i.endswith(f".{name}") for i in exposed)]

    assert declared, "no decorator-shaped routes found — the probe stopped working"
    assert not missing, f"{len(missing)} of {len(declared)} handlers have no inbound EXPOSES: {missing[:5]}"
