"""Precision boundary of the optional clang semantic pass."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.pkg.clang_link import usr_to_id
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch


@pytest.mark.parametrize(
    ("usr", "language", "rel", "expected"),
    [
        ("c:@F@subtotal", "c", "src/s.c", "c:subtotal"),
        ("c:s.c@F@helper", "c", "src/s.c", "c:src/s.c::helper"),
        ("c:@S@Handler@F@run#", "cpp", "src/dispatch.cpp", "cpp:Handler::run"),
        ("c:@N@geo@S@Circle@F@area#", "cpp", "geo.hpp", "cpp:geo::Circle::area"),
        ("c:@N@one@N@two@F@call#", "cpp", "a.cpp", "cpp:one::two::call"),
        ("c:@F@plain#", "cpp", "a.cpp", "cpp:plain"),
        ("c:@F@extern_c", "cpp", "a.cpp", "cpp:extern_c"),
        ("c:@F@run#I#", "cpp", "a.cpp", "cpp:run"),
        ("c:@S@Handler@F@run#d#", "cpp", "a.cpp", "cpp:Handler::run"),
        ("c:@S@Handler@F@run#1", "cpp", "a.cpp", "cpp:Handler::run"),
        ("c:@S@Handler@F@run#*1C#S", "cpp", "a.cpp", "cpp:Handler::run"),
        ("c:@S@Handler@F@run#I#3&", "cpp", "a.cpp", "cpp:Handler::run"),
        ("c:@S@Handler@F@run#&&", "cpp", "a.cpp", "cpp:Handler::run"),
        ("c:@S@Handler@F@run#*$@N@api@S@Arg#", "cpp", "a.cpp", "cpp:Handler::run"),
    ],
)
def test_usr_maps_supported_functions(usr: str, language: str, rel: str, expected: str) -> None:
    assert usr_to_id(usr, language=language, rel=rel) == expected


@pytest.mark.parametrize(
    "usr",
    [
        "",
        "c:@S@Handler",
        "c:@S@Handler@FI@field",
        "c:@ST>1#T@Box@F@get#",
        "c:@F@run<#I>#I#",
        "c:@aN@F@hidden#",
        "c:a.cpp@aN@F@hidden#",
        "c:a.cpp@12@F@f#@Sa@F@operator()#1",
        "c:@F@operator+#I#",
        "c:@S@Handler@F@~Handler#",
        "c:@S@Handler@F@run#garbage",
        "c:@F@run#\n",
        "c:@S@Handler@F@run#8",
        "c:@S@Handler@F@run#&&&",
        "c:@S@Handler@FT@>1#Trun#t0.0#v#",
        "c:@S@Box>#I@F@run#",
        "c:a.cpp@99@F@outer#@S@Local@F@run#",
        "c:@N@@F@run#",
        "c:@N@a@T@Alias@F@run#",
    ],
)
def test_usr_refuses_unsupported_identities(usr: str) -> None:
    assert usr_to_id(usr, language="cpp", rel="src/a.cpp") is None


@pytest.mark.parametrize("rel", ["", "/tmp/a.c", "../a.c", "src/../../a.c", "C:/a.c", "src\\a.c"])
def test_usr_requires_repository_relative_path(rel: str) -> None:
    assert usr_to_id("c:@F@f", language="c", rel=rel) is None


def test_static_uses_declaration_path_and_checks_basename() -> None:
    assert usr_to_id("c:a.c@F@f", language="c", rel="one/a.c") == "c:one/a.c::f"
    assert usr_to_id("c:a.c@F@f", language="c", rel="two/a.c") == "c:two/a.c::f"
    assert usr_to_id("c:a.c@F@f", language="c", rel="two/b.c") is None
    assert usr_to_id("c:a.c@F@f", language="cpp", rel="a.c") is None
    assert usr_to_id("c:@F@f", language="java", rel="A.java") is None


@pytest.fixture
def clang_ready() -> None:
    pytest.importorskip("clang.cindex")
    pytest.importorskip("tree_sitter_cpp")
    pytest.importorskip("tree_sitter_c")


def _calls(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def test_member_reference_pointer_virtual_and_same_line(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "calls.cpp").write_text(
        "struct Base { virtual int run() { return 1; } };\n"
        "struct Child : Base { int run() { return 2; } };\n"
        "int use(Base& b, Base* p) { return b.run() + p->run(); }\n"
        "int local() { Child c; return c.run(); }\n"
    )
    ex = RepoCodeExtractor()
    b = ex.extract(tmp_path)
    calls = _calls(b)
    assert ("cpp:use", "cpp:Base::run") in calls
    assert ("cpp:use", "cpp:Child::run") not in calls
    assert ("cpp:local", "cpp:Child::run") in calls
    assert ex.clang_report.resolved == ex.clang_report.pending == 3
    assert ex.clang_report.parsed_tus == ex.clang_report.total_tus == 1


def test_only_pending_tus_are_parsed_and_reuse_clears_state(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text("struct A { int f() { return 1; } }; int use(A& a) { return a.f(); }")
    (tmp_path / "b.cpp").write_text("int clean() { return 1; }")
    (tmp_path / "ignored.cu").write_text("this is deliberately not C++")
    (tmp_path / "ignored.mm").write_text("this is deliberately not Objective-C++")
    ex = RepoCodeExtractor()
    ex.extract(tmp_path)
    assert ex.clang_report.parsed_tus == 1
    assert ex.clang_report.total_tus == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    b = ex.extract(empty)
    assert not b.nodes and not b.edges
    assert ex.clang_report.pending == ex.clang_report.parsed_tus == 0


def test_missing_headers_and_external_targets_add_no_nodes(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text(
        "#include <not_a_real_header.hpp>\n"
        "struct A { int good() { return 1; } int external(); };\n"
        "int use(A& a) { return a.good() + a.external(); }\n"
    )
    ex = RepoCodeExtractor()
    b = ex.extract(tmp_path)
    assert ("cpp:use", "cpp:A::good") in _calls(b)
    assert ("cpp:use", "cpp:A::external") not in _calls(b)
    assert ex.clang_report.resolved == 1 and ex.clang_report.pending == 2
    assert ex.clang_report.diagnostic_tus == 1


def test_c_function_pointer_is_reported_but_not_invented(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.c").write_text("void use(void (*cb)(void)) { cb(); }")
    ex = RepoCodeExtractor()
    b = ex.extract(tmp_path)
    assert not _calls(b)
    assert ex.clang_report.pending == ex.clang_report.parsed_tus == 1
    assert ex.clang_report.resolved == 0


def test_unavailable_clang_and_failed_parse_preserve_batch(
    tmp_path: Path, clang_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clang import cindex

    from orchestrator.pkg import clang_link

    (tmp_path / "a.cpp").write_text("struct A { void f() {} }; void use(A& a) { a.f(); }")
    monkeypatch.setattr(clang_link, "clang_available", lambda: False)
    ex = RepoCodeExtractor()
    before = ex.extract(tmp_path)
    assert not ex.clang_report.available and ex.clang_report.pending == 1
    assert ex.clang_report.unresolved_reasons == {"extra_unavailable": 1}
    monkeypatch.setattr(clang_link, "clang_available", lambda: True)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise cindex.TranslationUnitLoadError("test parse failure")

    monkeypatch.setattr(cindex.Index, "parse", fail)
    after = ex.extract(tmp_path)
    assert after.nodes == before.nodes and after.edges == before.edges
    assert ex.clang_report.failed_tus == 1
    assert ex.clang_report.unresolved_reasons == {"no_matching_call": 1}


def test_header_sites_select_including_tu(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "api.hpp").write_text(
        "struct A { int f() { return 1; } }; inline int use(A& a) { return a.f(); }"
    )
    (tmp_path / "a.cpp").write_text('#include "api.hpp"\n')
    ex = RepoCodeExtractor()
    assert ("cpp:use", "cpp:A::f") in _calls(ex.extract(tmp_path))
    assert ex.clang_report.resolved == ex.clang_report.parsed_tus == 1


def test_checkout_paths_and_compile_database_do_not_change_facts(tmp_path: Path, clang_ready: None) -> None:
    batches = []
    for dirname in ("first", "second"):
        root = tmp_path / dirname
        root.mkdir()
        (root / "a.cpp").write_text("struct A { void f() {} }; void use(A& a) { a.f(); }")
        (root / "compile_commands.json").write_text(
            '[{"directory":"/' + dirname + '","command":"clang -DWRONG=1"}]'
        )
        batches.append(RepoCodeExtractor().extract(root))
    assert batches[0].nodes == batches[1].nodes
    assert batches[0].edges == batches[1].edges


def test_corpus_is_additive_only(tmp_path: Path, clang_ready: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from orchestrator.pkg import clang_link

    corpus = Path(__file__).resolve().parents[2] / "corpus"
    for spec_path in sorted(corpus.glob("*/*/expected.json")):
        spec = json.loads(spec_path.read_text())
        roots = list(spec.get("roots", {}).values()) or [spec.get("root", ".repo")]
        for rel in roots:
            root = spec_path.parent / rel
            monkeypatch.setattr(clang_link, "clang_available", lambda: False)
            before = RepoCodeExtractor().extract(root)
            monkeypatch.setattr(clang_link, "clang_available", lambda: True)
            after = RepoCodeExtractor().extract(root)
            assert before.nodes == after.nodes, str(spec_path)
            assert set(before.edges) <= set(after.edges), str(spec_path)
            grounded = {n.id for n in before.nodes if n.grounded}
            assert all(e.src in grounded and e.dst in grounded for e in set(after.edges) - set(before.edges))


def test_signature_qualified_methods_preserve_name_keyed_overloads(
    tmp_path: Path, clang_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.pkg import clang_link

    (tmp_path / "a.cpp").write_text(
        "namespace api { struct Arg {}; struct A {\n"
        "int run(int) const { return 1; } int run(double) const { return 2; }\n"
        "int withArg(Arg*) & { return 3; } static int staticCall(int) { return 4; }\n"
        "template<class T> int templated(T) { return 5; } }; }\n"
        "int use(api::A& a, api::Arg* arg) {\n"
        "return a.run(1) + a.run(2.0) + a.withArg(arg) + a.staticCall(3) + a.templated(4); }"
    )
    monkeypatch.setattr(clang_link, "clang_available", lambda: False)
    before = RepoCodeExtractor().extract(tmp_path)
    monkeypatch.setattr(clang_link, "clang_available", lambda: True)
    ex = RepoCodeExtractor()
    after = ex.extract(tmp_path)
    assert after.nodes == before.nodes
    assert set(before.edges) <= set(after.edges)
    added = set(after.edges) - set(before.edges)
    assert {(e.src, e.dst) for e in added} == {
        ("cpp:use", "cpp:api::A::run"),
        ("cpp:use", "cpp:api::A::withArg"),
        ("cpp:use", "cpp:api::A::staticCall"),
    }
    assert ex.clang_report.resolved == 4
    assert ex.clang_report.pending == 5
    assert ex.clang_report.unresolved_reasons == {"unsupported_usr": 1}


def test_miss_reasons_count_distinct_sites_across_header_tus(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "api.hpp").write_text(
        "struct A { int external(int); };\n"
        "inline int use(A& a, int (*cb)(int)) { return a.external(1) + cb(2); }\n"
    )
    for name in ("a.cpp", "b.cpp"):
        (tmp_path / name).write_text('#include "api.hpp"\n')
    ex = RepoCodeExtractor()
    ex.extract(tmp_path)
    assert ex.clang_report.pending == 2
    assert ex.clang_report.parsed_tus == 2
    assert ex.clang_report.resolved == 0
    assert ex.clang_report.unresolved_reasons == {
        "indirect_or_unsupported_target": 1,
        "ungrounded_target": 1,
    }


def test_nested_member_calls_with_same_start_have_separate_sites(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text(
        "struct A { A first(int) { return *this; } int second(int) { return 2; } };\n"
        "int use(A& a) { return a.first(1).second(2); }\n"
    )
    ex = RepoCodeExtractor()
    batch = ex.extract(tmp_path)
    assert ex.clang_report.pending == ex.clang_report.resolved == 2
    assert _calls(batch) == {("cpp:use", "cpp:A::first"), ("cpp:use", "cpp:A::second")}
    assert not ex.clang_report.unresolved_reasons


def test_destructor_calls_do_not_attach_to_constructor(
    tmp_path: Path, clang_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.pkg import clang_link

    (tmp_path / "guard.cpp").write_text(
        "struct Mutex { void lock() {} void unlock() {} };\n"
        "struct Guard { Mutex* m;\n"
        "Guard(Mutex* p): m(p) { m->lock(); }\n"
        "~Guard() { m->unlock(); } };\n"
    )
    monkeypatch.setattr(clang_link, "clang_available", lambda: False)
    before = RepoCodeExtractor().extract(tmp_path)
    monkeypatch.setattr(clang_link, "clang_available", lambda: True)
    ex = RepoCodeExtractor()
    after = ex.extract(tmp_path)
    assert after.nodes == before.nodes
    assert set(before.edges) <= set(after.edges)
    assert ("cpp:Guard::Guard", "cpp:Mutex::lock") in _calls(after)
    assert ("cpp:Guard::Guard", "cpp:Mutex::unlock") not in _calls(after)
    assert ex.clang_report.resolved == 1
    assert ex.clang_report.unresolved_reasons == {"caller_identity_mismatch": 1}


def test_macro_callers_must_match_the_enclosing_function(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "cases.cpp").write_text(
        "#define TEST(a,b) void a ## _ ## b()\n"
        "struct Worker { void run() {} };\n"
        "TEST(Suite, First) { Worker w; w.run(); }\n"
        "TEST(Suite, Second) { Worker w; w.run(); }\n"
    )
    ex = RepoCodeExtractor()
    batch = ex.extract(tmp_path)
    assert ("cpp:TEST", "cpp:Worker::run") not in _calls(batch)
    assert ex.clang_report.pending == 2
    assert ex.clang_report.resolved == 0
    assert ex.clang_report.unresolved_reasons == {"caller_identity_mismatch": 2}


def test_separate_program_entrypoints_do_not_share_semantic_edges(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text("struct A { void run() {} }; int main() { A a; a.run(); }\n")
    (tmp_path / "b.cpp").write_text("struct B { void run() {} }; int main() { B b; b.run(); }\n")
    ex = RepoCodeExtractor()
    batch = ex.extract(tmp_path)
    assert ("cpp:main", "cpp:A::run") in _calls(batch)
    assert ("cpp:main", "cpp:B::run") not in _calls(batch)
    assert ex.clang_report.resolved == 1
    assert ex.clang_report.unresolved_reasons == {"caller_identity_mismatch": 1}


def test_lost_caller_scope_is_refused_without_repairing_nodes(tmp_path: Path, clang_ready: None) -> None:
    from orchestrator.pkg.clang_link import ClangReport, PendingMemberCall, link_clang
    from orchestrator.pkg.facts import Node, NodeKind, Provenance

    source = (
        "namespace api { struct Worker { void run() {} };\n"
        "struct Service { void use(Worker& w) { w.run(); } }; }\n"
    )
    (tmp_path / "a.cpp").write_text(source)
    # A grounded but scope-stripped CST caller, as observed with export macros.
    batch = FactBatch()
    batch.add_node(Node("cpp:a.cpp", NodeKind.MODULE, "a.cpp", "cpp", Provenance("a.cpp", 1)))
    batch.add_node(Node("cpp:use", NodeKind.FUNCTION, "use", "cpp", Provenance("a.cpp", 2)))
    batch.add_node(Node("cpp:api::Worker::run", NodeKind.FUNCTION, "run", "cpp", Provenance("a.cpp", 1)))
    before = batch.nodes
    offset = source.index("w.run()")
    report = ClangReport()
    result = link_clang(
        batch,
        tmp_path,
        pending=[PendingMemberCall("cpp:use", "a.cpp", offset, 2, offset + len("w.run()"))],
        report=report,
    )
    assert result.nodes == before
    assert not result.edges
    assert report.unresolved_reasons == {"caller_identity_mismatch": 1}


def test_out_of_line_constructor_keeps_header_grounded_overload(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.hpp").write_text(
        "struct Worker { void run() {} };\nstruct Service { Service() {} Service(Worker& w); };\n"
    )
    (tmp_path / "a.cpp").write_text('#include "a.hpp"\nService::Service(Worker& w) { w.run(); }\n')
    ex = RepoCodeExtractor()
    batch = ex.extract(tmp_path)
    assert ("cpp:Service::Service", "cpp:Worker::run") in _calls(batch)
    assert ex.clang_report.resolved == 1


def test_local_class_calls_do_not_attach_to_outer_function(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text(
        "struct Worker { void run() {} };\n"
        "int main() { struct Local { void use() { Worker w; w.run(); } }; return 0; }\n"
    )
    ex = RepoCodeExtractor()
    batch = ex.extract(tmp_path)
    assert ("cpp:main", "cpp:Worker::run") not in _calls(batch)
    assert ex.clang_report.pending == 1
    assert ex.clang_report.resolved == 0
    assert ex.clang_report.unresolved_reasons == {"caller_identity_mismatch": 1}


def test_file_static_callers_preserve_scope_and_source_file(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text(
        "struct A { void run() {} };\n"
        "static void use(A& a) { a.run(); }\n"
        "namespace api { static void use(A& a) { a.run(); } }\n"
    )
    (tmp_path / "b.cpp").write_text(
        "struct B { void run() {} };\n"
        "static void use(B& b) { b.run(); }\n"
        "namespace api { static void use(B& b) { b.run(); } }\n"
    )
    ex = RepoCodeExtractor()
    batch = ex.extract(tmp_path)
    assert ("cpp:use", "cpp:A::run") in _calls(batch)
    assert ("cpp:api::use", "cpp:A::run") in _calls(batch)
    assert ("cpp:use", "cpp:B::run") not in _calls(batch)
    assert ("cpp:api::use", "cpp:B::run") not in _calls(batch)
    assert ex.clang_report.resolved == 2
    assert ex.clang_report.unresolved_reasons == {"caller_identity_mismatch": 2}


def test_exact_destructor_and_call_operator_callers(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "a.cpp").write_text(
        "namespace api { struct Worker { void run() {} };\n"
        "struct Guard { Worker w; ~Guard(); void operator()(); };\n"
        "Guard::~Guard() { w.run(); }\n"
        "void Guard::operator()() { w.run(); } }\n"
    )
    ex = RepoCodeExtractor()
    batch = ex.extract(tmp_path)
    assert ("cpp:api::Guard::~Guard", "cpp:api::Worker::run") in _calls(batch)
    assert ("cpp:api::Guard::operator()", "cpp:api::Worker::run") in _calls(batch)
    assert ("cpp:api::Guard::Guard", "cpp:api::Worker::run") not in _calls(batch)
    assert ex.clang_report.resolved == ex.clang_report.pending == 2


@pytest.mark.parametrize(
    "usr",
    [
        "c:other.cpp@F@use#",
        "c:a.cpp@aN@F@use#",
        "c:a.cpp@99@F@outer#@S@Local@F@operator()#",
        "c:@ST>1#T@Box@F@operator()#",
        "c:a.cpp@FT@>1#Tuse#t0.0#v#",
        "c:@S@A@F@operator+#I#",
    ],
)
def test_additional_caller_shapes_still_refuse_unrepresentable_identities(usr: str) -> None:
    from orchestrator.pkg.clang_link import _caller_usr_to_id

    assert _caller_usr_to_id(usr, language="cpp", rel="src/a.cpp") is None


@pytest.mark.parametrize(
    "usr",
    [
        "c:@S@Runner@F@run#@Sa@F@operator()#1",
        "c:@S@Runner@F@run#@S@Local@F@run#",
        "c:@N@api@F@run#I#@S@Local@F@method#",
    ],
)
def test_usr_refuses_local_declaration_scope_after_parameters(usr: str) -> None:
    assert usr_to_id(usr, language="cpp", rel="main.cpp") is None


@pytest.mark.parametrize(
    "body",
    [
        "auto work = []() {};\nwork();",
        "struct Local { void run() {} }; Local item; item.run();",
    ],
)
def test_local_callables_cannot_become_enclosing_self_calls(
    tmp_path: Path, clang_ready: None, body: str
) -> None:
    (tmp_path / "main.cpp").write_text(f"struct Runner {{ void run() {{ {body} }} }};\n")
    batch = RepoCodeExtractor().extract(tmp_path)
    assert any(n.id == "cpp:Runner::run" and n.grounded for n in batch.nodes)
    assert ("cpp:Runner::run", "cpp:Runner::run") not in _calls(batch)


def test_local_class_body_cannot_borrow_enclosing_caller(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "main.cpp").write_text(
        "struct Target { void go() {} };\n"
        "struct Runner { void run() {\n"
        "  struct Local { void method(Target& t) { t.go(); } };\n"
        "} };\n"
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    assert ("cpp:Runner::run", "cpp:Target::go") not in _calls(batch)


def test_scope_validation_preserves_transparent_linkage_blocks(tmp_path: Path, clang_ready: None) -> None:
    (tmp_path / "main.cpp").write_text(
        'extern "C" { struct Worker { void work() {} }; }\n'
        'namespace api { extern "C" { void use(Worker& w) { w.work(); } } }\n'
    )
    from clang import cindex

    from orchestrator.pkg.clang_link import _declaration_scope_matches

    # Linkage blocks are transparent semantic scopes even though the CST does
    # not currently emit these block-form declarations. Test the native guard.
    tu = cindex.Index.create().parse(
        str(tmp_path / "main.cpp"), args=["-x", "c++", "-std=c++17", "-nostdinc"]
    )
    declarations = {
        c.spelling: c for c in tu.cursor.walk_preorder() if c.kind.name in {"CXX_METHOD", "FUNCTION_DECL"}
    }
    assert _declaration_scope_matches(declarations["work"], "cpp:Worker::work", "cpp", "main.cpp")
    assert _declaration_scope_matches(declarations["use"], "cpp:api::use", "cpp", "main.cpp")
