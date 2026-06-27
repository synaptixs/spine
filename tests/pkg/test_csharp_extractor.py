"""PKG: the C# front-end maps C# source onto the universal facts (Track 1).

tree-sitter is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.csharp_extractor import CSharpExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind

pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")

INVOICE = """\
using System;
using System.Collections.Generic;

namespace Billing.Core
{
    public interface IRepository { }

    public class InvoiceService : BaseService, IRepository
    {
        private readonly int _count;
        public string Name { get; set; }

        public InvoiceService(int count) { _count = count; }
        public decimal Total(Invoice inv) { return inv.Amount; }

        public enum Status { Open, Paid }
    }

    public record Money(decimal Amount, string Currency);
}
"""


def _facts(tmp_path: Path, src: str = INVOICE, name: str = "Invoice.cs") -> tuple[FactBatch, str]:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    ex = CSharpExtractor()
    module = ex.module_name(f, tmp_path)
    return ex.extract(path=f, module=module, rel=f"src/{name}"), module


def test_module_name_is_the_namespace(tmp_path: Path) -> None:
    _, module = _facts(tmp_path)
    assert module == "Billing.Core"


def test_emits_type_method_field_nodes(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["csharp:Billing.Core.InvoiceService"].kind is NodeKind.TYPE
    assert by_id["csharp:Billing.Core.IRepository"].kind is NodeKind.TYPE
    # method + constructor are Functions
    assert by_id["csharp:Billing.Core.InvoiceService.Total"].kind is NodeKind.FUNCTION
    assert by_id["csharp:Billing.Core.InvoiceService.InvoiceService"].kind is NodeKind.FUNCTION
    # field + property + enum member are Fields
    assert by_id["csharp:Billing.Core.InvoiceService._count"].kind is NodeKind.FIELD
    assert by_id["csharp:Billing.Core.InvoiceService.Name"].kind is NodeKind.FIELD
    assert by_id["csharp:Billing.Core.InvoiceService.Status.Open"].kind is NodeKind.FIELD
    # nested enum is a Type; record is a Type with positional params as Fields
    assert by_id["csharp:Billing.Core.InvoiceService.Status"].kind is NodeKind.TYPE
    assert by_id["csharp:Billing.Core.Money"].kind is NodeKind.TYPE
    assert by_id["csharp:Billing.Core.Money.Amount"].kind is NodeKind.FIELD


def test_imports_and_contains_edges(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("csharp:Billing.Core", "csharp:System", EdgeKind.IMPORTS) in edges
    assert (
        "csharp:Billing.Core.InvoiceService",
        "csharp:Billing.Core.InvoiceService.Total",
        EdgeKind.CONTAINS,
    ) in edges


def test_implements_resolves_same_namespace(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    # both base entries resolve to same-namespace ids; IRepository lands on the real node
    assert ("csharp:Billing.Core.InvoiceService", "csharp:Billing.Core.IRepository") in impls
    assert ("csharp:Billing.Core.InvoiceService", "csharp:Billing.Core.BaseService") in impls


def test_does_not_emit_calls(tmp_path: Path) -> None:
    # Precision-first: C# call resolution needs overload/type inference, so no CALLS yet.
    batch, _ = _facts(tmp_path)
    assert not [e for e in batch.edges if e.kind is EdgeKind.CALLS]


def test_repo_extractor_dispatches_csharp_by_suffix(tmp_path: Path) -> None:
    from orchestrator.pkg.extractor import PythonExtractor, RepoCodeExtractor

    (tmp_path / "A.cs").write_text(INVOICE, encoding="utf-8")
    (tmp_path / "m.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    batch = RepoCodeExtractor([PythonExtractor(), CSharpExtractor()]).extract(tmp_path)
    langs = {n.language for n in batch.nodes}
    assert "csharp" in langs and "python" in langs


def test_file_scoped_namespace(tmp_path: Path) -> None:
    src = "namespace App;\n\npublic class Widget { public void Render() { } }\n"
    batch, module = _facts(tmp_path, src, name="Widget.cs")
    assert module == "App"
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["csharp:App.Widget"].kind is NodeKind.TYPE
    assert by_id["csharp:App.Widget.Render"].kind is NodeKind.FUNCTION


def test_unnamespaced_file_falls_back_to_path(tmp_path: Path) -> None:
    _, module = _facts(tmp_path, "public class Bare { }\n", name="Bare.cs")
    assert module == "Bare.cs"


WALLET = """\
namespace Pay.Domain
{
    public delegate decimal Rate(decimal amount);

