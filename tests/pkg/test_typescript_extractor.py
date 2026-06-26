"""PKG G6: the TypeScript front-end maps TS source onto the universal facts.

tree-sitter is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.typescript_extractor import TypeScriptExtractor

pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")

ACCOUNT = """\
import { Base } from "./base";
import type { List } from "./util";

export interface Closeable {
  close(): void;
}

export class Account extends Base implements Closeable {
  private owner: string;
  balance = 0;

  constructor(owner: string) {
    this.owner = owner;
  }

  deposit(amount: number): void {
    this.balance += amount;
  }

  close(): void {}
}

export enum Status { OPEN, CLOSED }

export function make(owner: string): Account {
  return new Account(owner);
}

export const helper = (n: number): number => n + 1;
"""


def _facts(tmp_path: Path, src: str = ACCOUNT, name: str = "account.ts") -> tuple[FactBatch, str]:
    f = tmp_path / name
    f.write_text(src, encoding="utf-8")
    ex = TypeScriptExtractor()
    module = ex.module_name(f, tmp_path)
    return ex.extract(path=f, module=module, rel=f"src/{name}"), module


def test_module_name_is_the_path(tmp_path: Path) -> None:
    _, module = _facts(tmp_path)
    assert module == "account"


def test_index_collapses_to_directory(tmp_path: Path) -> None:
    sub = tmp_path / "widgets"
    sub.mkdir()
    _, module = _facts(sub.parent, src="export const x = 1;\n", name="widgets/index.ts")
    assert module == "widgets"


def test_emits_type_method_field_nodes(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["ts:account.Account"].kind is NodeKind.TYPE
    assert by_id["ts:account.Closeable"].kind is NodeKind.TYPE
    assert by_id["ts:account.Account.deposit"].kind is NodeKind.FUNCTION
    assert by_id["ts:account.Account.owner"].kind is NodeKind.FIELD
    assert by_id["ts:account.Account.balance"].kind is NodeKind.FIELD
    assert by_id["ts:account.Status"].kind is NodeKind.TYPE
    # interface member signatures: method → Function, fields → Field
    assert by_id["ts:account.Closeable.close"].kind is NodeKind.FUNCTION


def test_emits_top_level_functions_decl_and_arrow(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["ts:account.make"].kind is NodeKind.FUNCTION  # function declaration
    assert by_id["ts:account.helper"].kind is NodeKind.FUNCTION  # exported arrow const


def test_imports_and_contains_edges(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    edges = {(e.src, e.dst, e.kind) for e in batch.edges}
    assert ("ts:account", "ts:./base", EdgeKind.IMPORTS) in edges
    assert ("ts:account", "ts:./util", EdgeKind.IMPORTS) in edges
    assert ("ts:account.Account", "ts:account.Account.deposit", EdgeKind.CONTAINS) in edges


def test_implements_resolves_import_and_local_sibling(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    # extends Base → resolved via the import to its module specifier
    assert ("ts:account.Account", "ts:./base:Base") in impls
    # implements Closeable → resolved to the same-module sibling interface
    assert ("ts:account.Account", "ts:account.Closeable") in impls


def test_does_not_emit_calls(tmp_path: Path) -> None:
    # Precision-first: TS call resolution needs type inference, so no CALLS.
    batch, _ = _facts(tmp_path)
    assert not [e for e in batch.edges if e.kind is EdgeKind.CALLS]


def test_repo_extractor_dispatches_typescript_by_suffix(tmp_path: Path) -> None:
    from orchestrator.pkg.extractor import PythonExtractor, RepoCodeExtractor

    (tmp_path / "a.ts").write_text(ACCOUNT, encoding="utf-8")
    (tmp_path / "m.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    batch = RepoCodeExtractor([PythonExtractor(), TypeScriptExtractor()]).extract(tmp_path)
    langs = {n.language for n in batch.nodes}
    assert "typescript" in langs and "python" in langs


def test_tsx_is_parsed(tmp_path: Path) -> None:
    src = "export class Widget {\n  render(): null { return null; }\n}\n"
    batch, _ = _facts(tmp_path, src=src, name="widget.tsx")
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["ts:widget.Widget"].kind is NodeKind.TYPE
    assert by_id["ts:widget.Widget.render"].kind is NodeKind.FUNCTION
