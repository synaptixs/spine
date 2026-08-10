"""``CONSUMES`` — the client half of the route join.

``EXPOSES`` gave a route its handler. Until this, nothing pointed *at* an endpoint, so a
public route was a leaf: something the server declared that nothing appeared to want.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg import FactStore
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, NodeKind


def _extract(tmp_path: Path, files: dict[str, str]) -> FactStore:
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return FactStore(RepoCodeExtractor().extract(tmp_path))


SERVER = """
from fastapi import APIRouter

router = APIRouter(prefix="/v1")


@router.get("/runs")
def list_runs():
    return []
"""


def test_a_literal_call_joins_to_the_endpoint_it_calls(tmp_path: Path) -> None:
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": 'import httpx\n\n\ndef fetch(c):\n    return c.get("/v1/runs")\n',
        },
    )
    edges = store.edges_of_kind(EdgeKind.CONSUMES)
    assert len(edges) == 1
    assert edges[0].src == "py:client.fetch"
    target = store.node(edges[0].dst)
    assert target is not None and target.name == "GET /v1/runs"


def test_the_endpoint_now_reports_its_client(tmp_path: Path) -> None:
    """`exposers_of` said an endpoint's dependents were 'outside the repo entirely'."""
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": 'import httpx\n\n\ndef fetch(c):\n    return c.get("/v1/runs")\n',
        },
    )
    endpoint = next(n for n in store.nodes if n.kind is NodeKind.ENDPOINT)
    assert [n.name for n in store.consumers_of(endpoint.id)] == ["fetch"]


def test_changing_a_handler_reaches_the_client_that_calls_it(tmp_path: Path) -> None:
    """The join is useless if the walk stops at the endpoint."""
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": 'import httpx\n\n\ndef fetch(c):\n    return c.get("/v1/runs")\n',
        },
    )
    handler = next(n for n in store.nodes if n.name == "list_runs")
    reached = {n.name for n, _ in store.impact_of(handler.id)}
    assert "fetch" in reached


def test_a_computed_path_yields_nothing(tmp_path: Path) -> None:
    """A wrong edge is worse than an absent one — an f-string is not a known path."""
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": 'import httpx\n\n\ndef fetch(c, kind):\n    return c.get(f"/v1/{kind}")\n',
        },
    )
    assert store.edges_of_kind(EdgeKind.CONSUMES) == []


def test_a_call_to_an_endpoint_this_repo_does_not_serve_yields_nothing(tmp_path: Path) -> None:
    store = _extract(
        tmp_path,
        {"client.py": 'import httpx\n\n\ndef fetch():\n    return httpx.get("https://example.com/v1/x")\n'},
    )
    assert store.edges_of_kind(EdgeKind.CONSUMES) == []


def test_a_dict_lookup_is_not_an_http_call(tmp_path: Path) -> None:
    """Without the import gate, `config.get("/default")` reads as traffic that never happens."""
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": 'CONFIG = {}\n\n\ndef fetch():\n    return CONFIG.get("/v1/runs")\n',
        },
    )
    assert store.edges_of_kind(EdgeKind.CONSUMES) == []


def test_a_full_url_is_matched_on_its_path(tmp_path: Path) -> None:
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": (
                'import httpx\n\n\ndef fetch():\n    return httpx.get("http://localhost:8000/v1/runs")\n'
            ),
        },
    )
    assert len(store.edges_of_kind(EdgeKind.CONSUMES)) == 1


def test_verb_as_an_argument_is_read(tmp_path: Path) -> None:
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": 'import httpx\n\n\ndef fetch(c):\n    return c.request("GET", "/v1/runs")\n',
        },
    )
    assert len(store.edges_of_kind(EdgeKind.CONSUMES)) == 1


def test_the_wrong_verb_does_not_match(tmp_path: Path) -> None:
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": 'import httpx\n\n\ndef fetch(c):\n    return c.post("/v1/runs")\n',
        },
    )
    assert store.edges_of_kind(EdgeKind.CONSUMES) == []


def test_a_call_inside_a_with_block_is_attributed_to_the_enclosing_function(tmp_path: Path) -> None:
    """`with _client() as c: _check(c.get("/x"))` is the shape this repo actually uses."""
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": (
                "import httpx\n\n\ndef fetch():\n"
                "    with httpx.Client() as c:\n"
                '        return check(c.get("/v1/runs"))\n'
            ),
        },
    )
    edges = store.edges_of_kind(EdgeKind.CONSUMES)
    assert [e.src for e in edges] == ["py:client.fetch"]


def test_trailing_slashes_do_not_split_the_join(tmp_path: Path) -> None:
    store = _extract(
        tmp_path,
        {
            "server.py": SERVER,
            "client.py": 'import httpx\n\n\ndef fetch(c):\n    return c.get("/v1/runs/")\n',
        },
    )
    assert len(store.edges_of_kind(EdgeKind.CONSUMES)) == 1