    public partial class Wallet<T> : BaseWallet<T> where T : class
    {
        public event System.EventHandler? Changed;
        public event System.EventHandler Added, Removed;
        public static Wallet<T> operator +(Wallet<T> a, Wallet<T> b) => a;
        public void Add(T item) { }
    }

    public partial class Wallet<T> { public void Remove(T item) { } }
}
"""


def test_delegates_events_operators_and_partial_merge(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, WALLET, name="Wallet.cs")
    by_id = {n.id: n for n in batch.nodes}
    # delegate is a (leaf) Type
    assert by_id["csharp:Pay.Domain.Rate"].kind is NodeKind.TYPE
    # events → Field (including a multi-declarator event line)
    assert by_id["csharp:Pay.Domain.Wallet.Changed"].kind is NodeKind.FIELD
    assert by_id["csharp:Pay.Domain.Wallet.Added"].kind is NodeKind.FIELD
    assert by_id["csharp:Pay.Domain.Wallet.Removed"].kind is NodeKind.FIELD
    # operator → Function
    assert by_id["csharp:Pay.Domain.Wallet.operator+"].kind is NodeKind.FUNCTION
    # generic name is clean ("Wallet", not "Wallet<T>") and the two `partial` halves
    # merge onto one Type carrying both methods
    assert by_id["csharp:Pay.Domain.Wallet"].kind is NodeKind.TYPE
    assert "csharp:Pay.Domain.Wallet.Add" in by_id
    assert "csharp:Pay.Domain.Wallet.Remove" in by_id


def test_delegate_has_no_spurious_fields(tmp_path: Path) -> None:
    # a delegate's parameters must NOT be emitted as Fields (it's a leaf type).
    batch, _ = _facts(tmp_path, WALLET, name="Wallet.cs")
    assert not [n for n in batch.nodes if n.id.startswith("csharp:Pay.Domain.Rate.")]


def test_bom_and_file_scoped_namespace_qualifies_ids(tmp_path: Path) -> None:
    # .NET files often start with a UTF-8 BOM and use a file-scoped namespace; the
    # type id must still be namespace-qualified, never leaked to the file path.
    src = "﻿namespace MediatR;\n\npublic readonly struct Unit { }\n"
    batch, module = _facts(tmp_path, src, name="Unit.cs")
    assert module == "MediatR"
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["csharp:MediatR.Unit"].kind is NodeKind.TYPE
    assert not [n for n in batch.nodes if ".cs." in n.id]  # no path-leaked ids


def test_indexer_is_a_field(tmp_path: Path) -> None:
    src = "namespace N { public class Bag { public int this[int i] { get => 0; set { } } } }\n"
    batch, _ = _facts(tmp_path, src, name="Bag.cs")
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["csharp:N.Bag.this[]"].kind is NodeKind.FIELD


# --- Phase 1.3: framework + call edges -------------------------------------

CONTROLLER = """\
namespace Shop.Api;

using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet("{id}")]
    public Order GetById(int id) { return Find(id); }

    [HttpPost]
    public void Create([FromBody] Order o) { Save(o); }

    private Order Find(int id) { return this.Lookup(id); }
    private Order Lookup(int id) { return null; }
    private void Save(Order o) { }
}
"""

ENTITIES = """\
namespace Shop.Data;

public class ShopContext : DbContext
{
    public DbSet<Order> Orders { get; set; }
    public DbSet<Customer> Customers { get; set; }
}

[Table("orders")]
public class Order
{
    public int Id { get; set; }
    public Customer Customer { get; set; }
    public List<LineItem> Items { get; set; }
}

public class Customer { public int Id { get; set; } }

[Table("line_items")]
public class LineItem { public int Id { get; set; } }
"""

MINIMAL_API = """\
var app = builder.Build();
app.MapGet("/health", () => "ok");
app.MapPost("/orders", (Order o) => Save(o));
app.Run();
"""


def test_controller_endpoints_expose_handlers(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, CONTROLLER, name="OrdersController.cs")
    by_id = {n.id: n for n in batch.nodes}
    # class [Route] prefix is joined with the method route; nodes are Endpoints.
    get_id = "csharp:endpoint:GET /api/[controller]/{id}"
    post_id = "csharp:endpoint:POST /api/[controller]"
    assert by_id[get_id].kind is NodeKind.ENDPOINT
    assert by_id[post_id].kind is NodeKind.ENDPOINT
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert (get_id, "csharp:Shop.Api.OrdersController.GetById", EdgeKind.EXPOSES) in edges
    assert (post_id, "csharp:Shop.Api.OrdersController.Create", EdgeKind.EXPOSES) in edges


ROUTE_SPLIT_CONTROLLER = """\
namespace App.Web;

