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
    # A relative specifier resolves to the imported module's ID, not the literal `./base`.
    # That is what lets IMPORTS join to the first-party module; left raw, the same module
    # appeared once per importing directory as a separate "external" one.
    # (The fixture passes rel="src/account.ts" while module="account", so the resolved
    # prefix is `src/` here; in a real repo the two agree.)
    assert ("ts:account", "ts:src/base", EdgeKind.IMPORTS) in edges
    assert ("ts:account", "ts:src/util", EdgeKind.IMPORTS) in edges
    assert ("ts:account.Account", "ts:account.Account.deposit", EdgeKind.CONTAINS) in edges


def test_implements_resolves_import_and_local_sibling(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path)
    impls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    # extends Base → resolved via the import to its module specifier
    # A base type imported RELATIVELY resolves to the repo-local module's symbol id.
    # It used to read `ts:./base:Base` — package-shaped, two colons — which made the
    # extractor mint an external module node beside the real first-party one.
    assert ("ts:account.Account", "ts:src/base.Base") in impls
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


_TS_CALLS = """\
import { help } from "./helper";
import * as util from "./util";

export function a() {
  b();
  help();
  util.run();
  local.ignored();
}
export function b() {}

class Svc {
  run() { this.step(); }
  step() {}
}
"""


def test_emits_calls_for_local_imported_and_this(tmp_path: Path) -> None:
    batch, _ = _facts(tmp_path, _TS_CALLS, "a.ts")
    calls = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert ("ts:a.a", "ts:a.b") in calls  # local module function
    assert ("ts:a.a", "ts:src/helper.help") in calls  # imported binding
    assert ("ts:a.a", "ts:src/util.run") in calls  # imported namespace
    assert ("ts:a.Svc.run", "ts:a.Svc.step") in calls  # this.method()
    assert not any(dst.endswith(":ignored") or dst.endswith(".ignored") for _, dst in calls)


def test_package_call_target_gets_an_external_node(tmp_path: Path) -> None:
    """An imported third-party call must land on a node, not dangle.

    Measured on a real Angular codebase: 854 dangling edges, nearly all of them
    `rxjs/operators:takeUntil`, `@angular/core:OnInit`, `jquery:$`. The ids were honest —
    they name real packages — but no node was emitted, so every edge dangled.
    """
    src = tmp_path / "a.ts"
    src.write_text("import { takeUntil } from 'rxjs/operators';\nexport function f() { takeUntil(); }\n")
    batch = TypeScriptExtractor().extract(path=src, module="a", rel="a.ts")
    ids = {n.id for n in batch.nodes}
    dangling = [e for e in batch.edges if e.dst not in ids]
    assert not dangling, f"dangling: {[(e.src, e.dst) for e in dangling]}"
    ext = [n for n in batch.nodes if n.id == "ts:rxjs/operators:takeUntil"]
    assert ext and ext[0].external and not ext[0].grounded


def test_package_base_type_gets_an_external_node(tmp_path: Path) -> None:
    """`implements OnInit` from @angular/core needs the same treatment as a call target."""
    src = tmp_path / "b.ts"
    src.write_text("import { OnInit } from '@angular/core';\nexport class C implements OnInit {}\n")
    batch = TypeScriptExtractor().extract(path=src, module="b", rel="b.ts")
    ids = {n.id for n in batch.nodes}
    assert not [e for e in batch.edges if e.dst not in ids]


def test_repo_local_target_is_not_invented(tmp_path: Path) -> None:
    """A relative import should resolve to a real declaration; inventing a node for it
    would paper over a genuine resolution miss rather than fix it."""
    src = tmp_path / "c.ts"
    src.write_text("import { helper } from './util';\nexport function g() { helper(); }\n")
    batch = TypeScriptExtractor().extract(path=src, module="c", rel="c.ts")
    invented = [n for n in batch.nodes if n.id.startswith("ts:util") and n.kind is NodeKind.FUNCTION]
    assert not invented, "repo-local target must not get a synthesised node"


def test_method_call_on_a_named_import_is_not_a_module_member(tmp_path: Path) -> None:
    """`items.forEach()` calls an Array method, not an export of `items`' module.

    `X.foo()` only means "the export foo of X's module" when X was bound by `import * as X`.
    Treating a named binding the same way resolved a real `sidenavMenuItems.forEach(...)` to
    `ts:<module>.forEach` — a node that does not and should not exist, and the last dangling
    edge in a 24k-edge graph.
    """
    f = tmp_path / "svc.ts"
    f.write_text(
        "import { items } from './data';\nexport function run() { items.forEach(x => x); }\n",
        encoding="utf-8",
    )
    batch = TypeScriptExtractor().extract(path=f, module="svc", rel="svc.ts")
    ids = {n.id for n in batch.nodes}
    assert not [e for e in batch.edges if e.dst not in ids], "dangling edge from a named import"
    assert not [e for e in batch.edges if e.dst.endswith(".forEach")], "forEach resolved as an export"


