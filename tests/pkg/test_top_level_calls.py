"""B120: a call in a TS/JS file's top-level code belongs to that file's Module node.

The TypeScript front-end walked calls only inside functions, so everything a mocha/jest test
file does — `describe(…, () => { it(…, () => { f() }) })` — produced no CALLS edge, and
`regression_gaps` called functions untested that a test plainly calls (measured on one extension
repository: a function its tests call six times had zero edges). Every residual top-level
statement is now walked, with the file's Module as the caller (D1/D2/D4), and a TypeScript call
that lands on no node is dropped rather than left dangling (D3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")

from orchestrator.pkg.extractor import RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance  # noqa: E402
from orchestrator.pkg.js_extractor import JavaScriptExtractor  # noqa: E402
from orchestrator.pkg.overview import build_overview  # noqa: E402
from orchestrator.pkg.store import FactStore  # noqa: E402
from orchestrator.pkg.typescript_extractor import TypeScriptExtractor  # noqa: E402
from orchestrator.sdlc.coverage import CoverageIndex, build_regression_plan, render_regression_plan_md  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return RepoCodeExtractor(extractors=[TypeScriptExtractor(), JavaScriptExtractor()]).extract(tmp_path)


def _calls(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS}


def _call_lines(batch: FactBatch, src: str, dst: str) -> list[int]:
    return sorted(
        e.provenance.line
        for e in batch.edges
        if e.kind is EdgeKind.CALLS and e.src == src and e.dst == dst and e.provenance is not None
    )


SRC_A = """\
export function f(n: number): number {
  return n;
}