public class SearchController : BaseController
{
    [Route("api/getInfo")]
    [HttpGet]
    public IActionResult GetInfo(string login) { return null; }

    [Route("api/saveThing")]
    [HttpPost]
    public IActionResult SaveThing() { return null; }

    [Route("api/anyVerb")]
    public IActionResult AnyVerb() { return null; }
}
"""


def test_method_level_route_attribute_distinguishes_endpoints(tmp_path: Path) -> None:
    # The common ASP.NET pattern: a separate method-level [Route("api/x")] carries the
    # path and [HttpGet]/[HttpPost] carries only the verb (no class [Route], no route
    # arg on the verb attr). Each must become a DISTINCT endpoint — not collapse to "/".
    batch, _ = _facts(tmp_path, ROUTE_SPLIT_CONTROLLER, name="SearchController.cs")
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["csharp:endpoint:GET /api/getInfo"].kind is NodeKind.ENDPOINT
    assert by_id["csharp:endpoint:POST /api/saveThing"].kind is NodeKind.ENDPOINT
    # a [Route]-only action (no verb attr) on a controller → ANY.
    assert by_id["csharp:endpoint:ANY /api/anyVerb"].kind is NodeKind.ENDPOINT
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    base = "csharp:App.Web.SearchController"
    assert ("csharp:endpoint:GET /api/getInfo", f"{base}.GetInfo", EdgeKind.EXPOSES) in edges
    assert ("csharp:endpoint:POST /api/saveThing", f"{base}.SaveThing", EdgeKind.EXPOSES) in edges


def test_route_only_method_on_plain_class_is_not_an_endpoint(tmp_path: Path) -> None:
    # precision-first: a [Route] on a non-controller class must NOT become an endpoint.
    src = 'namespace N { public class Helper { [Route("x")] public int F() { return 0; } } }\n'
    batch, _ = _facts(tmp_path, src, name="Helper.cs")
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENDPOINT]


def test_intra_type_calls_resolve_to_siblings(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, CONTROLLER, name="OrdersController.cs")
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    base = "csharp:Shop.Api.OrdersController"
    assert (f"{base}.GetById", f"{base}.Find") in calls  # unqualified call
    assert (f"{base}.Find", f"{base}.Lookup") in calls  # this.-qualified call
    assert (f"{base}.Create", f"{base}.Save") in calls


def test_no_calls_to_unknown_methods(tmp_path: Path) -> None:
    # precision-first: a call to a method not declared in the same type is dropped.
    src = "namespace N { public class C { void F() { External(1); } } }\n"
    batch, _ = _facts(tmp_path, src, name="C.cs")
    assert not [e for e in batch.edges if e.kind is EdgeKind.CALLS]


def test_ef_entities_and_references(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, ENTITIES, name="Shop.cs")
    by_id = {n.id: n for n in batch.nodes}
    # DbSet<T> + [Table] both register entities; Customer is reached via DbSet only.
    assert by_id["csharp:entity:Shop.Data.Order"].kind is NodeKind.ENTITY
    assert by_id["csharp:entity:Shop.Data.Customer"].kind is NodeKind.ENTITY
    assert by_id["csharp:entity:Shop.Data.LineItem"].kind is NodeKind.ENTITY
    refs = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}
    # navigation properties (scalar + collection element) become entity→entity edges.
    assert ("csharp:entity:Shop.Data.Order", "csharp:entity:Shop.Data.Customer") in refs
    assert ("csharp:entity:Shop.Data.Order", "csharp:entity:Shop.Data.LineItem") in refs


def test_non_entity_class_emits_no_entity_node(tmp_path: Path) -> None:
    # a plain class with no [Table] and not in any DbSet<T> is not an entity.
    src = "namespace N { public class Plain { public int X { get; set; } } }\n"
    batch, _ = _facts(tmp_path, src, name="Plain.cs")
    assert not [n for n in batch.nodes if n.kind is NodeKind.ENTITY]


def test_minimal_api_endpoints_expose_module(tmp_path: Path) -> None:
    batch, module = _facts(tmp_path, MINIMAL_API, name="Program.cs")
    by_id = {n.id: n for n in batch.nodes}
    get_id = "csharp:endpoint:GET /health"
    post_id = "csharp:endpoint:POST /orders"
    assert by_id[get_id].kind is NodeKind.ENDPOINT
    assert by_id[post_id].kind is NodeKind.ENDPOINT
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    # no named handler for a lambda → EXPOSES points at the module the route lives in.
    assert (get_id, f"csharp:{module}", EdgeKind.EXPOSES) in edges
    assert (post_id, f"csharp:{module}", EdgeKind.EXPOSES) in edges
