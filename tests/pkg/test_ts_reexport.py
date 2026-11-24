"""B121 (SSPN-288): a TS/JS call through a barrel or a CommonJS re-export reaches the declaration.

`import { ok } from '../src'`, where `src/index.ts` says `export { ok } from './result'`, named
`ts:src.ok`, which no file declares, so the last finalizer dropped the edge and the caller was
lost (neverthrow: 401 calls; vue-core: 2,289). `module.exports = require('./lib/express')` made
the root module opaque, so `express.json()` was dropped the same way (express: 57). The re-export
table (`pkg/ts_reexport.py`) follows each form to the module that declares the name, with a depth
cap and a cycle guard, and refuses a name two `export *` statements both provide.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")

from orchestrator.pkg.extractor import RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, FactBatch  # noqa: E402
from orchestrator.pkg.js_extractor import JavaScriptExtractor  # noqa: E402
from orchestrator.pkg.ts_reexport import MAX_DEPTH, STAR, WHOLE, ReexportTable  # noqa: E402
from orchestrator.pkg.typescript_extractor import TypeScriptExtractor  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return RepoCodeExtractor(extractors=[TypeScriptExtractor(), JavaScriptExtractor()]).extract(tmp_path)


def _calls(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def _dangling(batch: FactBatch) -> set[tuple[str, str]]:
    known = {n.id for n in batch.nodes}
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS and e.dst not in known}


RESULT = "export function ok(): number {\n  return 1;\n}\n\nexport function err(): number {\n  return 2;\n}\n"


def _use(names: str, call: str, spec: str = "../src") -> str:
    return f'import {{ {names} }} from "{spec}";\n\nexport function use(): void {{\n  {call};\n}}\n'


# ---- ESM forms ---------------------------------------------------------------------------


def test_a_named_re_export_reaches_the_declaration(tmp_path: Path) -> None:
    """T11. `export { ok } from './result'` in `src/index.ts`."""
    batch = _repo(
        tmp_path,
        {
            "src/result.ts": RESULT,
            "src/index.ts": 'export { ok } from "./result";\n',
            "test/t.ts": _use("ok", "ok()"),
        },
    )

    assert ("ts:test/t.use", "ts:src/result.ok") in _calls(batch)
    assert _dangling(batch) == set()


def test_a_renamed_re_export_reaches_the_original_name(tmp_path: Path) -> None:
    """T12. `export { err as fail } from './result'`: `fail()` is `result.err`."""
    batch = _repo(
        tmp_path,
        {
            "src/result.ts": RESULT,
            "src/index.ts": 'export { err as fail } from "./result";\n',
            "test/t.ts": _use("fail", "fail()"),
        },
    )

    assert ("ts:test/t.use", "ts:src/result.err") in _calls(batch)
    assert not any(dst.endswith(".fail") for _, dst in _calls(batch))


def test_a_star_re_export_reaches_the_declaration(tmp_path: Path) -> None:
    """T13. `export * from './result'`."""
    batch = _repo(
        tmp_path,
        {
            "src/result.ts": RESULT,
            "src/index.ts": 'export * from "./result";\n',
            "test/t.ts": _use("err", "err()"),
        },
    )

    assert ("ts:test/t.use", "ts:src/result.err") in _calls(batch)


def test_a_default_re_exported_under_a_name_reaches_the_default_function(tmp_path: Path) -> None:
    """T14. `export { default as make } from './factory'`."""
    batch = _repo(
        tmp_path,
        {
            "src/factory.ts": "export default function build(): number {\n  return 1;\n}\n",
            "src/index.ts": 'export { default as make } from "./factory";\n',
            "test/t.ts": _use("make", "make()"),
        },
    )

    assert ("ts:test/t.use", "ts:src/factory.build") in _calls(batch)


def test_a_namespace_re_export_draws_nothing_it_cannot_land(tmp_path: Path) -> None:
    """T15 (guard). `export * as results` then `results.ok()` through a NAMED import: the call walk
    resolves no member call on a named import (a known limit that predates B121), so there is no
    edge to forward, and nothing may dangle. The table's half is `test_the_table_*` below."""
    batch = _repo(
        tmp_path,
        {
            "src/result.ts": RESULT,
            "src/index.ts": 'export * as results from "./result";\n',
            "test/t.ts": _use("results", "results.ok()"),
        },
    )

    assert _dangling(batch) == set()
    assert not any(dst == "ts:src/result.err" for _, dst in _calls(batch))