export function g(): void {}
"""

MOCHA_TS = """\
import { f } from "../src/a";
describe("a", () => {
  it("calls f", () => {
    f(1);
  });
});
"""


# ---- T1–T3: a test file's calls reach the code it tests ----------------------


def test_a_call_inside_a_typescript_test_callback_is_a_call_from_the_test_module(tmp_path: Path) -> None:
    batch = _repo(tmp_path, {"src/a.ts": SRC_A, "test/a.test.ts": MOCHA_TS})

    assert _call_lines(batch, "ts:test/a.test", "ts:src/a.f") == [4]


def test_a_call_inside_a_javascript_mocha_callback_is_a_call_from_the_test_module(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "src/a.js": "function f(n) {\n  return n;\n}\nmodule.exports = { f };\n",
            "test/a.test.js": (
                "const { f } = require('../src/a');\n"
                "describe('a', function () {\n"
                "  it('calls f', function () {\n"
                "    f(1);\n"
                "  });\n"
                "});\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:test/a.test", "ts:src/a.f") == [4]


def test_a_function_called_only_from_a_test_callback_is_exercised(tmp_path: Path) -> None:
    store = FactStore(_repo(tmp_path, {"src/a.ts": SRC_A, "test/a.test.ts": MOCHA_TS}))

    assert CoverageIndex(store).is_covered("ts:src/a.f")
    assert "✓ exercised by tests" in render_regression_plan_md(build_regression_plan(store, "ts:src/a.f"))


# ---- T4: production top-level code too (D2) ------------------------------------


@pytest.mark.parametrize(
    "declaration",
    ["export function main(): void {}\n", "export const main = (): void => {};\n"],
    ids=["function", "const-arrow"],
)
def test_a_production_top_level_call_is_a_call_from_its_module(tmp_path: Path, declaration: str) -> None:
    """The declaration that made `main` a node is the file-level binding, not a shadow of it."""
    batch = _repo(tmp_path, {"src/cli.ts": declaration + "\nmain();\n"})

    assert _call_lines(batch, "ts:src/cli", "ts:src/cli.main") == [3]


# ---- T5–T7, T9: guards — one caller per call site, and scope still holds ------


def test_a_named_function_inside_a_test_callback_keeps_its_calls(tmp_path: Path) -> None:
    """The walk stops at a named scope, as it does inside a function body.

    `helper` is no node, so `g()` inside it is attributed to nobody — not to the module, which
    would claim the module calls `g` wherever `helper` is merely declared.
    """
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": SRC_A,
            "test/b.test.ts": (
                'import { f, g } from "../src/a";\n'
                'describe("b", () => {\n'
                "  function helper(): void {\n"
                "    g();\n"
                "  }\n"
                '  it("uses helper", () => {\n'
                "    helper();\n"
                "    f(2);\n"
                "  });\n"
                "});\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:test/b.test", "ts:src/a.f") == [8]
    assert ("ts:test/b.test", "ts:src/a.g") not in _calls(batch)


def test_a_top_level_arrow_function_is_still_the_only_caller_of_its_calls(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        # A block body: an expression-bodied `() => g()` draws no call on develop either (B127).
        {"src/a.ts": SRC_A, "src/c.ts": 'import { g } from "./a";\n\nexport const f = () => {\n  g();\n};\n'},
    )

    assert {src for src, dst in _calls(batch) if dst == "ts:src/a.g"} == {"ts:src/c.f"}


def test_a_name_rebound_at_top_level_is_not_the_function_it_shadows(tmp_path: Path) -> None:
    """`var f = make()` replaces the declared `f`, so a later `f()` is not a call to it.

    The binding crosses statements: it is made in one top-level statement and read in another.
    """
    batch = _repo(
        tmp_path,
        {
            "src/d.js": (
                "function f() {\n"
                "  return 1;\n"
                "}\n"
                "function make() {\n"
                "  return () => 2;\n"
                "}\n"
                "var f = make();\n"
                "describe('d', function () {\n"
                "  it('x', function () {\n"
                "    f();\n"
                "  });\n"
                "});\n"
                "module.exports = { make };\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:src/d", "ts:src/d.make") == [7]
    assert ("ts:src/d", "ts:src/d.f") not in _calls(batch)


@pytest.mark.parametrize("keyword", ["const", "var"])
def test_a_receiver_typed_inside_a_function_does_not_type_the_module_s_receiver(
    tmp_path: Path, keyword: str
) -> None:
    """`build`'s `h` is a `Handler`; the top-level `h` is whatever `makeOther()` returns.

    The module's walk reads typed receivers over the whole program, and `build` was read as a
    body of its own, so its locals are not the module's — a hoisted `var` included.
    """
    batch = _repo(
        tmp_path,
        {
            "src/h.ts": "export class Handler {\n  run(): void {}\n}\n",
            "src/m.ts": (
                'import { Handler } from "./h";\n'
                "export function build(): void {\n"
                f"  {keyword} h = new Handler();\n"
                "}\n"
                "export function makeOther(): any {\n"
                "  return 1;\n"
                "}\n"
                f"{keyword} h = makeOther();\n"
                "h.run();\n"
            ),
        },
    )

    assert {dst for src, dst in _calls(batch) if src == "ts:src/m"} == {"ts:src/m.makeOther"}


@pytest.mark.parametrize(
    "source",
    [
        "function g() {\n  return 1;\n}\nmodule.exports = { f() {\n  return g();\n} };\n",
        "function g() {\n  return 1;\n}\nmodule.exports.f = function () {\n  return g();\n};\n",
    ],
    ids=["object-member", "member-assignment"],
)
def test_a_commonjs_exported_function_is_the_only_caller_of_its_calls(tmp_path: Path, source: str) -> None:
    batch = _repo(tmp_path, {"src/e.js": source})

    assert {src for src, dst in _calls(batch) if dst == "ts:src/e.g"} == {"ts:src/e.f"}


# ---- review round 1: receivers typed in one scope, functions read elsewhere -----

SVC = """\
export class A {
  load(): void {}
  save(): void {}
}
export class B {
  load(): void {}
  save(): void {}
}
export function mk(): any {
  return 1;
}
"""


def test_a_receiver_s_type_is_the_one_in_scope_at_the_call(tmp_path: Path) -> None:
    """Every `it` reuses `s`; each call reaches the type its own callback declared.

    One flat name-to-type map for the whole file sent `s.load()` in the first callback to
    `B.load` — the second callback's type — so coverage called `A.load` untested and `B.load`,
    which nothing calls, tested.
    """
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "test/s.test.ts": (
                'import { A, B, mk } from "../src/svc";\n'
                'it("a", () => {\n'
                "  const s: A = mk();\n"
                "  s.load();\n"
                "});\n"
                'it("b", () => {\n'
                "  const s: B = mk();\n"
                "  s.save();\n"
                "});\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:test/s.test", "ts:src/svc.A.load") == [4]
    assert _call_lines(batch, "ts:test/s.test", "ts:src/svc.B.save") == [8]
    assert ("ts:test/s.test", "ts:src/svc.B.load") not in _calls(batch)


def test_the_innermost_stated_type_wins(tmp_path: Path) -> None:
    """A `describe` types `s` as `A`; one `it` inside it re-declares `s` as `B`."""
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "test/n.test.ts": (
                'import { A, B, mk } from "../src/svc";\n'
                'describe("n", () => {\n'
                "  const s: A = mk();\n"
                '  it("inner", () => {\n'
                "    const s: B = mk();\n"
                "    s.save();\n"
                "  });\n"
                "  s.load();\n"
                "});\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:test/n.test", "ts:src/svc.B.save") == [6]
    assert _call_lines(batch, "ts:test/n.test", "ts:src/svc.A.load") == [8]
    assert ("ts:test/n.test", "ts:src/svc.A.save") not in _calls(batch)


def test_a_narrower_binding_that_states_no_type_hides_the_outer_type(tmp_path: Path) -> None:
    """Every inner `s` here is something else; none of them is the outer `const s: A`.

    A callback parameter, a `for…of` binding, a `catch` parameter, a destructured parameter and
    an untyped `let` each rebind `s` more narrowly than the stated type. With no type of its own
    in force, the call has no stated receiver type and draws no edge (B120 review round 2).
    """
    batch = _repo(
        tmp_path,
        {
            "src/m.ts": "export class A {\n  load(): void {}\n}\nexport class B {\n  load(): void {}\n}\n",
            "test/m.test.ts": (
                'import { A, B } from "../src/m";\n'
                "const s: A = new A();\n"
                "[new B()].forEach((s) => {\n"
                "  s.load();\n"
                "});\n"
                'describe("x", () => {\n'
                "  for (const s of [new B()]) {\n"
                "    s.load();\n"
                "  }\n"
                "  try {\n"
                "  } catch (s) {\n"
                "    s.load();\n"
                "  }\n"
                '  it("y", ({ s }: { s: B }) => {\n'
                "    s.load();\n"
                "  });\n"
                '  it("z", () => {\n'
                "    let s = makeB();\n"
                "    s.load();\n"
                "  });\n"
                "  s.load();\n"
                "});\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:test/m.test", "ts:src/m.A.load") == [21]  # the outer `s`, still in force


def test_a_parameter_of_a_type_only_signature_types_no_receiver(tmp_path: Path) -> None:
    """`handle(s: B)` in an interface binds nothing at run time, so it cannot type `s` below."""
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "src/m.ts": (
                'import { B, mk } from "./svc";\n'
                "interface Handler {\n"
                "  handle(s: B): void;\n"
                "}\n"
                "const s = mk();\n"
                "s.save();\n"
            ),
        },
    )

    assert {dst for src, dst in _calls(batch) if src == "ts:src/m"} == {"ts:src/svc.mk"}


def test_a_function_in_an_aliased_exports_object_is_not_top_level_code(tmp_path: Path) -> None:
    """`const api = {create() {…}}; module.exports = api` gives `create` a node of its own.

    Its body is not the module's code, so the module is no caller of what `create` calls.
    """
    batch = _repo(
        tmp_path,
        {
            "src/a.js": "function g() {}\nmodule.exports = { g };\n",
            "src/k.js": (
                "const { g } = require('./a');\n"
                "const api = {\n"
                "  create: function () {\n"
                "    g();\n"
                "  },\n"
                "  arrow: () => {\n"
                "    g();\n"
                "  },\n"
                "};\n"
                "module.exports = api;\n"
            ),
        },
    )

    assert "ts:src/k.create" in {n.id for n in batch.nodes}
    assert ("ts:src/k", "ts:src/a.g") not in _calls(batch)


def test_a_generator_declaration_is_a_named_scope(tmp_path: Path) -> None:
    """`function* gen(g)` is not top-level code, and its parameter `g` is not the import."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": "export function g(): void {}\n",
            "src/k.ts": 'import { g } from "./a";\nexport function* gen(g: any) {\n  g();\n}\n',
        },
    )

    assert ("ts:src/k", "ts:src/a.g") not in _calls(batch)


