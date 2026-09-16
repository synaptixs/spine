"""Repository-only clang include-root inference and precision boundaries."""

from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind


@pytest.mark.parametrize("quoted", [False, True])
@pytest.mark.parametrize("transitive", [False, True])
def test_prefixed_include_root_recovers_grounded_call(tmp_path: Path, quoted: bool, transitive: bool) -> None:
    pytest.importorskip("clang.cindex")
    pytest.importorskip("tree_sitter_cpp")
    headers = tmp_path / "include" / "api"
    headers.mkdir(parents=True)
    (headers / "worker.hpp").write_text("struct Worker { void run() {} };\n")
    entry = "worker.hpp"
    if transitive:
        (headers / "entry.hpp").write_text("#include <api/worker.hpp>\n")
        entry = "entry.hpp"
    include = f'"api/{entry}"' if quoted else f"<api/{entry}>"
    (tmp_path / "main.cpp").write_text(f"#include {include}\nvoid use(Worker& w) {{ w.run(); }}\n")
    extractor = RepoCodeExtractor()
    batch = extractor.extract(tmp_path)
    assert any(n.id == "cpp:Worker::run" and n.grounded for n in batch.nodes)
    assert any(n.id == "cpp:use" and n.grounded for n in batch.nodes)
    assert ("cpp:use", "cpp:Worker::run") in {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert extractor.clang_report.resolved == extractor.clang_report.pending == 1
    assert extractor.clang_report.parsed_tus == 1


def _roots(root: Path, files: dict[str, str], *, admitted: set[str] | None = None) -> list[str]:
    from orchestrator.pkg.clang_includes import infer_include_roots

    pytest.importorskip("tree_sitter_cpp")
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    known = set(files) if admitted is None else admitted
    baseline = sorted({str((root / f).parent) for f in known if f.endswith(".hpp")})
    return infer_include_roots(root, known, baseline)


def test_duplicate_full_suffix_does_not_choose_a_root(tmp_path: Path) -> None:
    assert (
        _roots(
            tmp_path,
            {
                "main.cpp": "#include <api/worker.hpp>\n",
                "one/api/worker.hpp": "struct A {};",
                "two/api/worker.hpp": "struct B {};",
            },
        )
        == []
    )


def test_duplicate_basename_with_distinct_suffix_is_supported(tmp_path: Path) -> None:
    assert _roots(
        tmp_path,
        {
            "main.cpp": "#include <api/worker.hpp>\n",
            "one/api/worker.hpp": "struct A {};",
            "two/other/worker.hpp": "struct B {};",
        },
    ) == ["one"]


def test_interacting_roots_cannot_resolve_ambiguous_literal(tmp_path: Path) -> None:
    assert (
        _roots(
            tmp_path,
            {
                "main.cpp": (
                    "#include <api/first.hpp>\n#include <other/second.hpp>\n#include <shared/value.hpp>\n"
                ),
                "one/api/first.hpp": "struct A {};",
                "two/other/second.hpp": "struct B {};",
                "one/shared/value.hpp": "struct C {};",
                "two/shared/value.hpp": "struct D {};",
            },
        )
        == []
    )


def test_existing_quoted_local_include_precedes_inference(tmp_path: Path) -> None:
    assert (
        _roots(
            tmp_path,
            {
                "main.cpp": '#include "api/worker.hpp"\n',
                "api/worker.hpp": "struct Local {};",
                "lib/api/worker.hpp": "struct Remote {};",
            },
        )
        == []
    )


def test_comments_strings_and_computed_includes_do_not_add_roots(tmp_path: Path) -> None:
    assert (
        _roots(
            tmp_path,
            {
                "main.cpp": (
                    '// #include <api/worker.hpp>\nconst char* s = "#include <api/worker.hpp>";\n'
                    "#include HEADER\n"
                ),
                "include/api/worker.hpp": "struct Worker {};",
            },
        )
        == []
    )


@pytest.mark.parametrize("excluded", ["build", "vendor", ".hidden", "custom"])
def test_unadmitted_headers_do_not_supply_roots(tmp_path: Path, excluded: str) -> None:
    files = {"main.cpp": "#include <api/worker.hpp>\n", f"{excluded}/api/worker.hpp": "struct Worker {};"}
    assert _roots(tmp_path, files, admitted={"main.cpp"}) == []


def test_nested_checkout_does_not_supply_roots(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / ".git").write_text("gitdir: ../elsewhere")
    assert (
        _roots(
            tmp_path,
            {
                "main.cpp": "#include <api/worker.hpp>\n",
                "nested/api/worker.hpp": "struct Worker {};",
            },
        )
        == []
    )


def test_new_root_must_not_expose_unadmitted_or_escaping_header(tmp_path: Path) -> None:
    from orchestrator.pkg.clang_includes import infer_include_roots

    files = {
        "main.cpp": "#include <api/worker.hpp>\n#include <secret.hpp>\n",
        "include/api/worker.hpp": "struct Worker {};",
        "include/secret.hpp": "struct Secret {};",
    }
    # A root proposed for api/worker.hpp also exposes an unadmitted file.
    assert _roots(tmp_path, files, admitted={"main.cpp", "include/api/worker.hpp"}) == []
    secret = tmp_path / "include/secret.hpp"
    secret.unlink()
    outside = tmp_path.parent / (tmp_path.name + "-outside.hpp")
    outside.write_text("struct Outside {};")
    secret.symlink_to(outside)
    try:
        assert infer_include_roots(tmp_path, set(files), [str(tmp_path / "include/api")]) == []
    finally:
        outside.unlink()


def test_include_roots_are_checkout_and_enumeration_independent(tmp_path: Path) -> None:
    files = {"main.cpp": "#include <api/worker.hpp>\n", "include/api/worker.hpp": "struct Worker {};"}
    assert _roots(tmp_path / "first", files) == _roots(
        tmp_path / "second", dict(reversed(list(files.items())))
    )
    assert _roots(tmp_path / "third", files) == ["include"]


def test_c_only_grammar_can_infer_literal_include_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.pkg import clang_includes

    pytest.importorskip("tree_sitter_c")
    import importlib.util

    original = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: None if name == "tree_sitter_cpp" else original(name),
    )
    (tmp_path / "include/api").mkdir(parents=True)
    (tmp_path / "include/api/value.h").write_text("struct Value { int field; };\n")
    (tmp_path / "main.c").write_text("#include <api/value.h>\n")
    assert clang_includes.infer_include_roots(
        tmp_path, {"main.c", "include/api/value.h"}, [str(tmp_path / "include/api")]
    ) == ["include"]


