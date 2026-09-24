"""The Java/C# type lookup sees every binding closer than the one a flat lookup finds (B30).

B21's receivers, B22's creations, static calls, bases and DI bindings all resolve a written type
through ``TypeIndex.type_of``. Before this it could not see a local class, an anonymous class's
inherited member type, a member type an external base declares, a dotted head's inherited member,
or a local function's / generic method's type parameter — and grounded the name on a farther
in-repo namesake. The corpus cases ``java/lookup_scope`` and ``csharp/lookup_scope`` carry each
shape as a creation; these pin the other paths through the same lookup, and the forms that must
still resolve.
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


def _java(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    return _edges(tmp_path, files)


def _csharp(tmp_path: Path, files: dict[str, str], kind: EdgeKind = EdgeKind.CALLS) -> set[tuple[str, str]]:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")
    return _edges(tmp_path, files, kind)


ORDER = "package a;\npublic class Order { public void run() {} public static void make() {} }\n"


def test_java_a_local_class_hides_its_namesake_for_receivers_and_statics(tmp_path: Path) -> None:
    files = {
        "a/Order.java": ORDER,
        "a/Use.java": (
            "package a;\npublic class Use {\n"
            "  void local() { record Order(int id) { void run() {} static void make() {} }\n"
            "    Order o = new Order(1); o.run(); Order.make(); }\n"
            "  void outside() { Order o = new Order(); o.run(); Order.make(); }\n}\n"
        ),
    }
    calls = {c for c in _java(tmp_path, files) if c[1].startswith("java:a.Order")}
    assert calls == {
        ("java:a.Use.outside", "java:a.Order"),
        ("java:a.Use.outside", "java:a.Order.run"),
        ("java:a.Use.outside", "java:a.Order.make"),
    }


def test_java_an_external_member_type_is_inherited_through_an_in_repo_ancestor(tmp_path: Path) -> None:
    files = {
        "a/SimpleEntry.java": "package a;\npublic class SimpleEntry { public int key() { return 0; } }\n",
        "a/Entry.java": "package a;\npublic class Entry { }\n",
        "a/BaseMap.java": (
            "package a;\nimport java.util.AbstractMap;\n"
            "public abstract class BaseMap<K, V> extends AbstractMap<K, V> { }\n"
        ),
        "a/MyMap.java": (
            "package a;\npublic abstract class MyMap extends BaseMap<String, String> {\n"
            "  SimpleEntry held;\n  int viaField() { return held.key(); }\n}\n"
        ),
        "a/Other.java": "package a;\n"
        "public class Other { SimpleEntry held; int viaField() { return held.key(); } }\n",
        "a/Keyed.java": "package a;\nimport java.util.Map;\n"
        "public abstract class Keyed implements Map<String, String> {\n"
        "  Object make() { return new Entry(); }\n}\n",
    }
    calls = _java(tmp_path, files)
    # `SimpleEntry` inside MyMap is AbstractMap.SimpleEntry, two levels up; outside, the repo's own
    assert ("java:a.MyMap.viaField", "java:a.SimpleEntry.key") not in calls
    assert ("java:a.Other.viaField", "java:a.SimpleEntry.key") in calls
    # Java inherits member types from interfaces too: `Entry` in a Map implementation is Map.Entry
    assert ("java:a.Keyed.make", "java:a.Entry") not in calls


def test_java_qualified_names_still_resolve(tmp_path: Path) -> None:
    files = {
        "app/model/Order.java": "package app.model;\npublic class Order { public static class Line { } }\n",
        "app/svc/Use.java": (
            "package app.svc;\nimport app.model.Order;\npublic class Use {\n"
            "  void pkg() { new app.model.Order(); }\n  void nested() { new Order.Line(); }\n"
            "  void external() { new java.util.ArrayList<Order>(); }\n}\n"
        ),
    }
    assert _java(tmp_path, files) == {
        ("java:app.svc.Use.pkg", "java:app.model.Order"),
        ("java:app.svc.Use.nested", "java:app.model.Order.Line"),
    }


def test_csharp_an_external_member_type_through_an_in_repo_base(tmp_path: Path) -> None:
    files = {
        "M.cs": "namespace App.Model { public class ControlCollection { "
        "public ControlCollection(object o) { } } }\n",
        "V.cs": (
            "using System.Windows.Forms;\nusing App.Model;\nnamespace App.Ui {\n"
            "  public class BaseForm : Form { }\n"
            "  public class Orders : BaseForm { object Make() => new ControlCollection(this); }\n"
            "  public class Plain { object Make() => new ControlCollection(this); } }\n"
        ),
    }
    assert {c for c in _csharp(tmp_path, files) if c[1] == "csharp:App.Model.ControlCollection"} == {
        ("csharp:App.Ui.Plain.Make", "csharp:App.Model.ControlCollection")
    }


def test_csharp_a_dotted_head_through_an_alias_and_inheritance(tmp_path: Path) -> None:
    files = {
        "M.cs": (
            "namespace Lib { public class BaseResponse { public class Status { } }\n"
            "  public class Response : BaseResponse { } }\n"
            "namespace Other { public class Response { public class Status { } } }\n"
        ),
        "U.cs": (
            "using R = Lib.Response;\nusing Other;\nnamespace App {\n"
            "  public class U { void Aliased() { new R.Status(); } void "
            "Imported() { new Response.Status(); } } }\n"
        ),
    }
    assert _csharp(tmp_path, files) == {
        ("csharp:App.U.Aliased", "csharp:Lib.BaseResponse.Status"),
        ("csharp:App.U.Imported", "csharp:Other.Response.Status"),
    }


def test_csharp_type_parameters_of_local_functions_and_generic_methods(tmp_path: Path) -> None:
    files = {
        "T.cs": (
            "namespace D {\n  public interface IA { }\n  public class TImpl : IA { }\n"
            "  public class TItem { public void Go() { } }\n"
            "  public class Services { }\n  public class Use {\n"
            "    void Local() { void Inner<TItem>(TItem item) where "
            "TItem : new() { var x = new TItem(); } }\n"
            "    void Real() { var x = new TItem(); x.Go(); }\n"
            "    void Reg<TImpl>(Services services) where TImpl : class, "
            "IA { services.AddScoped<IA, TImpl>(); }\n"
            "    void RegReal(Services services) { services.AddScoped<IA, TImpl>(); } } }\n"
        ),
    }
    calls = _csharp(tmp_path, files)
    assert {c for c in calls if c[1].startswith("csharp:D.TItem")} == {
        ("csharp:D.Use.Real", "csharp:D.TItem"),
        ("csharp:D.Use.Real", "csharp:D.TItem.Go"),
    }
    # the real registration binds; the generic one binds whatever its caller passes — the two
    # would be the same edge, so tell them apart by the line that registered them
    batch = RepoCodeExtractor().extract(tmp_path)
    lines = [
        e.provenance.line for e in batch.edges if e.kind is EdgeKind.PROVIDES and e.provenance is not None
    ]
    text = files["T.cs"].splitlines()
    assert [text[n - 1].strip()[:12] for n in lines] == ["void RegReal"]


def test_csharp_a_head_that_is_a_nearer_namespace_is_read_as_the_namespace(tmp_path: Path) -> None:
    files = {
        "R.cs": "namespace Biz.Rules { public class Rules { public static int Apply() => 1; } }\n",
        "C.cs": (
            "using Biz.Rules;\nnamespace Biz.Cart {\n"
            "  public class Cart {\n    int Total() => Rules.Rules.Apply();\n"
            "    object Make() => new Rules.Rules(); } }\n"
        ),
    }
    # `Rules` in `Biz.Cart` is the namespace `Biz.Rules` (found at the `Biz` level before any using),
    # so `Rules.Rules` is the class — not a member type `Rules` of the class the using brings in
    assert _csharp(tmp_path, files) == {
        ("csharp:Biz.Cart.Cart.Total", "csharp:Biz.Rules.Rules.Apply"),
        ("csharp:Biz.Cart.Cart.Make", "csharp:Biz.Rules.Rules"),
    }


def test_csharp_an_alias_to_a_namespace_heads_a_qualified_name(tmp_path: Path) -> None:
    files = {
        "M.cs": (
            "namespace App.Model {\n  public class Base { }\n  public interface IRepo { }\n"
            "  public class Repo : IRepo { }\n"
            "  public class Order { public void Run() { } public static void Make() { } } }\n"
        ),
        "F.cs": (
            "using Microsoft.Extensions.DependencyInjection;\nusing M = App.Model;\nnamespace App.Ui {\n"
            "  public class F : M.Base {\n"
            "    void Go(IServiceCollection services) { var o = new M.Order(); o.Run(); M.Order.Make();\n"
            "      services.AddScoped<M.IRepo, M.Repo>(); } } }\n"
        ),
    }
    # `M` is an alias for a namespace, so `M.Order` is `App.Model.Order` — every path through the
    # lookup, not an external type named `M` (review 1, B1: all of these were lost or misplaced)
    assert _csharp(tmp_path, files) == {
        ("csharp:App.Ui.F.Go", "csharp:App.Model.Order"),
        ("csharp:App.Ui.F.Go", "csharp:App.Model.Order.Run"),
        ("csharp:App.Ui.F.Go", "csharp:App.Model.Order.Make"),
    }
    assert _edges(tmp_path, {}, EdgeKind.PROVIDES) == {("csharp:App.Model.Repo", "csharp:App.Model.IRepo")}
    assert ("csharp:App.Ui.F", "csharp:App.Model.Base") in _edges(tmp_path, {}, EdgeKind.IMPLEMENTS)


def test_java_a_private_member_type_is_not_inherited_but_hides_a_deeper_one(tmp_path: Path) -> None:
    files = {
        "app/model/Node.java": "package app.model;\npublic class Node { public void visit() {} }\n",
        "app/svc/Root.java": "package app.svc;\n"
        "public class Root { public static class Node { public void visit() {} } }\n",
        "app/svc/Mid.java": (
            "package app.svc;\npublic class Mid extends Root {\n"
            "  private static class Node { void visit() {} }\n  void own(Node n) { n.visit(); }\n}\n"
        ),
        "app/svc/Leaf.java": (
            "package app.svc;\nimport app.model.Node;\n"
            "public class Leaf extends Mid { void f(Node n) { n.visit(); } }\n"
        ),
    }
    calls = _java(tmp_path, files)
    # javac: Mid's private Node is not inherited, and it hides Root.Node — so the import decides
    assert ("java:app.svc.Leaf.f", "java:app.model.Node.visit") in calls
    assert not {c for c in calls if c[0] == "java:app.svc.Leaf.f" and "svc" in c[1]}
    # inside Mid itself, its own private Node is the one in scope
    assert ("java:app.svc.Mid.own", "java:app.svc.Mid.Node.visit") in calls


def test_java_lang_is_imported_implicitly(tmp_path: Path) -> None:
    files = {
        "app/State.java": "package app;\n"
        "public class State { public static State valueOf(String s) { return null; } }\n",
        "app/Worker.java": (
            "package app;\n"
            'public class Worker extends Thread { Object f() { return State.valueOf("NEW"); } }\n'
        ),
        "app/Plain.java": "package app;\n"
        'public class Plain { Object f() { return State.valueOf("NEW"); } }\n',
    }
    calls = _java(tmp_path, files)
    # `Thread` is java.lang.Thread without an import, so `State` in its subclass is Thread.State
    assert ("java:app.Worker.f", "java:app.State.valueOf") not in calls
    assert ("java:app.Plain.f", "java:app.State.valueOf") in calls


BASE = (
    "package a;\npublic abstract class Base {\n"
    "  public static class Helper { public void go() {} public static void s() {} }\n"
    "  public abstract void run();\n}\n"
)
HELPER = "package a;\npublic class Helper { public void go() {} public static void s() {} }\n"


def test_java_a_refused_head_refuses_the_whole_name(tmp_path: Path) -> None:
    files = {
        "a/Order.java": "package a;\npublic class Order { public static class Line { } }\n",
        "a/SimpleEntry.java": "package a;\npublic class SimpleEntry { public static class Pair { } }\n",
        "a/Use.java": (
            "package a;\npublic class Use {\n  void local() { class Order { } new Order.Line(); }\n}\n"
        ),
        "a/MyMap.java": (
            "package a;\nimport java.util.AbstractMap;\n"
            "public abstract class MyMap<K, V> extends AbstractMap<K, V> { "
            "Object f() { return new SimpleEntry.Pair(); } }\n"
        ),
    }
    # a local class head, or a head an external base declares, is not the in-repo type of that name
    assert _java(tmp_path, files) == set()


def test_java_receivers_and_statics_inside_an_anonymous_body(tmp_path: Path) -> None:
    files = {
        "a/Base.java": BASE,
        "a/Helper.java": HELPER,
        "a/Use.java": (
            "package a;\npublic class Use {\n  void f() {\n"
            "    Base b = new Base() { public void run() { Helper h = null; h.go(); Helper.s(); } };\n"
            "    Helper outside = null; outside.go();\n  }\n}\n"
        ),
    }
    calls = _java(tmp_path, files)
    assert {("java:a.Use.f", "java:a.Base.Helper.go"), ("java:a.Use.f", "java:a.Base.Helper.s")} <= calls
    assert ("java:a.Use.f", "java:a.Helper.go") in calls  # the one declared outside the anonymous body
    assert ("java:a.Use.f", "java:a.Helper.s") not in calls


def test_java_an_anonymous_class_with_an_external_base(tmp_path: Path) -> None:
    files = {
        "a/Helper.java": HELPER,
        "a/SimpleEntry.java": "package a;\npublic class SimpleEntry { }\n",
        "a/Use.java": (
            "package a;\nimport java.util.AbstractMap;\npublic class Use {\n"
            "  void listed() { Object m = new AbstractMap<String, String>() {\n"
            "    public java.util.Set entrySet() { new SimpleEntry(); return null; } }; }\n"
            "  void unlisted() { Runnable r = new Runnable() { public void run() { new Helper(); } }; }\n}\n"
        ),
    }
    calls = _java(tmp_path, files)
    # AbstractMap declares SimpleEntry, so it hides the repo's; Runnable declares no Helper
    assert ("java:a.Use.listed", "java:a.SimpleEntry") not in calls
    assert ("java:a.Use.unlisted", "java:a.Helper") in calls


def test_java_a_local_class_hides_to_the_end_of_its_block_and_into_a_lambda(tmp_path: Path) -> None:
    files = {
        "a/Order.java": "package a;\npublic class Order { }\n",
        "a/Use.java": (
            "package a;\npublic class Use {\n"
            "  void block(boolean b) { if (b) { class Order { } } new Order(); }\n"
            "  void lambda() { class Order { } Runnable r = () -> new Order(); }\n}\n"
        ),
    }
    assert _java(tmp_path, files) == {("java:a.Use.block", "java:a.Order")}


def test_csharp_a_receiver_typed_by_a_local_functions_type_parameter(tmp_path: Path) -> None:
    files = {
        "T.cs": (
            "namespace D {\n  public class TItem { public void Go() { } }\n  public class Use {\n"
            "    void M() { void Inner<TItem>(TItem item) { item.Go(); } }\n"
            "    void Real(TItem item) { item.Go(); } } }\n"
        ),
    }
    assert _csharp(tmp_path, files) == {("csharp:D.Use.Real", "csharp:D.TItem.Go")}