# ---- T8: a call that lands on no node is dropped, not left dangling (D3) ------


def test_a_typescript_call_through_a_barrel_lands_on_a_node_or_is_not_drawn(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "src/lib/a.ts": "export function f(): void {}\n",
            "src/lib/index.ts": 'export { f } from "./a";\n',
            "src/use.ts": 'import { f } from "./lib";\n\nexport function g(): void {\n  f();\n}\n',
        },
    )
    known = {n.id for n in batch.nodes}

    dangling = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CALLS and e.dst not in known}
    assert dangling == set()


# ---- D9: the overview's hubs are not test files --------------------------------


def test_a_module_calling_from_top_level_code_is_not_ranked_a_hub() -> None:
    """Top-level CALLS make a test file the most connected node on every repo with tests.

    `top_symbols` ranks by degree, and a test module's top-level code calls everything it tests.
    Those edges stay in the graph and in the module edges; they just don't vote for a hub.
    """
    batch = FactBatch()
    test_mod = Node(
        "ts:test/a.test", NodeKind.MODULE, "test/a.test", "typescript", Provenance("test/a.test.ts", 1)
    )
    src_mod = Node("ts:src/a", NodeKind.MODULE, "src/a", "typescript", Provenance("src/a.ts", 1))
    funcs = [
        Node(f"ts:src/a.f{i}", NodeKind.FUNCTION, f"f{i}", "typescript", Provenance("src/a.ts", i + 1))
        for i in range(4)
    ]
    for n in (test_mod, src_mod, *funcs):
        batch.add_node(n)
    for i, fn in enumerate(funcs):
        batch.add_edge(Edge(src_mod.id, fn.id, EdgeKind.CONTAINS, fn.provenance))
        batch.add_edge(Edge(test_mod.id, fn.id, EdgeKind.CALLS, Provenance("test/a.test.ts", 10 + i)))
    batch.add_edge(Edge(funcs[1].id, funcs[0].id, EdgeKind.CALLS, Provenance("src/a.ts", 2)))

    overview = build_overview(batch)
    degree = {s["id"]: s["degree"] for s in overview["top_symbols"]}

    assert degree["ts:test/a.test"] == 0
    assert degree["ts:src/a.f0"] == 2  # CONTAINS + one production call; the test call casts no vote
    assert overview["edge_kinds"]["CALLS"] == 5  # the edges themselves are all still counted
    assert {"src": "test/a.test.ts", "dst": "src/a.ts", "kind": "CALLS", "count": 4} in overview[
        "module_edges"
    ]