def test_a_name_two_star_re_exports_both_provide_is_refused(tmp_path: Path) -> None:
    """T16 (guard). Two `export *` that both export `ok`: ambiguous by the ES spec, so no edge."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": "export function ok(): number {\n  return 1;\n}\n",
            "src/b.ts": "export function ok(): number {\n  return 2;\n}\n",
            "src/index.ts": 'export * from "./a";\nexport * from "./b";\n',
            "test/t.ts": _use("ok", "ok()"),
        },
    )

    assert not any(src == "ts:test/t.use" for src, _ in _calls(batch))
    assert _dangling(batch) == set()


def test_an_explicit_re_export_wins_over_a_star(tmp_path: Path) -> None:
    """T16b. ES semantics: a named export shadows the same name from `export *`, so not ambiguous."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": "export function ok(): number {\n  return 1;\n}\n",
            "src/b.ts": "export function ok(): number {\n  return 2;\n}\n",
            "src/index.ts": 'export * from "./a";\nexport { ok } from "./b";\n',
            "test/t.ts": _use("ok", "ok()"),
        },
    )

    assert {dst for src, dst in _calls(batch) if src == "ts:test/t.use"} == {"ts:src/b.ok"}


def test_a_re_export_cycle_ends_with_no_edge(tmp_path: Path) -> None:
    """T17 (guard). `a` re-exports `*` from `b` and `b` from `a`; neither declares `ok`."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": 'export * from "./b";\n',
            "src/b.ts": 'export * from "./a";\n',
            "test/t.ts": _use("ok", "ok()", "../src/a"),
        },
    )

    assert not any(src == "ts:test/t.use" for src, _ in _calls(batch))
    assert _dangling(batch) == set()


def test_a_chain_of_barrels_reaches_the_declaration(tmp_path: Path) -> None:
    """T18. `src/index.ts` → `src/lib/index.ts` → `src/lib/result.ts`."""
    batch = _repo(
        tmp_path,
        {
            "src/lib/result.ts": RESULT,
            "src/lib/index.ts": 'export { ok } from "./result";\n',
            "src/index.ts": 'export * from "./lib";\n',
            "test/t.ts": _use("ok", "ok()"),
        },
    )

    assert ("ts:test/t.use", "ts:src/lib/result.ok") in _calls(batch)


def test_a_module_s_own_exported_declaration_wins_over_its_star_re_export(tmp_path: Path) -> None:
    """T20b (guard). `src/index.ts` exports its own `ok` and `export *`s one: its own wins. Reached
    through a second barrel, so the table (not a target that already exists) decides."""
    batch = _repo(
        tmp_path,
        {
            "src/result.ts": RESULT,
            "src/index.ts": 'export * from "./result";\nexport function ok(): number {\n  return 9;\n}\n',
            "pkg/index.ts": 'export * from "../src";\n',
            "test/t.ts": _use("ok", "ok()", "../pkg"),
        },
    )

    assert {dst for src, dst in _calls(batch) if src == "ts:test/t.use"} == {"ts:src.ok"}


def test_a_barrel_call_from_a_function_in_production_code_is_routed_too(tmp_path: Path) -> None:
    """T11b. Not only test code: a production module calling through its package's barrel."""
    batch = _repo(
        tmp_path,
        {
            "src/core/result.ts": RESULT,
            "src/core/index.ts": 'export { ok } from "./result";\n',
            "src/app.ts": (
                'import { ok } from "./core";\n\nexport function main(): number {\n  return ok();\n}\n'
            ),
        },
    )

    assert ("ts:src/app.main", "ts:src/core/result.ok") in _calls(batch)


# ---- CommonJS forms ----------------------------------------------------------------------


def test_a_module_that_exports_a_required_module_reaches_its_members(tmp_path: Path) -> None:
    """T19. `index.js`: `module.exports = require('./lib/express')`; `require('..').json()`."""
    batch = _repo(
        tmp_path,
        {
            "lib/express.js": "function json() {}\nfunction text() {}\nmodule.exports = { json, text };\n",
            "index.js": "module.exports = require('./lib/express');\n",
            "test/t.js": (
                "const express = require('..');\n\n"
                "function use() {\n  express.json();\n}\n\n"
                "module.exports = { use };\n"
            ),
        },
    )

    assert ("ts:test/t.use", "ts:lib/express.json") in _calls(batch)


