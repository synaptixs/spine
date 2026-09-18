"""PKG: Blazor `.razor` components enter the graph through the C# front-end (NSS-1209).

`pkg.razor` rewrites a component into line-aligned C# — directives in place, markup blanked,
`@code` opened as a `partial class` — and the C# parser reads the rest. tree-sitter is an
optional extra, so the extraction tests skip cleanly when it is absent; the rewrite itself is
pure Python and always runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, NodeKind
from orchestrator.pkg.razor import component_class_name, component_namespace, razor_to_csharp

GRID = """\
@page "/auctions/products"
@using Commercial.Secondary.Sales.Shared.Enums
@using Commercial.Secondary.Sales.Features.Common.Products.Model
@inject IProductService ProductService

<h3>Products</h3>
<GridColumn Title="Group">@context.ProductDetails!.ProductGroup</GridColumn>

@code {
    private List<Product> _products = new();

    private string DisplayGroup(string code)
    {
        return ProductGroupHelper.GetDisplayNameFromAcronym(code);
    }
}
"""


def _line_of(src: str, needle: str) -> int:
    return next(i for i, line in enumerate(src.splitlines(), 1) if needle in line)


# ---- the rewrite ----------------------------------------------------------------------------


def test_every_line_keeps_its_number() -> None:
    out = razor_to_csharp(GRID, "WebApp/Features/AuctionProductsGrid.razor").splitlines()
    assert len(out) == len(GRID.splitlines())
    assert out[_line_of(GRID, "@using Commercial.Secondary.Sales.Shared.Enums") - 1] == (
        "using Commercial.Secondary.Sales.Shared.Enums;"
    )
    assert out[_line_of(GRID, "@inject") - 1] == (
        "partial class AuctionProductsGrid { IProductService ProductService; }"
    )
    assert out[_line_of(GRID, "@code {") - 1] == "partial class AuctionProductsGrid {"
    assert out[_line_of(GRID, "<h3>") - 1] == ""  # markup is blank, not dropped
    assert out[_line_of(GRID, "@page") - 1] == ""
    assert (
        out[_line_of(GRID, "private string DisplayGroup") - 1]
        == "    private string DisplayGroup(string code)"
    )


def test_a_markup_only_component_still_declares_its_class() -> None:
    out = razor_to_csharp("<h1>Hello</h1>\n<p>@Name</p>\n", "Pages/Banner.razor")
    assert out.splitlines() == ["partial class Banner { }", ""]


def test_a_code_block_with_its_brace_on_the_next_line() -> None:
    src = "@code\n{\n    int X;\n}\n<p/>\n"
    out = razor_to_csharp(src, "A.razor").splitlines()
    assert out == ["partial class A", "{", "    int X;", "}", ""]


def test_namespace_and_class_name() -> None:
    assert component_namespace('@page "/x"\n@namespace My.App.Pages\n') == "My.App.Pages"
    assert component_namespace("<p/>") == ""
    assert component_class_name("Pages/Order-Details.razor") == "Order_Details"
    assert component_class_name("Pages/1Up.razor") == "_1Up"


# ---- extraction through the C# front-end -----------------------------------------------------


def _needs_csharp() -> None:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")


def test_a_component_s_symbols_report_their_true_razor_lines(tmp_path: Path) -> None:
    _needs_csharp()
    rel = "WebApp/Features/AuctionProductsGrid.razor"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(GRID, encoding="utf-8")

    store_batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in store_batch.nodes}

    grid = by_id["csharp:AuctionProductsGrid"]
    assert grid.kind is NodeKind.TYPE and grid.provenance is not None
    assert grid.provenance.file == rel
    # The generated class is the file: markup is its render method's body.
    assert (grid.provenance.line, grid.provenance.end_line) == (1, len(GRID.splitlines()))
    method = by_id["csharp:AuctionProductsGrid.DisplayGroup"]
    assert method.kind is NodeKind.FUNCTION
    assert method.provenance is not None and method.provenance.line == _line_of(
        GRID, "private string DisplayGroup"
    )
    field = by_id["csharp:AuctionProductsGrid._products"]
    assert field.provenance is not None and field.provenance.line == _line_of(GRID, "_products")
    injected = by_id["csharp:AuctionProductsGrid.ProductService"]
    assert injected.kind is NodeKind.FIELD and injected.provenance is not None
    assert injected.provenance.line == _line_of(GRID, "@inject")


def test_a_component_s_usings_are_imports_and_its_module_is_its_path(tmp_path: Path) -> None:
    _needs_csharp()
    rel = "WebApp/Features/AuctionProductsGrid.razor"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(GRID, encoding="utf-8")

    batch = RepoCodeExtractor().extract(tmp_path)
    module_id = f"csharp:{rel}"
    assert any(n.id == module_id and n.kind is NodeKind.MODULE for n in batch.nodes)
    imports = {e.dst for e in batch.edges if e.src == module_id and e.kind is EdgeKind.IMPORTS}
    assert imports == {
        "csharp:Commercial.Secondary.Sales.Shared.Enums",
        "csharp:Commercial.Secondary.Sales.Features.Common.Products.Model",
    }
    # `@inject` and `@code` each open a partial declaration of the component: one containment.
    contains = [
        e
        for e in batch.edges
        if e.src == module_id and e.dst == "csharp:AuctionProductsGrid" and e.kind is EdgeKind.CONTAINS
    ]
    assert len(contains) == 1


def test_a_declared_namespace_qualifies_the_component(tmp_path: Path) -> None:
    _needs_csharp()
    src = "@namespace My.App.Pages\n@code {\n    public int Count;\n}\n"
    (tmp_path / "Counter.razor").write_text(src, encoding="utf-8")

    batch = RepoCodeExtractor().extract(tmp_path)
    ids = {n.id for n in batch.nodes}
    assert "csharp:My.App.Pages" in ids
    assert "csharp:My.App.Pages.Counter" in ids
    assert "csharp:My.App.Pages.Counter.Count" in ids


def test_nss_1209_the_grids_resolve_and_are_not_flagged_absent(tmp_path: Path) -> None:
    """The five render locations of NSS-1209 were invisible; now a design naming them resolves."""
    _needs_csharp()
    from orchestrator.pkg import FactStore
    from orchestrator.sdlc.impact import blast_radius, unverified_references

    for name in ("AuctionProductsGrid", "ProductsGrid"):
        (tmp_path / f"{name}.razor").write_text(GRID, encoding="utf-8")
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    br = blast_radius(store, ["AuctionProductsGrid.razor", "ProductsGrid.razor", "Ghost.razor"])
    assert unverified_references(br) == ["Ghost.razor"]


def test_a_warm_cache_cannot_serve_a_razor_less_graph(tmp_path: Path) -> None:
    """D8: no grammar changed, so nothing in `_GRAMMAR_MODULES` did — the fingerprint must
    still move, and it does, because it hashes every `pkg/` module's source. Measured the
    way it matters: a `pkg/` without `razor.py` — the pre-branch wheel — keys differently."""
    import shutil

    from orchestrator.pkg import persistence

    src = Path(persistence.__file__).parent
    copy = tmp_path / "pkg"
    shutil.copytree(src, copy, ignore=shutil.ignore_patterns("__pycache__"))
    with_razor = persistence.extractor_fingerprint(package_dir=copy)
    (copy / "razor.py").unlink()
    assert persistence.extractor_fingerprint(package_dir=copy) != with_razor


def test_the_invention_oracle_scopes_the_rewrite_not_the_markup(tmp_path: Path) -> None:
    """A component's sibling call is a `CALLS` edge; the oracle must examine it against the
    line-aligned C# the front-end parsed, not raw markup, or every bound name looks invented."""
    _needs_csharp()
    from orchestrator.pkg.invention import MEASURED, find_invented_calls

    src = (
        "<p>@Total()</p>\n@code {\n"
        "    private int Total() { return Sub(); }\n"
        "    private int Sub() { return 1; }\n}\n"
    )
    (tmp_path / "Totals.razor").write_text(src, encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    assert any(e.kind is EdgeKind.CALLS and e.src == "csharp:Totals.Total" for e in batch.edges)

    report = find_invented_calls(batch, tmp_path)
    row = next(r for r in report.by_language if r.language == "csharp")
    assert row.status == MEASURED and row.examined >= 1
    assert row.invented == ()


# ---- what the rewrite refuses to invent (maintainer review of the branch) ---------------------


def test_a_razor_comment_declares_nothing() -> None:
    """Commented-out code — the old block kept for history — used to become grounded nodes."""
    src = "@*\n@namespace Commented.Out\n@code {\n    int Ghost;\n}\n*@\n<p/>\n"
    out = razor_to_csharp(src, "Widget.razor").splitlines()
    assert component_namespace(src) == ""
    assert "Ghost" not in "\n".join(out) and "Commented" not in "\n".join(out)
    assert out[0] == "partial class Widget { }" and len(out) == 7


def test_the_namespace_is_hoisted_so_it_qualifies_every_declaration() -> None:
    """`@page` first, then `@namespace`, then markup — the documented ordering. C# applies a
    file-scoped namespace only to what follows it, so the class must come after it."""
    src = '@page "/x"\n@namespace My.App.Pages\n<h1>Hi</h1>\n'
    out = razor_to_csharp(src, "Widget.razor").splitlines()
    assert out[0] == "namespace My.App.Pages; partial class Widget { }"
    assert out[1] == "" and out[2] == ""

    src = "@inject IFoo Foo\n@namespace My.App.Pages\n@code {\n    int X;\n}\n"
    out = razor_to_csharp(src, "Widget.razor").splitlines()
    assert out[0] == "namespace My.App.Pages; partial class Widget { IFoo Foo; }"


def test_a_brace_in_a_string_or_a_comment_ends_nothing() -> None:
    src = (
        "@code {\n"
        '    private string Close() { return "}"; }\n'
        "    // closes here: }\n"
        "    private int Later() { return 1; }\n"
        "}\n"
        "<div class=card>{ not code }</div>\n"
    )
    out = razor_to_csharp(src, "A.razor").splitlines()
    assert out[3] == "    private int Later() { return 1; }"
    assert out[4] == "}" and out[5] == ""  # the markup after the real close is blank


def test_line_fidelity_survives_characters_splitlines_would_break_on() -> None:
    for src in ("<p/>\n\x0c\n@code {\n    int X;\n}\n", "<p>a\u2028b</p>\n@code {\n    int X;\n}\n"):
        out = razor_to_csharp(src, "A.razor").splitlines()
        assert out[src.split("\n").index("    int X;")] == "    int X;"


def test_a_file_of_only_directives_declares_no_class() -> None:
    """`_Imports.razor`: Blazor generates no class for it, and there is no line to put one on."""
    src = "@using System\n@using My.App\n"
    out = razor_to_csharp(src, "_Imports.razor").splitlines()
    assert out == ["using System;", "using My.App;"]
    src = "@using System\n"  # a component name, but still nothing to declare
    assert razor_to_csharp(src, "Only.razor").splitlines() == ["using System;"]


def test_an_inject_with_a_trailing_comment_keeps_its_brace() -> None:
    out = razor_to_csharp("@inject IFoo Foo // the service\n", "A.razor").splitlines()
    assert out[0] == "partial class A { IFoo Foo; }"


def test_a_code_directive_is_only_the_directive() -> None:
    out = razor_to_csharp("@code.Length\n@if (x) {\n Hello World\n}\n", "A.razor").splitlines()
    assert "Hello" not in "\n".join(out) and out == ["partial class A { }", "", "", ""]


def test_an_imports_file_yields_a_module_and_no_type(tmp_path: Path) -> None:
    _needs_csharp()
    (tmp_path / "_Imports.razor").write_text("@using System\n@using My.App\n", encoding="utf-8")
    batch = RepoCodeExtractor().extract(tmp_path)
    kinds = {n.kind for n in batch.nodes if n.provenance and n.provenance.file.endswith(".razor")}
    assert kinds == {NodeKind.MODULE}
    for n in batch.nodes:
        if n.provenance and n.provenance.file.endswith(".razor"):
            assert n.provenance.line <= 2


def test_a_code_behind_partial_merges_onto_one_type(tmp_path: Path) -> None:
    """`Grid.razor` + `Grid.razor.cs` is one class in the namespace only the second declares."""
    _needs_csharp()
    (tmp_path / "Grid.razor").write_text("@code {\n    int Shown;\n}\n", encoding="utf-8")
    (tmp_path / "Grid.razor.cs").write_text(
        "namespace My.App;\npublic partial class Grid { public int Behind; }\n", encoding="utf-8"
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    types = [n for n in batch.nodes if n.kind is NodeKind.TYPE and n.name == "Grid"]
    assert [t.id for t in types] == ["csharp:My.App.Grid"]
    ids = {n.id for n in batch.nodes}
    assert {"csharp:My.App.Grid.Shown", "csharp:My.App.Grid.Behind"} <= ids


def test_a_body_line_inside_code_has_an_enclosing_symbol(tmp_path: Path) -> None:
    _needs_csharp()
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.retrieval import GroundedRetriever

    rel = "Grid.razor"
    (tmp_path / rel).write_text(GRID, encoding="utf-8")
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    body_line = _line_of(GRID, "return ProductGroupHelper")
    # Members record a start line only (as for every C# member), so the smallest span covering
    # a body line is the component's — the whole file — which is what a diff there needs.
    found = GroundedRetriever(store).enclosing_symbol(rel, body_line)
    assert found is not None and found.name == "Grid"
    markup_line = _line_of(GRID, "<h3>")
    found = GroundedRetriever(store).enclosing_symbol(rel, markup_line)
    assert found is not None and found.name == "Grid"
