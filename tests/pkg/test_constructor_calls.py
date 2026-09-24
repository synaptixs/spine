"""Java and C# constructor calls are ``CALLS`` to the created Type (B22).

Instantiation is ``CALLS`` to the Type node (``corpus/README.md``), which Python and Kotlin already
emitted; Java and C# emitted nothing, so ``blast_radius`` on a class showed nobody who creates one.
The corpus cases ``java/constructor_calls`` and ``csharp/constructor_calls`` carry the shapes end to
end; these pin what they do not: the forms whose type is relative to an expression or inferred,
creations outside any method, and the DI factory rule.
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


def test_java_a_creation_lands_on_the_type_and_only_in_a_method(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    files = {
        "a/Outer.java": (
            "package a;\npublic class Outer {\n  public class Inner { }\n"
            "  private Outer held = new Outer();\n"  # a field initializer: no calling function
            "  void make(Outer o) { new Outer(); o.new Inner(); }\n}\n"
        ),
    }
    calls = _edges(tmp_path, files)
    assert calls == {("java:a.Outer.make", "java:a.Outer")}  # `o.new Inner()` is relative to `o`


def test_java_a_creation_in_an_anonymous_body_belongs_to_the_enclosing_method(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    files = {
        "a/Task.java": "package a;\npublic interface Task { void run(); }\n",
        "a/Job.java": "package a;\npublic class Job { }\n",
        "a/Use.java": (
            "package a;\npublic class Use {\n"
            "  void go() { Task t = new Task() { public void run() { new Job(); } }; }\n}\n"
        ),
    }
    # the anonymous class has no node of its own; its calls are attributed as receiver calls are
    assert {("java:a.Use.go", "java:a.Task"), ("java:a.Use.go", "java:a.Job")} <= _edges(tmp_path, files)


def test_java_a_creation_of_an_external_type_gets_no_edge(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    files = {
        "a/Use.java": (
            "package a;\nimport java.util.HashMap;\npublic class Use { void go() { new HashMap(); } }\n"
        ),
    }
    assert _edges(tmp_path, files) == set()


def test_csharp_target_typed_new_only_where_the_declaration_writes_the_type(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")
    files = {
        "A.cs": (
            "namespace A {\n  public class Job { }\n  public class Use {\n"
            "    private Job _held = new();\n"  # a field initializer: no calling function
            "    void Take(Job j) { }\n"
            "    void Local() { Job j = new(); }\n"
            "    void Argument() { Take(new()); }\n"
            "    void Assigned() { Job j; j = new(); }\n"
            "    void Inferred() { var j = new Job(); } } }\n"
        ),
    }
    calls = {c for c in _edges(tmp_path, files) if c[1] == "csharp:A.Job"}
    assert calls == {("csharp:A.Use.Local", "csharp:A.Job"), ("csharp:A.Use.Inferred", "csharp:A.Job")}


def _di(tmp_path: Path, registration: str) -> set[tuple[str, str]]:
    pytest.importorskip("tree_sitter_c_sharp", reason="install the 'csharp' extra")
    files = {
        "D.cs": (
            "namespace D {\n  public interface IAudit { }\n  public class DbAudit : IAudit { }\n"
            "  public class Stray { }\n  public class Services { }\n"
            "  public static class Startup {\n"
            "    static DbAudit Build() => new DbAudit();\n"
            f"    public static void Register(Services services) {{ {registration} }} }} }}\n"
        ),
    }
    return _edges(tmp_path, files, EdgeKind.PROVIDES)


def test_a_factory_that_is_one_creation_provides(tmp_path: Path) -> None:
    assert _di(tmp_path, "services.AddScoped<IAudit>(sp => new DbAudit());") == {
        ("csharp:D.DbAudit", "csharp:D.IAudit")
    }


@pytest.mark.parametrize(
    "registration",
    [
        pytest.param("services.AddScoped<IAudit>(sp => { return new DbAudit(); });", id="a block body"),
        pytest.param("services.AddScoped<IAudit>(sp => Build());", id="a method call"),
        pytest.param(
            "services.AddScoped<IAudit>(sp => new Stray());", id="a type that is not an implementation"
        ),
        pytest.param("services.AddScoped<IAudit>(new DbAudit());", id="an instance, not a factory"),
    ],
)
def test_any_other_factory_is_not_read(tmp_path: Path, registration: str) -> None:
    assert _di(tmp_path, registration) == set()
