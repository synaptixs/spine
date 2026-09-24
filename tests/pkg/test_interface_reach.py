"""An implementation method's blast radius reaches the callers of the member it implements (B21, D9/D14/D15).

A call through an interface lands on the interface's member, so nothing calls the implementation
by name: before this, `blast_radius` on a DI-bound .NET service method said 0 callers although
304 call sites went through its interface. The reach is reported *apart* from direct callers,
each with the member it went through — those callers may reach this implementation, not must.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg import EdgeKind, FactBatch, FactStore
from orchestrator.pkg.facts import Edge, Node, NodeKind, Provenance


def _batch(*, via: EdgeKind = EdgeKind.IMPLEMENTS, chain: bool = False, cycle: bool = False) -> FactBatch:
    b = FactBatch()
    for tid in ("x:IBase", "x:IService", "x:Service", "x:Client"):
        b.add_node(Node(tid, NodeKind.TYPE, tid.split(":")[1], "x", Provenance("a.x", 1)))
    for mid in (
        "x:IBase.Do",
        "x:IService.Do",
        "x:Service.Do",
        "x:Client.Use",
        "x:Client.Direct",
        "x:Service.Other",
    ):
        b.add_node(Node(mid, NodeKind.FUNCTION, mid.rsplit(".", 1)[1], "x", Provenance("a.x", 2)))
        b.add_edge(Edge(mid.rsplit(".", 1)[0], mid, EdgeKind.CONTAINS))
    b.add_edge(Edge("x:Service", "x:IService", via))
    b.add_edge(Edge("x:Client.Use", "x:IService.Do", EdgeKind.CALLS, Provenance("c.x", 7)))
    b.add_edge(Edge("x:Client.Direct", "x:Service.Other", EdgeKind.CALLS, Provenance("c.x", 9)))
    if chain:
        b.add_edge(Edge("x:IService", "x:IBase", EdgeKind.IMPLEMENTS))
        b.add_edge(Edge("x:Client.Use", "x:IBase.Do", EdgeKind.CALLS, Provenance("c.x", 8)))
    if cycle:
        b.add_edge(Edge("x:IService", "x:Service", EdgeKind.IMPLEMENTS))
    return b


def test_an_interface_caller_is_reached_and_labelled_with_its_member() -> None:
    store = FactStore(_batch())
    assert store.callers_of("x:Service.Do") == []
    (site,) = store.interface_callers_of("x:Service.Do")
    assert (site.caller.id, site.at, site.via) == ("x:Client.Use", "c.x:7", "x:IService.Do")


def test_a_di_binding_reaches_the_same_way() -> None:
    (site,) = FactStore(_batch(via=EdgeKind.PROVIDES)).interface_callers_of("x:Service.Do")
    assert site.via == "x:IService.Do"


def test_every_level_up_the_hierarchy_and_cycles_terminate() -> None:
    sites = FactStore(_batch(chain=True, cycle=True)).interface_callers_of("x:Service.Do")
    assert [(s.caller.id, s.via) for s in sites] == [
        ("x:Client.Use", "x:IBase.Do"),
        ("x:Client.Use", "x:IService.Do"),
    ]


def test_impact_includes_it_and_a_method_with_no_interface_member_is_unchanged() -> None:
    store = FactStore(_batch())
    assert "x:Client.Use" in {n.id for n, _ in store.impact_of("x:Service.Do")}
    assert store.interface_callers_of("x:Service.Other") == []
    assert [c.caller.id for c in store.callers_of("x:Service.Other")] == ["x:Client.Direct"]


def test_blast_radius_reports_it_apart_from_direct_callers(tmp_path: Path) -> None:
    import pytest

    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")
    from orchestrator.plugin.server import blast_radius

    (tmp_path / "Svc.cs").write_text(
        "namespace App;\npublic interface IMailer { void Send(); }\n"
        "public class SmtpMailer : IMailer { public void Send() {} }\n"
        "public class Signup { private readonly IMailer _mailer; public void Go() => _mailer.Send(); }\n",
        encoding="utf-8",
    )
    out = blast_radius(repo_path=str(tmp_path), symbol="Send")
    impl = next(m for m in out["matches"] if m["id"] == "csharp:App.SmtpMailer.Send")
    assert impl["caller_count"] == 0
    assert impl["interface_caller_count"] == 1
    assert impl["interface_callers"][0] == {
        "id": "csharp:App.Signup.Go",
        "at": "Svc.cs:4",
        "via": "csharp:App.IMailer.Send",
    }
    assert "Called through an interface (1)" in out["markdown"]
