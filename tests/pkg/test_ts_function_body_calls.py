"""B127 and B128: what a TS/JS *function body* calls, read the way the module walk already reads it.

**B127 (SSPN-294).** An expression-bodied arrow, `const f = () => g()`, drew no `f → g`: the
call walk started at the body's *children*, and a concise body is the call itself. The block
form, `() => { g(); }`, was always read. Every walk that reads a body now starts from
`_body_roots`, which yields a concise body's own node.

**B128 (SSPN-295).** A typed receiver inside a function body took its type from one flat
name-to-type map over the whole body, so a sibling closure's `const s: B` typed this closure's
`s.load()`, and an untyped inner binding (a callback parameter, `catch (s)`) did not hide an
outer `s: A`. Function bodies now read receivers by scope, the way B120 made the module walk do.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")

from orchestrator.pkg.extractor import RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, FactBatch  # noqa: E402
from orchestrator.pkg.js_extractor import JavaScriptExtractor  # noqa: E402
from orchestrator.pkg.typescript_extractor import TypeScriptExtractor  # noqa: E402


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


A_TS = "export function g(): void {}\n"


# ---- B127: an expression-bodied arrow --------------------------------------------------


def test_a_concise_arrow_calls_what_its_body_calls(tmp_path: Path) -> None:
    """T1. `() => g()` and `() => { g(); }` are the same function; both call `g`."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": A_TS,
            "src/e.ts": (
                'import { g } from "./a";\nexport const c = () => g();\nexport const k = () => { g(); };\n'
            ),
        },
    )

    assert _call_lines(batch, "ts:src/e.c", "ts:src/a.g") == [2]
    assert _call_lines(batch, "ts:src/e.k", "ts:src/a.g") == [3]


def test_a_concise_arrow_in_javascript_calls_what_its_body_calls(tmp_path: Path) -> None:
    """T2. The JavaScript front-end inherits the walk, so `.js` had the same gap."""
    batch = _repo(
        tmp_path,
        {
            "src/a.js": "export function g() {}\n",
            "src/e.js": 'import { g } from "./a.js";\nexport const c = () => g();\n',
        },
    )

    assert _call_lines(batch, "ts:src/e.c", "ts:src/a.g") == [2]


def test_a_concise_arrow_s_argument_calls_are_read_too(tmp_path: Path) -> None:
    """T1b. `() => g(h())`: the call in the argument was found before; the outer one now too."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": "export function g(n: number): void {}\nexport function h(): number { return 1; }\n",
            "src/e.ts": 'import { g, h } from "./a";\nexport const c = () => g(h());\n',
        },
    )

    assert _call_lines(batch, "ts:src/e.c", "ts:src/a.g") == [2]
    assert _call_lines(batch, "ts:src/e.c", "ts:src/a.h") == [2]


def test_a_nested_concise_arrow_keeps_exactly_one_caller(tmp_path: Path) -> None:
    """T3 (guard). A concise arrow inside a function was read already; it is not read twice."""
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": A_TS,
            "src/e.ts": (
                'import { g } from "./a";\nexport function n(): void {\n  [1].forEach(() => g());\n}\n'
            ),
        },
    )

    edges = [e for e in batch.edges if e.kind is EdgeKind.CALLS and e.dst == "ts:src/a.g"]
    assert [(e.src, e.provenance.line if e.provenance else None) for e in edges] == [("ts:src/e.n", 3)]


def test_a_concise_arrow_s_parameter_hides_the_import_it_shadows(tmp_path: Path) -> None:
    """T4 (guard). `(g) => g()` calls whatever it is handed, not the imported `g`.

    The block form has refused this all along; the concise form must refuse it the same way.
    """
    batch = _repo(
        tmp_path,
        {
            "src/a.ts": A_TS,
            "src/e.ts": (
                'import { g } from "./a";\n'
                "export const c = (g: () => void) => g();\n"
                "export const k = (g: () => void) => { g(); };\n"
            ),
        },
    )

    assert not any(dst == "ts:src/a.g" for _, dst in _calls(batch))


def test_a_concise_arrow_returning_a_function_reads_like_its_block_form(tmp_path: Path) -> None:
    """T5 (guard). `() => function inner() { g(); }` draws exactly what the block form draws."""
    concise = _repo(
        tmp_path / "concise",
        {
            "src/a.ts": A_TS,
            "src/e.ts": 'import { g } from "./a";\nexport const f = () => function inner() { g(); };\n',
        },
    )
    block = _repo(
        tmp_path / "block",
        {
            "src/a.ts": A_TS,
            "src/e.ts": (
                'import { g } from "./a";\nexport const f = () => { return function inner() { g(); }; };\n'
            ),
        },
    )

    assert _calls(concise) == _calls(block)


# ---- B128: typed receivers inside a function body ---------------------------------------

SVC = (
    "export class A {\n  load(): void {}\n}\n"
    "export class B {\n  load(): void {}\n  save(): void {}\n}\n"
    "export function mk(): any {\n  return 1;\n}\n"
)


def test_a_sibling_closure_s_type_does_not_type_this_closure_s_receiver(tmp_path: Path) -> None:
    """T6. The ticket's first form: `s.load()` under `const s: A` reaches `A.load`, never `B.load`."""
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "src/use.ts": (
                'import { A, B, mk } from "./svc";\n'
                "export function run(): void {\n"
                "  [1].forEach(() => { const s: A = mk(); s.load(); });\n"
                "  [2].forEach(() => { const s: B = mk(); });\n"
                "}\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:src/use.run", "ts:src/svc.A.load") == [3]
    assert ("ts:src/use.run", "ts:src/svc.B.load") not in _calls(batch)