def test_an_exports_object_of_required_members_reaches_each_member(tmp_path: Path) -> None:
    """T20. `module.exports = { json: require('./lib/body').json }`."""
    batch = _repo(
        tmp_path,
        {
            "lib/body.js": "function json() {}\nmodule.exports = { json };\n",
            "index.js": "module.exports = { json: require('./lib/body').json };\n",
            "test/t.js": (
                "const api = require('..');\n\n"
                "function use() {\n  api.json();\n}\n\n"
                "module.exports = { use };\n"
            ),
        },
    )

    assert ("ts:test/t.use", "ts:lib/body.json") in _calls(batch)


# ---- the table itself --------------------------------------------------------------------


def test_the_table_follows_a_namespace_name_into_its_module() -> None:
    """`export * as results from './result'`: `ts:src.results.ok` is `ts:src/result.ok`."""
    table = ReexportTable()
    table.add("ts:src", "results", "ts:src/result", WHOLE)
    known = {"ts:src", "ts:src/result", "ts:src/result.ok"}

    assert table.route("ts:src.results.ok", {"ts:src", "ts:src/result"}, known) == "ts:src/result.ok"


def test_the_table_gives_up_past_its_depth_cap() -> None:
    """T18b (guard). A chain one hop longer than `MAX_DEPTH` resolves to nothing; one at the cap resolves."""

    def chain(hops: int) -> tuple[ReexportTable, set[str]]:
        table = ReexportTable()
        for i in range(hops):
            table.add(f"ts:m{i}", STAR, f"ts:m{i + 1}", STAR)
        return table, {f"ts:m{i}" for i in range(hops + 1)} | {f"ts:m{hops}.ok"}

    table, known = chain(MAX_DEPTH)
    assert table.resolve("ts:m0", "ok", known) == f"ts:m{MAX_DEPTH}.ok"
    table, known = chain(MAX_DEPTH + 1)
    assert table.resolve("ts:m0", "ok", known) is None


def test_the_table_answers_only_with_ids_that_exist() -> None:
    """A forward to a name the source module does not declare is no answer, not a minted id."""
    table = ReexportTable()
    table.add("ts:src", "ok", "ts:src/result", "ok")

    assert table.resolve("ts:src", "ok", {"ts:src", "ts:src/result"}) is None


def test_the_table_is_the_same_whatever_order_star_sources_were_read_in() -> None:
    """Determinism: one `export *` source that has the name and one that does not, either order."""
    known = {"ts:a", "ts:b", "ts:src", "ts:b.ok"}
    for order in (("ts:a", "ts:b"), ("ts:b", "ts:a")):
        table = ReexportTable()
        for src in order:
            table.add("ts:src", STAR, src, STAR)
        assert table.resolve("ts:src", "ok", known) == "ts:b.ok"


# ---- review of #703: an export map decides, and nothing is routed twice ---------------------

UNRELATED_TS = {"tools/unrelated.ts": "export function noop(): void {}\n"}


def _both(tmp_path: Path, files: dict[str, str]) -> list[set[tuple[str, str]]]:
    """The same repository read alone and with one unrelated `.ts` file: the answer must not
    depend on which languages' finalizers run."""
    return [
        _calls(_repo(tmp_path / "js", files)),
        _calls(_repo(tmp_path / "mixed", {**files, **UNRELATED_TS})),
    ]


def test_a_commonjs_export_map_beats_a_same_named_declaration(tmp_path: Path) -> None:
    """`module.exports = { run: helper }` beside a local `function run`: the export `run` is
    `helper`. A declared function is not a CommonJS export."""
    files = {
        "lib/m.js": "function run() {}\nfunction helper() {}\nmodule.exports = { run: helper };\n",
        "index.js": "module.exports = require('./lib/m');\n",
        "test/t.js": "const e = require('..');\nfunction go() {\n  e.run();\n}\nmodule.exports = { go };\n",
    }
    for calls in _both(tmp_path, files):
        assert ("ts:test/t.go", "ts:lib/m.helper") in calls
        assert ("ts:test/t.go", "ts:lib/m.run") not in calls


