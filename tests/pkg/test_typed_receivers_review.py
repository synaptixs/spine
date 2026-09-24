"""Every shape the maintainer review proved the typed-receiver pass guessed on (B21, review pass 1).

Each case states what the compiler binds and what a guessing pass would have emitted instead —
the `javac` / .NET 10 target was established when the review compiled them. A case asserts the
true edge where one resolves, and the absence of the wrong one either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import EdgeKind, RepoCodeExtractor


def _edges(tmp_path: Path, files: dict[str, str], kind: EdgeKind = EdgeKind.CALLS) -> set[tuple[str, str]]:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return {(e.src, e.dst) for e in RepoCodeExtractor().extract(tmp_path).edges if e.kind is kind}


JAVA = [
    pytest.param(
        {
            "com/x/Map.java": "package com.x;\npublic class Map { public int size() { return 0; } }\n",
            "com/x/Use.java": "package com.x;\nimport java.util.Map;\n"
            "public class Use { Map m; void f() { m.size(); } }\n",
        },
        None,
        ("java:com.x.Use.f", "java:com.x.Map.size"),
        id="an explicit external import ends the lookup",
    ),
    pytest.param(
        {
            "app/Box.java": "package app;\npublic class Box<Handler> { Handler h; void f() { h.run(); } }\n",
            "app/Handler.java": "package app;\npublic class Handler { public void run() {} }\n",
        },
        None,
        ("java:app.Box.f", "java:app.Handler.run"),
        id="a type parameter is not the in-repo type",
    ),
    pytest.param(
        {
            "app/H.java": "package app;\npublic class H { public boolean ok() { return true; } }\n",
            "app/Use.java": "package app;\n"
            "public class Use { H arr[]; void f(H p[]) { arr.ok(); p.ok(); } }\n",
        },
        None,
        ("java:app.Use.f", "java:app.H.ok"),
        id="a C-style array is not its element type",
    ),
    pytest.param(
        {
            "app/Rocket.java": "package app;\npublic class Rocket { public void run() {} }\n",
            "app/Car.java": "package app;\npublic class Car { public void run() {} }\n",
            "app/B.java": (
                "package app;\npublic class B { Rocket x;\n"
                "  void f(boolean b) { if (b) { Car x = new Car(); } x.run(); }\n"
                "  void g() { x.run(); Car x = new Car(); } }\n"
            ),
        },
        [("java:app.B.f", "java:app.Rocket.run"), ("java:app.B.g", "java:app.Rocket.run")],
        ("java:app.B.f", "java:app.Car.run"),
        id="a local is in force only in its block, from its declaration",
    ),
    pytest.param(
        {
            "app/Rocket.java": "package app;\npublic class Rocket { public void run() {} }\n",
            "app/Car.java": "package app;\npublic class Car { public void run() {} }\n",
            "app/Consts.java": "package app;\npublic interface Consts { Car x = new Car(); }\n",
            "app/O.java": (
                "package app;\npublic class O { Rocket x;\n"
                "  class In implements Consts { void f() { x.run(); } } }\n"
            ),
        },
        [("java:app.O.In.f", "java:app.Car.run")],
        ("java:app.O.In.f", "java:app.Rocket.run"),
        id="an inherited interface constant shadows the outer field",
    ),
    pytest.param(
        {
            "app/Rocket.java": "package app;\npublic class Rocket { public void run() {} }\n",
            "app/O.java": (
                "package app;\nimport org.ext.ExternalBase;\npublic class O { Rocket x;\n"
                "  class In extends ExternalBase { void f() { x.run(); } }\n"
                "  void g() { Object o = new ExternalBase() { void h() { x.run(); } }; } }\n"
            ),
        },
        None,
        ("java:app.O.In.f", "java:app.Rocket.run"),
        id="no outer-field fallback past an external base or inside an anonymous class",
    ),
    pytest.param(
        {
            "app/Car.java": "package app;\npublic class Car { public void run() {} }\n",
            "app/Rocket.java": "package app;\npublic class Rocket { public void run() {} }\n",
            "app/S.java": "package app;\n"
            "public class S { void f(Car Rocket) { Rocket.run(); LOG.info(); } }\n",
        },
        [("java:app.S.f", "java:app.Car.run")],
        ("java:app.S.f", "java:app.Rocket.run"),
        id="a parameter named like a type is the variable, and a static call must exist",
    ),
    pytest.param(
        {
            "app/Listener.java": "package app;\npublic interface Listener { default void on() {} }\n",
            "app/O.java": (
                "package app;\npublic class O {\n  public interface Listener { default void on() {} }\n"
                "  public static class Impl implements Listener { }\n  void f(Impl i) { i.on(); } }\n"
            ),
        },
        [("java:app.O.f", "java:app.O.Listener.on")],
        ("java:app.O.f", "java:app.Listener.on"),
        id="a nested base wins over the package's type of the same name",
    ),
    pytest.param(
        {
            "app/A.java": "package app;\npublic class A { public void m() {} }\n",
            "app/I.java": "package app;\npublic interface I { default void m() {} }\n",
            "app/C.java": "package app;\npublic class C extends A implements I { }\n",
            "app/U.java": "package app;\npublic class U { void f(C c) { c.m(); } }\n",
        },
        [("java:app.U.f", "java:app.A.m")],
        ("java:app.U.f", "java:app.I.m"),
        id="the class chain before an interface default",
    ),
]


JAVA.extend(
    [
        pytest.param(
            {
                "app/Base.java": "package app;\npublic class Base { public void load() {} }\n",
                "app/Sub.java": (
                    "package app;\nimport java.io.Serializable;\n"
                    "public class Sub extends Base implements Serializable { }\n"
                ),
                "app/Car.java": "package app;\npublic class Car { public void load() {} }\n",
                "app/U.java": "package app;\npublic class U { void f(Sub s) { s.load(); } }\n",
            },
            [("java:app.U.f", "java:app.Base.load")],
            ("java:app.U.f", "java:app.Car.load"),
            id="an external interface does not stop the base-class walk",
        ),
        pytest.param(
            {
                "app/I.java": "package app;\npublic interface I { void size(); }\n",
                "app/L.java": (
                    "package app;\nimport java.util.AbstractList;\n"
                    "public abstract class L extends AbstractList<String> implements I { }\n"
                ),
                "app/U.java": "package app;\npublic class U { void f(L l) { l.size(); } }\n",
            },
            None,
            ("java:app.U.f", "java:app.I.size"),
            id="an external base class does — it may implement the member itself",
        ),
        pytest.param(
            {
                "app/Msg.java": (
                    "package app;\nimport com.google.protobuf.GeneratedMessage;\n"
                    "public class Msg extends GeneratedMessage {\n"
                    "  private static final Msg DEFAULT_INSTANCE = null;\n"
                    "  public Msg toBuilder() { return this; }\n"
                    "  public static Msg newBuilder() { return DEFAULT_INSTANCE.toBuilder(); } }\n"
                ),
                "app/Helper.java": "package app;\npublic class Helper { public static void help() {} }\n",
                "app/U.java": "package app;\npublic class U { void f() { Helper.help(); } }\n",
            },
            [("java:app.Msg.newBuilder", "java:app.Msg.toBuilder"), ("java:app.U.f", "java:app.Helper.help")],
            ("java:app.Msg.newBuilder", "java:app.DEFAULT_INSTANCE.toBuilder"),
            id="a capitalized name is a declared field first, then a type",
        ),
    ]
)


@pytest.mark.parametrize(("files", "present", "absent"), JAVA)
def test_java(
    tmp_path: Path, files: dict[str, str], present: list[tuple[str, str]] | None, absent: tuple[str, str]
) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    calls = _edges(tmp_path, files)
    assert absent not in calls
    assert set(present or ()) <= calls


def test_java_an_external_import_keeps_its_base(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    files = {
        "com/x/CallableStatement.java": "package com.x;\npublic class CallableStatement { }\n",
        "com/x/W.java": (
            "package com.x;\nimport java.sql.CallableStatement;\n"
            "public abstract class W implements CallableStatement { }\n"
        ),
    }
    bases = _edges(tmp_path, files, EdgeKind.IMPLEMENTS)
    assert ("java:com.x.W", "java:java.sql.CallableStatement") in bases
    assert ("java:com.x.W", "java:com.x.CallableStatement") not in bases


CSHARP = [
    pytest.param(
        {
            "T.cs": (
                "namespace Outer { public class Foo { public void Run() {} } }\n"
                "namespace Lib { public class Foo { public void Run() {} } }\n"
            ),
            "U.cs": "namespace Outer.Inner {\n  using Lib;\n"
            "  public class C { Foo _f; public void M() { _f.Run(); } }\n}\n",
        },
        [("csharp:Outer.Inner.C.M", "csharp:Lib.Foo.Run")],
        ("csharp:Outer.Inner.C.M", "csharp:Outer.Foo.Run"),
        id="a using inside a namespace block is consulted at that block's level",
    ),
    pytest.param(
        {
            "T.cs": (
                "using Widget = App.Data.Gadget;\n"
                "namespace App.Data { public class Gadget { public void Spin() {} } }\n"
                "namespace App { public class Widget { public void Spin() {} }\n"
                "  public class U { Widget w; void M() { w.Spin(); } } }\n"
            ),
        },
        [("csharp:App.U.M", "csharp:App.Widget.Spin")],
        ("csharp:App.U.M", "csharp:App.Data.Gadget.Spin"),
        id="a namespace member wins over a file-level alias",
    ),
    pytest.param(
        {
            "M.cs": "namespace App.Models { public class User { public "
            "static string Find(string s) => s; } }\n",
            "C.cs": (
                "using Microsoft.AspNetCore.Mvc;\nusing App.Models;\nnamespace App.Web {\n"
                '  public class Home : ControllerBase { public string Get() => User.Find("sub"); } }\n'
            ),
        },
        None,
        ("csharp:App.Web.Home.Get", "csharp:App.Models.User.Find"),
        id="an inherited framework property is not the in-repo type",
    ),
    pytest.param(
        {
            "ProjA/ProjA.csproj": "<Project />\n",
            "ProjA/GlobalUsings.cs": "global using App.Data;\n",
            "ProjA/Stack.cs": "namespace App.Data { public class Stack { public void Push(int x) {} } }\n",
            "ProjB/ProjB.csproj": "<Project />\n",
            "ProjB/Leak.cs": (
                "using System.Collections;\n"
                "namespace B { public class L { Stack s; void M() { s.Push(1); } } }\n"
            ),
        },
        None,
        ("csharp:B.L.M", "csharp:App.Data.Stack.Push"),
        id="a global using belongs to its own project",
    ),
    pytest.param(
        {
            "Q.cs": (
                "namespace Q {\n"
                "  public class Status { public class Kind { public static int Parse() => 1; } }\n"
                "  public class Other { public K Kind = new K(); }\n"
                "  public class K { public int Parse() => 1; }\n"
                "  public class U { Other Status; void M() { Status.Kind.Parse(); } } }\n"
            ),
        },
        None,
        ("csharp:Q.U.M", "csharp:Q.Status.Kind.Parse"),
        id="a dotted receiver whose head is a field is a chain, not a static call",
    ),
    pytest.param(
        {
            "N.cs": (
                "namespace N {\n  public interface IX { public class Inner { public void Go() {} } }\n"
                "  public class Inner { public void Go() {} }\n"
                "  public class C : IX { Inner i; void M() { i.Go(); } } }\n"
            ),
        },
        [("csharp:N.C.M", "csharp:N.Inner.Go")],
        ("csharp:N.C.M", "csharp:N.IX.Inner.Go"),
        id="an interface's nested type is not inherited in C#",
    ),
    pytest.param(
        {
            "P.cs": (
                "namespace P { public class Rocket { public void Fire() {} }\n"
                "  public class U { Rocket Cannon { get; } void M() { Cannon.Fire(); } } }\n"
            ),
        },
        [("csharp:P.U.M", "csharp:P.Rocket.Fire")],
        ("csharp:P.U.M", "csharp:P.Cannon.Fire"),
        id="a property receiver resolves through its declared type",
    ),
]


CSHARP.append(
    pytest.param(
        {
            "H.cs": (
                "namespace H { public static class Helper { public static void Format() {} }\n"
                "  public class Base { }\n"
                "  public class Svc : Base, System.IDisposable { public void Dispose() { }\n"
                "    void M() { Helper.Format(); } }\n"
                "  public class Web : Microsoft.AspNetCore.Mvc.ControllerBase {\n"
                "    void M() { Helper.Format(); } } }\n"
            ),
        },
        [("csharp:H.Svc.M", "csharp:H.Helper.Format")],
        ("csharp:H.Web.M", "csharp:H.Helper.Format"),
        id="only an external base class can hide a static name, not an external interface",
    )
)


@pytest.mark.parametrize(("files", "present", "absent"), CSHARP)
def test_csharp(
    tmp_path: Path, files: dict[str, str], present: list[tuple[str, str]] | None, absent: tuple[str, str]
) -> None:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")
    calls = _edges(tmp_path, files)
    assert absent not in calls
    assert set(present or ()) <= calls


def test_csharp_di_needs_a_real_implementation(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")
    files = {
        "D.cs": (
            "namespace D { public interface IA { } public class AImpl : IA { }\n"
            "  public static class W { public static void R(Svc s) { "
            "s.AddScoped<AImpl, IA>(); s.AddScoped<IA, AImpl>(); } }\n"
            "  public class Svc { } }\n"
        ),
    }
    assert _edges(tmp_path, files, EdgeKind.PROVIDES) == {("csharp:D.AImpl", "csharp:D.IA")}