def test_added_roots_keep_existing_search_directory_precedence(tmp_path: Path) -> None:
    assert _roots(
        tmp_path,
        {
            "main.cpp": "#include <api/worker.hpp>\n#include <old.hpp>\n",
            "include/api/worker.hpp": "struct Worker {};",
            "include/old.hpp": "struct New {};",
            "original/old.hpp": "struct Old {};",
        },
        admitted={"main.cpp", "include/api/worker.hpp", "original/old.hpp"},
    ) == ["include"]


def test_custom_exclusions_reach_include_inference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.pkg import clang_includes
    from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS

    pytest.importorskip("clang.cindex")
    pytest.importorskip("tree_sitter_cpp")
    (tmp_path / "custom/api").mkdir(parents=True)
    (tmp_path / "custom/api/worker.hpp").write_text("struct Worker { void run() {} };\n")
    (tmp_path / "main.cpp").write_text("#include <api/worker.hpp>\nvoid use(Worker& w) { w.run(); }\n")
    seen: list[set[str]] = []
    original = clang_includes.infer_include_roots

    def infer(root: Path, admitted: set[str], baseline: list[str]) -> list[str]:
        seen.append(admitted)
        return original(root, admitted, baseline)

    monkeypatch.setattr(clang_includes, "infer_include_roots", infer)
    extractor = RepoCodeExtractor(ignore_dirs=DEFAULT_IGNORE_DIRS | {"custom"})
    extractor.extract(tmp_path)
    assert seen == [{"main.cpp"}]
    assert extractor.clang_report.resolved == 0


def test_inferred_graph_is_checkout_independent(tmp_path: Path) -> None:
    pytest.importorskip("clang.cindex")
    pytest.importorskip("tree_sitter_cpp")
    batches = []
    for name in ("first", "second"):
        root = tmp_path / name
        (root / "include/api").mkdir(parents=True)
        (root / "include/api/worker.hpp").write_text("struct Worker { void run() {} };\n")
        (root / "main.cpp").write_text("#include <api/worker.hpp>\nvoid use(Worker& w) { w.run(); }\n")
        batches.append(RepoCodeExtractor().extract(root))
    assert batches[0] == batches[1]
    assert any(e.src == "cpp:use" and e.dst == "cpp:Worker::run" for e in batches[0].edges)


def test_repeated_header_calls_do_not_repeat_path_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections import Counter

    from orchestrator.pkg import clang_link

    pytest.importorskip("clang.cindex")
    pytest.importorskip("tree_sitter_cpp")
    (tmp_path / "include/api").mkdir(parents=True)
    header = tmp_path / "include/api/worker.hpp"
    header.write_text(
        "inline void helper() {}\ninline void unrelated() {\n"
        + "helper();\n" * 100
        + "}\nstruct Worker { void run() {} };\n"
    )
    (tmp_path / "main.cpp").write_text("#include <api/worker.hpp>\nvoid use(Worker& w) { w.run(); }\n")
    counts: Counter[str] = Counter()
    original = clang_link._repo_file

    def relative(path: str, root: Path) -> str | None:
        counts[path] += 1
        return original(path, root)

    monkeypatch.setattr(clang_link, "_repo_file", relative)
    extractor = RepoCodeExtractor()
    batch = extractor.extract(tmp_path)
    assert extractor.clang_report.resolved == 1
    assert any(e.src == "cpp:use" and e.dst == "cpp:Worker::run" for e in batch.edges)
    assert counts[str(header.resolve())] <= 2


def test_traversal_keeps_computed_includes_containing_wanted_header_sites(tmp_path: Path) -> None:
    pytest.importorskip("clang.cindex")
    pytest.importorskip("tree_sitter_cpp")
    (tmp_path / "worker.hpp").write_text("struct Worker { void run() {} };\n")
    (tmp_path / "nested.hpp").write_text(
        "#pragma once\nstruct Local { void invoke(Worker& w) { w.run(); } };\n"
    )
    (tmp_path / "wrapper.hpp").write_text(
        '#include "worker.hpp"\ninline void wrapper() {\n#define BODY "nested.hpp"\n#include BODY\n}\n'
    )
    (tmp_path / "main.cpp").write_text(
        '#include "wrapper.hpp"\n#include "nested.hpp"\nvoid use(Worker& w) { w.run(); }\n'
    )
    extractor = RepoCodeExtractor()
    batch = extractor.extract(tmp_path)
    assert extractor.clang_report.pending == 2
    assert extractor.clang_report.resolved == 1
    # The nested header site is actually visited, then refused for its local
    # class caller identity. Pruning wrapper by its own filename would hide it.
    assert extractor.clang_report.unresolved_reasons == {"caller_identity_mismatch": 1}
    assert any(e.src == "cpp:use" and e.dst == "cpp:Worker::run" for e in batch.edges)
    assert not any(e.src == "cpp:Local::invoke" and e.dst == "cpp:Worker::run" for e in batch.edges)