def test_a_forwarded_edge_is_routed_once_whatever_languages_are_present(tmp_path: Path) -> None:
    """`{ run: helper, helper: other }`: routed twice, `run` became `helper` and then `other`."""
    files = {
        "lib/x.js": (
            "function helper() {}\nfunction other() {}\nmodule.exports = { run: helper, helper: other };\n"
        ),
        "index.js": "module.exports = require('./lib/x');\n",
        "test/t.js": "const e = require('..');\nfunction go() {\n  e.run();\n}\nmodule.exports = { go };\n",
    }
    js, mixed = _both(tmp_path, files)
    assert js == mixed
    assert ("ts:test/t.go", "ts:lib/x.helper") in js
    assert ("ts:test/t.go", "ts:lib/x.other") not in js


def test_a_typescript_barrel_over_a_renaming_export_map(tmp_path: Path) -> None:
    """A TS barrel forwards `run` from a CommonJS module whose map says `run` is `helper`."""
    batch = _repo(
        tmp_path,
        {
            "lib/x.js": (
                "function helper() {}\n"
                "function other() {}\n"
                "module.exports = { run: helper, helper: other };\n"
            ),
            "src/index.ts": 'export { run } from "../lib/x";\n',
            "test/t.ts": _use("run", "run()"),
        },
    )

    assert {dst for src, dst in _calls(batch) if src == "ts:test/t.use"} == {"ts:lib/x.helper"}


def test_an_unexported_local_does_not_shadow_an_explicit_re_export(tmp_path: Path) -> None:
    """`function parse` (not exported) beside `export { parse } from './parser'`: legal ES, and the
    export is the parser's. Read through a second barrel so the table decides."""
    batch = _repo(
        tmp_path,
        {
            "src/parser.ts": "export function parse(s: string): string {\n  return s;\n}\n",
            "src/index.ts": (
                'function parse(s: string): number {\n  return 0;\n}\nexport { parse } from "./parser";\n'
            ),
            "pkg/index.ts": 'export { parse } from "../src";\n',
            "test/t.ts": _use("parse", "parse('x')", "../pkg"),
        },
    )

    assert {dst for src, dst in _calls(batch) if src == "ts:test/t.use"} == {"ts:src/parser.parse"}


def test_an_unexported_local_is_not_a_star_candidate(tmp_path: Path) -> None:
    """Two `export *`, one of whose modules has an unexported `parse`: only one exports it."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": "export function parse(): void {}\n",
            "src/b.ts": "function parse(): void {}\nexport function other(): void {\n  parse();\n}\n",
            "src/index.ts": 'export * from "./a";\nexport * from "./b";\n',
            "test/t.ts": _use("parse", "parse()"),
        },
    )

    assert {dst for src, dst in _calls(batch) if src == "ts:test/t.use"} == {"ts:src/a.parse"}


def test_a_later_module_exports_assignment_replaces_the_forward(tmp_path: Path) -> None:
    """`module.exports = require('./lib/x'); module.exports = { run };`: `helper` is no longer
    exported, so `e.helper()` reaches nothing; `e.run()` is the module's own `run`."""
    batch = _repo(
        tmp_path,
        {
            "lib/x.js": "function helper() {}\nmodule.exports = { helper };\n",
            "index.js": (
                "function run() {}\nmodule.exports = require('./lib/x');\nmodule.exports = { run };\n"
            ),
            "test/t.js": (
                "const e = require('..');\n"
                "function go() {\n"
                "  e.helper();\n"
                "  e.run();\n"
                "}\n"
                "module.exports = { go };\n"
            ),
        },
    )

    assert ("ts:test/t.go", "ts:lib/x.helper") not in _calls(batch)


def test_a_call_whose_target_exists_is_never_moved(tmp_path: Path) -> None:
    """A landed edge stays put even when the module also forwards the name elsewhere."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": "export function f(): void {}\n",
            "src/b.ts": 'export function f(): void {}\nexport * from "./a";\n',
            "src/use.ts": 'import { f } from "./b";\nexport function u(): void {\n  f();\n}\n',
        },
    )

    assert {dst for src, dst in _calls(batch) if src == "ts:src/use.u"} == {"ts:src/b.f"}
