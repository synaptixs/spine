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
            "  public void E() => _h.Run();\n"  # the control: the same member does resolve
            "}\n"
        ),
    }
    calls = {c for c in _edges(tmp_path, files) if c[1] == "csharp:Lib.Handler.Run"}
    assert calls == {("csharp:App.Use.E", "csharp:Lib.Handler.Run")}


def test_a_query_range_variable_shadows_a_field(tmp_path: Path) -> None:
    files = {
        "Lib/Handler.cs": HANDLER,
        "App/Use.cs": (
            "using Lib;\nusing System.Linq;\nnamespace App;\npublic class Use {\n"
            "  private Handler h;\n"
            "  public void Go(Handler[] xs) { var q = from h in xs select h.Run(); }\n"
            "  public void Field() => h.Run();\n}\n"
        ),
    }
    calls = _edges(tmp_path, files)
    # `h` in the query is the range variable — it resolves nowhere, not through the field
    assert ("csharp:App.Use.Go", "csharp:Lib.Handler.Run") not in calls
    assert ("csharp:App.Use.Field", "csharp:Lib.Handler.Run") in calls  # the field itself does


# ---- Java -----------------------------------------------------------------------------------


def _java(tmp_path: Path, files: dict[str, str], kind: EdgeKind = EdgeKind.CALLS) -> set[tuple[str, str]]:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    return _edges(tmp_path, files, kind)


def test_java_interface_extends_is_an_implements_edge(tmp_path: Path) -> None:
    files = {
        "a/Named.java": "package a;\npublic interface Named { String name(); }\n",
        "a/Store.java": "package a;\npublic interface Store extends Named { int load(); }\n",
    }
    assert ("java:a.Store", "java:a.Named") in _java(tmp_path, files, EdgeKind.IMPLEMENTS)


def test_java_an_override_is_not_a_tie(tmp_path: Path) -> None:
    files = {
        "a/P.java": (
            "package a;\ninterface Proto { int settings(); }\n"
            "abstract class Base implements Proto { public int settings() { return 1; } }\n"
            "class X extends Base implements Proto { }\n"
            "class Use { int go(X x) { return x.settings(); } }\n"
        ),
    }
    calls = _java(tmp_path, files)
    assert ("java:a.Use.go", "java:a.Base.settings") in calls
    assert ("java:a.Use.go", "java:a.Proto.settings") not in calls


def test_java_an_inherited_member_type_and_an_enclosing_field(tmp_path: Path) -> None:
    files = {
        "a/Session.java": "package a;\npublic interface Session { interface Listener { void closed(); } }\n",
        "b/Impl.java": (
            "package b;\nimport a.Session;\n"
            "public class Impl implements Session {\n"
            "  private Listener main;\n"
            "  void fire(Listener l) { l.closed(); }\n"
            "  class Inner { void go() { main.closed(); } }\n}\n"
        ),
    }
    calls = _java(tmp_path, files)
    assert {
        ("java:b.Impl.fire", "java:a.Session.Listener.closed"),
        ("java:b.Impl.Inner.go", "java:a.Session.Listener.closed"),
    } <= calls


def test_java_a_method_sharing_a_fields_id_refuses(tmp_path: Path) -> None:
    files = {
        "a/Lazy.java": (
            "package a;\npublic class Lazy {\n  private int length;\n  public int length() { return 0; }\n}\n"
        ),
        "a/Use.java": "package a;\nclass Use { int go(Lazy z) { return z.length(); } }\n",
    }
    assert ("java:a.Use.go", "java:a.Lazy.length") not in _java(tmp_path, files)


def test_java_a_union_catch_parameter_refuses(tmp_path: Path) -> None:
    files = {
        "a/Rocket.java": "package a;\npublic class Rocket extends Error { public void run() {} }\n",
        "a/Car.java": "package a;\npublic class Car extends Error { public void run() {} }\n",
        "a/Use.java": (
            "package a;\nclass Use { Rocket e;\n"
            "  void go() { try { } catch (Car | IllegalStateException e) { e.run(); } }\n"
            "  void one() { try { } catch (Car e) { e.run(); } } }\n"
        ),
    }
    calls = _java(tmp_path, files)
    # a union's type is their least upper bound — never the field's type, never the first alternative
    assert not {c for c in calls if c[0] == "java:a.Use.go"}
    assert ("java:a.Use.one", "java:a.Car.run") in calls


def test_java_no_outer_field_past_a_base_this_front_end_did_not_walk(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")
    files = {
        "app/KBase.kt": "package app\nopen class KBase { val x: Car = Car() }\n",
        "app/Car.java": "package app;\npublic class Car { public void run() {} }\n",
        "app/Rocket.java": "package app;\npublic class Rocket { public void run() {} }\n",
        "app/O.java": (
            "package app;\npublic class O { Rocket x;\n  class In extends KBase { void f() { x.run(); } } }\n"
        ),
    }
    # the Kotlin base declares `x`, which shadows the outer field — but its fields are not recorded here
    assert ("java:app.O.In.f", "java:app.Rocket.run") not in _java(tmp_path, files)
