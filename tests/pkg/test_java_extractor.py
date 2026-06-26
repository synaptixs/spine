"""PKG G6: the Java front-end maps Java source onto the universal facts.

tree-sitter is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.java_extractor import JavaExtractor

pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")

ACCOUNT = """\
package com.example.bank;

import com.example.base.Base;
import java.util.List;

public class Account extends Base implements Closeable {
    private final String owner;
    private int balance = 0;

    public Account(String owner) {
        this.owner = owner;
    }

    public void deposit(int amount) {
        this.balance += amount;
    }

    enum Status { OPEN, CLOSED }
}
"""


def _facts(tmp_path: Path, src: str = ACCOUNT, name: str = "Account.java") -> tuple[FactBatch, str]:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    ex = JavaExtractor()
    module = ex.module_name(f, tmp_path)
    return ex.extract(path=f, module=module, rel=f"src/{name}"), module


def test_module_name_is_the_package(tmp_path: Path) -> None:
    _, module = _facts(tmp_path)
    assert module == "com.example.bank"


def test_emits_type_method_field_nodes(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["java:com.example.bank.Account"].kind is NodeKind.TYPE
    assert by_id["java:com.example.bank.Account.deposit"].kind is NodeKind.FUNCTION
    assert by_id["java:com.example.bank.Account.owner"].kind is NodeKind.FIELD
    assert by_id["java:com.example.bank.Account.balance"].kind is NodeKind.FIELD
    # nested enum is a Type contained by the class
    assert by_id["java:com.example.bank.Account.Status"].kind is NodeKind.TYPE


def test_imports_and_contains_edges(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("java:com.example.bank", "java:com.example.base.Base", EdgeKind.IMPORTS) in edges
    assert (
        "java:com.example.bank.Account",
        "java:com.example.bank.Account.deposit",
        EdgeKind.CONTAINS,
    ) in edges


def test_implements_resolves_import_and_same_package(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    # extends Base → resolved via the import to its FQN
    assert ("java:com.example.bank.Account", "java:com.example.base.Base") in impls
    # implements Closeable → unimported, resolved to a same-package sibling
    assert ("java:com.example.bank.Account", "java:com.example.bank.Closeable") in impls


def test_does_not_emit_calls(tmp_path: Path) -> None:
    # Precision-first: Java call resolution needs type inference, so no CALLS.
    batch, _ = _facts(tmp_path)
    assert not [e for e in batch.edges if e.kind is EdgeKind.CALLS]


def test_repo_extractor_dispatches_java_by_suffix(tmp_path: Path) -> None:
    from orchestrator.pkg.extractor import PythonExtractor, RepoCodeExtractor

    (tmp_path / "A.java").write_text(ACCOUNT, encoding="utf-8")
    (tmp_path / "m.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    batch = RepoCodeExtractor([PythonExtractor(), JavaExtractor()]).extract(tmp_path)
    langs = {n.language for n in batch.nodes}
    assert "java" in langs and "python" in langs


def test_unpackaged_file_falls_back_to_path(tmp_path: Path) -> None:
    _, module = _facts(tmp_path, "class Bare {}\n", name="Bare.java")
    assert module == "Bare.java"