def test_namespace_import_member_call_still_resolves(tmp_path: Path) -> None:
    """The behaviour that must NOT regress: `import * as ns` really does expose exports."""
    f = tmp_path / "svc.ts"
    f.write_text(
        "import * as util from './util';\nexport function run() { util.helper(); }\n",
        encoding="utf-8",
    )
    batch = TypeScriptExtractor().extract(path=f, module="svc", rel="svc.ts")
    calls = {e.dst for e in batch.edges if e.kind is EdgeKind.CALLS}
    assert any(c.endswith("util.helper") for c in calls), f"namespace call lost: {calls}"


def test_relative_import_joins_to_the_first_party_module(tmp_path: Path) -> None:
    """A relative specifier must resolve to the module's id, not stay as `./x`.

    Left raw, the same first-party module appeared once per importing directory as a
    separate "external" module, and IMPORTS never joined.
    """
    f = tmp_path / "a.ts"
    f.write_text("import { T } from './model/thing';\nexport class C {}\n", encoding="utf-8")
    batch = TypeScriptExtractor().extract(path=f, module="app/a", rel="app/a.ts")
    mods = {n.id for n in batch.nodes if n.kind is NodeKind.MODULE}
    assert "ts:app/model/thing" in mods, mods
    assert "ts:./model/thing" not in mods, "raw specifier survived as a module id"


# ---- shadowed calls: a name the caller bound itself ------------------------
#
# `local_funcs` and `imports` hold the FILE-level binding of a name. When a parameter, local
# or closure argument shadows one, resolving through those tables asserts a call to a
# definition the source never reaches — and the target is a real node, so nothing dangled and
# `pkg verify` stayed silent. Measured across 11 public repositories before the fix; the
# guard is `corpus/typescript/shadowed_calls`.


def _calls(tmp_path: Path, src: str) -> set[tuple[str, str]]:
    batch, _ = _facts(tmp_path, src, "dispatch.ts")
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def test_a_parameter_shadowing_a_module_function_emits_no_call(tmp_path: Path) -> None:
    src = (
        "export function handle(): number { return 1; }\n"
        "export function run(handle: () => number): number { return handle(); }\n"
        "export function direct(): number { return handle(); }\n"
    )
    calls = _calls(tmp_path, src)
    assert ("ts:dispatch.run", "ts:dispatch.handle") not in calls
    assert ("ts:dispatch.direct", "ts:dispatch.handle") in calls  # the control


def test_a_closure_parameter_shadows_an_import(tmp_path: Path) -> None:
    """`hook.forEach(h => h(...args))` — the one fabricated edge found in vue/core."""
    src = (
        'import { h } from "@vue/runtime-core";\n'
        "export function callHook(hook: Function[]): void {\n"
        "  hook.forEach(h => h());\n"
        "}\n"
        "export function render(): void { h(); }\n"
    )
    calls = _calls(tmp_path, src)
    assert ("ts:dispatch.callHook", "ts:@vue/runtime-core:h") not in calls
    assert ("ts:dispatch.render", "ts:@vue/runtime-core:h") in calls


def test_a_destructuring_default_is_not_a_binding(tmp_path: Path) -> None:
    """`{ arg: slot = makeDefault() }` binds `slot`, not `makeDefault`.

    Reading the default expression as a pattern would silently drop every later call to that
    name — the same class of error as the invention, pointing the other way.
    """
    src = (
        "export function makeDefault(): number { return 0; }\n"
        "export function build(node: any): number {\n"
        "  const { arg: slot = makeDefault() } = node;\n"
        "  return slot + makeDefault();\n"
        "}\n"
    )
    assert ("ts:dispatch.build", "ts:dispatch.makeDefault") in _calls(tmp_path, src)


def test_a_function_types_own_parameter_names_bind_nothing(tmp_path: Path) -> None:
    """`cb: (nested: string) => void` declares `nested` inside a type. Nothing binds it."""
    src = (
        "export function nested(): void {}\n"
        "export function outer(cb: (nested: string) => void): void { nested(); }\n"
    )
    assert ("ts:dispatch.outer", "ts:dispatch.nested") in _calls(tmp_path, src)
