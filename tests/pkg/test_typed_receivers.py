"""Typed-receiver calls in the C# and Java front-ends land on the member the declared type means (B21).

Before this, C# resolved only a sibling call inside one type and Java only a capitalized
receiver, so `_service.Do()` through a DI field — 2,409 of 2,427 resolvable calls in one .NET
service — produced no edge, and `blast_radius` said "0 callers". The corpus cases
`csharp/typed_receivers`, `csharp/typed_receivers_refusals` (and their Java twins) carry the
shapes end to end; these pin each lookup rule on its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import EdgeKind, RepoCodeExtractor

pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")


def _edges(tmp_path: Path, files: dict[str, str], kind: EdgeKind = EdgeKind.CALLS) -> set[tuple[str, str]]:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return {(e.src, e.dst) for e in RepoCodeExtractor().extract(tmp_path).edges if e.kind is kind}


HANDLER = "namespace Lib;\npublic class Handler { public void Run() {} }\n"


def test_a_using_brings_the_receiver_type_and_the_base_into_scope(tmp_path: Path) -> None:
    files = {
        "Lib/Handler.cs": HANDLER,
        "Lib/IPort.cs": "namespace Lib.Ports;\npublic interface IPort { void Open(); }\n",
        "App/Use.cs": (
            "using Lib;\nusing Lib.Ports;\nnamespace App;\n"
            "public class Port : IPort { public void Open() {} }\n"
            "public class Use { private readonly Handler _h; public void Go() => _h.Run(); }\n"
        ),
    }
    assert ("csharp:App.Use.Go", "csharp:Lib.Handler.Run") in _edges(tmp_path, files)
    # the base in a sibling namespace used to become an external `csharp:IPort`
    assert ("csharp:App.Port", "csharp:Lib.Ports.IPort") in _edges(tmp_path, {}, EdgeKind.IMPLEMENTS)


def test_the_namespace_chain_wins_over_a_using(tmp_path: Path) -> None:
    files = {
        "Lib/Handler.cs": HANDLER,
        "App/Handler.cs": "namespace App;\npublic class Handler { public void Run() {} }\n",
        "App/Use.cs": (
            "using Lib;\nnamespace App.Inner;\npublic class Use { public void Go(Handler h) => h.Run(); }\n"
        ),
    }
    calls = _edges(tmp_path, files)
    assert ("csharp:App.Inner.Use.Go", "csharp:App.Handler.Run") in calls
    assert ("csharp:App.Inner.Use.Go", "csharp:Lib.Handler.Run") not in calls


def test_two_usings_bringing_the_same_name_refuse(tmp_path: Path) -> None:
    files = {
        "A/Handler.cs": "namespace A;\npublic class Handler { public void Run() {} }\n",
        "B/Handler.cs": "namespace B;\npublic class Handler { public void Run() {} }\n",
        "App/Use.cs": (
            "using A;\nusing B;\nnamespace App;\npublic class Use { public void Go(Handler h) => h.Run(); }\n"
        ),
    }
    assert not {c for c in _edges(tmp_path, files) if c[0] == "csharp:App.Use.Go"}


def test_global_using_and_alias(tmp_path: Path) -> None:
    files = {
        "Lib/Handler.cs": HANDLER,
        "GlobalUsings.cs": "global using Lib;\n",
        "App/Use.cs": (
            "using H = Lib.Handler;\nnamespace App;\n"
            "public class Use { public void Viaglobal(Handler h) => h.Run();\n"
            "  public void Viaalias(H h) => h.Run(); }\n"
        ),
    }
    calls = _edges(tmp_path, files)
    assert {
        ("csharp:App.Use.Viaglobal", "csharp:Lib.Handler.Run"),
        ("csharp:App.Use.Viaalias", "csharp:Lib.Handler.Run"),
    } <= calls


def test_a_field_of_a_partial_class_declared_in_another_file(tmp_path: Path) -> None:
    files = {
        "Lib/Handler.cs": HANDLER,
        "App/A.cs": "using Lib;\nnamespace App;\npublic partial class Use { private Handler _h; }\n",
        "App/B.cs": "namespace App;\npublic partial class Use { public void Go() => _h.Run(); }\n",
    }
    assert ("csharp:App.Use.Go", "csharp:Lib.Handler.Run") in _edges(tmp_path, files)


def test_a_nested_type_and_a_static_call(tmp_path: Path) -> None:
    files = {
        "App/Use.cs": (
            "namespace App;\npublic class Use {\n"
            "  public class Inner { public void Ping() {} public static int Make() => 1; }\n"
            "  public void Go(Inner i) { i.Ping(); Inner.Make(); }\n}\n"
        ),
    }
    calls = _edges(tmp_path, files)
    assert {
        ("csharp:App.Use.Go", "csharp:App.Use.Inner.Ping"),
        ("csharp:App.Use.Go", "csharp:App.Use.Inner.Make"),
    } <= calls


def test_what_is_out_of_scope_gets_no_edge(tmp_path: Path) -> None:
    files = {
        "Lib/Handler.cs": HANDLER,
        "App/Use.cs": (
            "using Lib;\nnamespace App;\npublic class Use {\n"
            "  private Handler _h;\n"
            "  Handler Make() => new Handler();\n"
            "  public void A() { var h = Make(); h.Run(); }\n"  # a return type — not inferred
            "  public void B() => _h?.Run();\n"  # conditional access — out of scope
            "  public void C(System.Collections.Generic.List<Handler> hs) => hs.ForEach(x => x.Run());\n"
            "  public void D() { var (a, b) = (new Handler(), 1); a.Run(); }\n"  # deconstruction
            "}\n"
        ),
    }
    calls = {c for c in _edges(tmp_path, files) if c[1] == "csharp:Lib.Handler.Run"}
    assert calls == set()


def test_a_query_range_variable_shadows_a_field(tmp_path: Path) -> None:
    files = {
        "Lib/Handler.cs": HANDLER,
        "App/Use.cs": (
            "using Lib;\nusing System.Linq;\nnamespace App;\npublic class Use {\n"
            "  private Handler h;\n"
            "  public void Go(int[] xs) { var q = from h in xs select h.ToString(); }\n}\n"
        ),
    }
    assert ("csharp:App.Use.Go", "csharp:Lib.Handler.ToString") not in _edges(tmp_path, files)
