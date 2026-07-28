"""The import join: intra-package imports must resolve to real module nodes.

The regression these tests pin down: a repo written with relative /
intra-package imports (the norm outside this repo) must show **non-zero
importers** for the modules it imports — the graph must never call a package's
most central module "a leaf". One fixture per language front-end; each skips
cleanly when its parser extra is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import EdgeKind, FactBatch, FactStore, NodeKind, RepoCodeExtractor
from orchestrator.pkg.facts import Edge, Node, Provenance
from orchestrator.pkg.import_link import link_imports


def _import_pairs(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPORTS}


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


# ---- python -----------------------------------------------------------------


def test_python_relative_imports_join(tmp_path: Path) -> None:
    _write(tmp_path, "click/__init__.py", "from . import core\n")
    _write(tmp_path, "click/core.py", "from .types import convert\nclass Context:\n    pass\n")
    _write(tmp_path, "click/types.py", "def convert():\n    pass\n")

    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    # the imported modules have non-zero importers — the whole point of the fix
    assert store.importers_of("py:click.core")
    assert [n.id for n in store.importers_of("py:click.core")] == ["py:click"]
    # `from .types import convert` resolves to the grounded symbol in click.types
    convert = store.node("py:click.types.convert")
    assert convert is not None and convert.grounded


def test_python_stdlib_shadow_stays_external(tmp_path: Path) -> None:
    # `import types` (stdlib) must NOT be conflated with the package's types.py
    _write(tmp_path, "click/__init__.py", "")
    _write(tmp_path, "click/core.py", "import types\nfrom .types import convert\n")
    _write(tmp_path, "click/types.py", "def convert():\n    pass\n")

    batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["py:types"].external  # stdlib, untouched
    assert ("py:click.core", "py:types") in _import_pairs(batch)
    assert by_id["py:click.types.convert"].grounded


def test_python_reexport_joins_to_the_package_module(tmp_path: Path) -> None:
    # `from click import echo` where echo really lives in click.utils: the id
    # py:click.echo matches no node, but its dotted prefix py:click does.
    _write(tmp_path, "click/__init__.py", "from .utils import echo\n")
    _write(tmp_path, "click/utils.py", "def echo():\n    pass\n")
    _write(tmp_path, "app.py", "from click import echo\n")

    batch = RepoCodeExtractor().extract(tmp_path)
    assert ("py:app", "py:click") in _import_pairs(batch)
    assert "py:click.echo" not in {n.id for n in batch.nodes}  # phantom dropped


def test_python_third_party_imports_stay_external(tmp_path: Path) -> None:
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/main.py", "import os\nfrom requests import get\n")

    batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["py:os"].external
    assert by_id["py:requests.get"].external
    assert ("py:app.main", "py:os") in _import_pairs(batch)


# ---- typescript -------------------------------------------------------------


def test_typescript_relative_import_joins(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    _write(tmp_path, "src/main.ts", 'import { helper } from "./utils";\nexport const x = helper;\n')
    _write(tmp_path, "src/utils.ts", "export function helper(): number { return 1; }\n")
    _write(tmp_path, "src/lib/index.ts", 'import { helper } from "../utils";\n')

    batch = RepoCodeExtractor().extract(tmp_path)
    pairs = _import_pairs(batch)
    assert ("ts:src/main", "ts:src/utils") in pairs
    assert ("ts:src/lib", "ts:src/utils") in pairs  # ../ from a collapsed index module
    assert "ts:./utils" not in {n.id for n in batch.nodes}  # phantom dropped
    store = FactStore(batch)
    assert {n.id for n in store.importers_of("ts:src/utils")} == {"ts:src/main", "ts:src/lib"}


def test_typescript_bare_specifier_stays_external(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")
    _write(tmp_path, "src/main.ts", 'import React from "react";\n')

    batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["ts:react"].external


# ---- go ---------------------------------------------------------------------


def test_go_intra_module_import_joins(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_go", reason="install the 'go' extra")
    _write(tmp_path, "go.mod", "module example.com/app\n\ngo 1.22\n")
    _write(tmp_path, "main.go", 'package main\n\nimport "example.com/app/util"\n\nfunc main() {}\n')
    _write(tmp_path, "util/util.go", "package util\n\nfunc Helper() int { return 1 }\n")

    batch = RepoCodeExtractor().extract(tmp_path)
    assert ("go:<root>", "go:util") in _import_pairs(batch)
    assert "go:example.com/app/util" not in {n.id for n in batch.nodes}
    store = FactStore(batch)
    assert [n.id for n in store.importers_of("go:util")] == ["go:<root>"]


def test_go_third_party_import_stays_external(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_go", reason="install the 'go' extra")
    _write(tmp_path, "go.mod", "module example.com/app\n")
    _write(tmp_path, "main.go", 'package main\n\nimport "fmt"\n\nfunc main() {}\n')

    batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["go:fmt"].external


# ---- c ----------------------------------------------------------------------


def test_c_include_dir_header_joins_by_suffix(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_c", reason="install the 'c' extra")
    # <mylib/api.h> is reached via -I include — invisible per-file, joined by suffix.
    _write(tmp_path, "src/main.c", "#include <mylib/api.h>\n\nint main(void) { return 0; }\n")
    _write(tmp_path, "include/mylib/api.h", "int api_call(void);\n")

    batch = RepoCodeExtractor().extract(tmp_path)
    assert ("c:src/main.c", "c:include/mylib/api.h") in _import_pairs(batch)
    store = FactStore(batch)
    assert [n.id for n in store.importers_of("c:include/mylib/api.h")] == ["c:src/main.c"]


def test_c_ambiguous_suffix_is_not_guessed(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_c", reason="install the 'c' extra")
    _write(tmp_path, "src/main.c", "#include <api.h>\n\nint main(void) { return 0; }\n")
    _write(tmp_path, "include/a/api.h", "int a(void);\n")
    _write(tmp_path, "include/b/api.h", "int b(void);\n")

    batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["c:api.h"].external  # two candidates — left alone, not guessed


# ---- java -------------------------------------------------------------------


def test_java_intra_package_import_joins(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    _write(
        tmp_path,
        "src/com/shop/Billing.java",
        "package com.shop;\n\nimport com.shop.tax.Calc;\n\npublic class Billing {}\n",
    )
    _write(
        tmp_path,
        "src/com/shop/tax/Calc.java",
        "package com.shop.tax;\n\npublic class Calc {}\n",
    )

    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    # the import resolves to the grounded Calc type (dedup join) whose module is the package
    calc = store.node("java:com.shop.tax.Calc")
    assert calc is not None and calc.grounded


def test_java_unmatched_class_joins_to_its_package_module(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    # An import of a class the graph doesn't have (e.g. generated code) still
    # joins to its first-party *package* via the dotted-prefix walk.
    _write(
        tmp_path,
        "src/com/shop/Billing.java",
        "package com.shop;\n\nimport com.shop.tax.Generated;\n\npublic class Billing {}\n",
    )
    _write(
        tmp_path,
        "src/com/shop/tax/Calc.java",
        "package com.shop.tax;\n\npublic class Calc {}\n",
    )

    batch = RepoCodeExtractor().extract(tmp_path)
    assert ("java:com.shop", "java:com.shop.tax") in _import_pairs(batch)
    assert "java:com.shop.tax.Generated" not in {n.id for n in batch.nodes}


# ---- unit: the post-pass on a hand-built batch ------------------------------


def test_link_imports_repoints_and_drops_the_phantom(tmp_path: Path) -> None:
    batch = FactBatch()
    batch.add_node(Node("py:pkg.a", NodeKind.MODULE, "pkg.a", "python", Provenance("pkg/a.py", 1)))
    batch.add_node(Node("py:pkg.b", NodeKind.MODULE, "pkg.b", "python", Provenance("pkg/b.py", 1)))
    batch.add_node(Node("py:pkg.b.missing", NodeKind.MODULE, "pkg.b.missing", "python", external=True))
    batch.add_edge(Edge("py:pkg.a", "py:pkg.b.missing", EdgeKind.IMPORTS, Provenance("pkg/a.py", 1)))

    linked = link_imports(batch, tmp_path)
    assert ("py:pkg.a", "py:pkg.b") in _import_pairs(linked)
    assert "py:pkg.b.missing" not in {n.id for n in linked.nodes}


def test_link_imports_is_a_noop_without_matches(tmp_path: Path) -> None:
    batch = FactBatch()
    batch.add_node(Node("py:pkg.a", NodeKind.MODULE, "pkg.a", "python", Provenance("pkg/a.py", 1)))
    batch.add_node(Node("py:os", NodeKind.MODULE, "os", "python", external=True))
    batch.add_edge(Edge("py:pkg.a", "py:os", EdgeKind.IMPORTS, Provenance("pkg/a.py", 1)))

    linked = link_imports(batch, tmp_path)
    assert linked is batch  # unchanged — same object back
