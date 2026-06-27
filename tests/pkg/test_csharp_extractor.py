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