def test_an_untyped_callback_parameter_hides_the_outer_stated_type(tmp_path: Path) -> None:
    """T7. The ticket's second form: the callback's `s` is a `B`, so no `A.load` and no `A`."""
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "src/use.ts": (
                'import { A, B } from "./svc";\n'
                "export function run(): void {\n"
                "  const s: A = new A();\n"
                "  [new B()].forEach((s) => { s.load(); });\n"
                "}\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:src/use.run", "ts:src/svc.A.load") == []
    # `new A()` itself is no call; the constructor edge came only from typing the inner `s` as `A`.
    assert _call_lines(batch, "ts:src/use.run", "ts:src/svc.A") == []


def test_every_narrower_untyped_binding_hides_the_outer_stated_type(tmp_path: Path) -> None:
    """T8. `for…of`, `catch`, a destructured parameter and an untyped `let` each rebind `s`."""
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "src/use.ts": (
                'import { A, B } from "./svc";\n'
                "declare function makeB(): B;\n"
                "export function run(): void {\n"
                "  const s: A = new A();\n"
                "  for (const s of [new B()]) {\n"
                "    s.load();\n"
                "  }\n"
                "  try {\n"
                "  } catch (s) {\n"
                "    s.load();\n"
                "  }\n"
                "  [1].forEach(({ s }: { s: B }) => {\n"
                "    s.load();\n"
                "  });\n"
                "  [2].forEach(() => {\n"
                "    let s = makeB();\n"
                "    s.load();\n"
                "  });\n"
                "  s.load();\n"
                "}\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:src/use.run", "ts:src/svc.A.load") == [19]  # the outer `s`, still in force


def test_a_typed_parameter_still_types_its_receiver(tmp_path: Path) -> None:
    """T9 (guard). `function f(h: A) { h.load(); }` reaches `A.load` and builds no `A`."""
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "src/use.ts": 'import { A } from "./svc";\nexport function f(h: A): void {\n  h.load();\n}\n',
        },
    )

    assert _call_lines(batch, "ts:src/use.f", "ts:src/svc.A.load") == [3]
    assert ("ts:src/use.f", "ts:src/svc.A") not in _calls(batch)


def test_a_receiver_built_with_new_reaches_the_method_and_the_constructor(tmp_path: Path) -> None:
    """T10 (guard). `const h = new A(); h.load()` inside a function, as before."""
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "src/use.ts": (
                'import { A } from "./svc";\n'
                "export function f(): void {\n"
                "  const h = new A();\n"
                "  h.load();\n"
                "}\n"
            ),
        },
    )

    assert _call_lines(batch, "ts:src/use.f", "ts:src/svc.A.load") == [4]
    assert ("ts:src/use.f", "ts:src/svc.A") in _calls(batch)


def test_a_typed_parameter_of_a_concise_arrow_types_its_receiver(tmp_path: Path) -> None:
    """T10b (B127 × B128). `(h: A) => h.load()`: the concise body's call reaches `A.load`."""
    batch = _repo(
        tmp_path,
        {
            "src/svc.ts": SVC,
            "src/use.ts": 'import { A } from "./svc";\nexport const f = (h: A) => h.load();\n',
        },
    )

    assert _call_lines(batch, "ts:src/use.f", "ts:src/svc.A.load") == [2]
