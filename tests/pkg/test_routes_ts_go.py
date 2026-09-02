"""Express and Gin routes → `Endpoint` nodes.

Before these, **only Java and C# emitted `Endpoint` among the tree-sitter front-ends**. Two
consequences, and the second is the larger one: a route handler had zero callers, so
`impact_of` called a public endpoint safe to refactor; and the multi-repo `http` joiner matches
against the *provider's* endpoints, so a Node or Go service could not be a provider at all.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, NodeKind

EXPRESS = """
import express from "express";
const app = express();
const v1 = express.Router();
export function listOrders(req: any, res: any): string { return "ok"; }
v1.get("/orders", listOrders);
v1.post("/orders", (req: any, res: any) => { return 1; });
app.use("/v1", v1);
app.get("/health", listOrders);
const base = "/dyn";
app.get(`${base}/thing`, listOrders);
"""

GIN = """package main

import "github.com/gin-gonic/gin"

func listOrders(c *gin.Context) {}

func main() {
\tr := gin.Default()
\tv1 := r.Group("/v1")
\tv1.GET("/orders", listOrders)
\tv1.POST("/orders", func(c *gin.Context) {})
\tr.GET("/health", listOrders)
\tpath := "/dyn"
\tr.GET(path, listOrders)
}
"""


def _graph(tmp_path: Path, name: str, body: str) -> tuple[set[str], set[tuple[str, str]]]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    if name.endswith(".go"):
        (repo / "go.mod").write_text("module example.com/svc\n\ngo 1.22\n", encoding="utf-8")
    (repo / name).write_text(body, encoding="utf-8")
    batch = RepoCodeExtractor().extract(repo)
    endpoints = {n.id for n in batch.nodes if n.kind is NodeKind.ENDPOINT}
    exposes = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.EXPOSES}
    return endpoints, exposes


def test_express_composes_a_mounted_route(tmp_path: Path) -> None:
    """`app.use("/v1", v1)` is in a different statement from `v1.get("/orders")`."""
    endpoints, _ = _graph(tmp_path, "server.ts", EXPRESS)
    assert "ts:endpoint:GET /v1/orders" in endpoints
    assert "ts:endpoint:POST /v1/orders" in endpoints


def test_express_leaves_an_unmounted_route_at_its_local_path(tmp_path: Path) -> None:
    """Dropping it restores the false negative this exists to kill: a handler with no edge."""
    endpoints, _ = _graph(tmp_path, "server.ts", EXPRESS)
    assert "ts:endpoint:GET /health" in endpoints


def test_express_refuses_a_computed_path(tmp_path: Path) -> None:
    """A template literal yields nothing. A wrong path is presented as grounded downstream."""
    endpoints, _ = _graph(tmp_path, "server.ts", EXPRESS)
    assert not [e for e in endpoints if "dyn" in e or "thing" in e]
    assert len(endpoints) == 3


def test_an_inline_handler_gets_an_endpoint_but_no_exposes(tmp_path: Path) -> None:
    """The dominant Express shape has no named symbol. The route is still a fact.

    Emitting `EXPOSES` anyway would need an invented handler id — the fabrication this graph
    refuses — while dropping the endpoint would lose the thing the joiner matches on.
    """
    endpoints, exposes = _graph(tmp_path, "server.ts", EXPRESS)
    assert "ts:endpoint:POST /v1/orders" in endpoints
    assert not [dst for src, dst in exposes if src == "ts:endpoint:POST /v1/orders"]
    assert ("ts:endpoint:GET /v1/orders", "ts:server.listOrders") in exposes


def test_gin_composes_a_group_prefix(tmp_path: Path) -> None:
    """`v1 := r.Group("/v1")` then `v1.GET("/orders")`, resolved in source order."""
    endpoints, exposes = _graph(tmp_path, "main.go", GIN)
    assert "go:endpoint:GET /v1/orders" in endpoints
    assert "go:endpoint:POST /v1/orders" in endpoints
    assert ("go:endpoint:GET /v1/orders", "go:<root>.listOrders") in exposes


def test_gin_refuses_a_path_held_in_a_variable(tmp_path: Path) -> None:
    endpoints, _ = _graph(tmp_path, "main.go", GIN)
    assert not [e for e in endpoints if "dyn" in e]
    assert len(endpoints) == 3


def test_net_http_handlefunc_yields_nothing(tmp_path: Path) -> None:
    """Deliberate, per the spec's D2: it registers a path with **no verb**.

    The joiner matches on verb equality, so an `ANY` endpoint would join to everything.
    """
    body = (
        'package main\n\nimport "net/http"\n\n'
        "func handle(w http.ResponseWriter, r *http.Request) {}\n\n"
        'func main() {\n\thttp.HandleFunc("/legacy", handle)\n}\n'
    )
    endpoints, _ = _graph(tmp_path, "main.go", body)
    assert endpoints == set()
