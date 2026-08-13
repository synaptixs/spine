"""The false-positive side — facts the graph asserts that are not true.

Every other check in this package hunts for **absence**: `verify` for self-inconsistency,
`accuracy` for missing facts, the parity counter for under-extraction. Nothing hunts for
**invention**, and an invented edge is worse than a missing one, because every surface
downstream presents it as grounded. Blast radius says "6 callers" in the same confident tone
whether or not one of them is fictional.

One invention class is **exactly detectable**, which the roadmap did not anticipate — it
scoped this phase as a human sampling chore. A ``CALLS`` edge whose target is an ``external``
single-segment id, where that name is *bound in the caller's own scope*, is not a call to
something outside the tree. It is a call through a parameter or a local, and the "module" it
names does not exist:

    def _run(cmd: list[str], echo: Echo) -> None:   # launch.py:237 — echo is a PARAMETER
        echo(f"$ {' '.join(cmd)}")                  # graph: _run -CALLS-> py:echo

Measured on this repo: **496 such edges, 3.2% of all ``CALLS``**. The corpus found the same
shape twice in hand-written fixtures (``py:cls`` from a local variable, ``py:fn`` from a
parameter) — this is its repo-wide size, and it is the whole gap between Python's 0.80 corpus
precision and TypeScript's 1.00.

**Scope matters, and it moved the number the opposite way from the prediction.** A crude
file-scoped prototype reported 326, and the build document predicted scope-correctness would
*lower* it — a name bound in one function but genuinely imported for another would stop being
flagged. It went up, to 496, because scope-correctness was not the only change: the prototype
never counted a ``def`` as a binding, so it missed nested functions called by name
(``esc`` in ``scripts/intents_to_confluence.py``). Two corrections in opposite directions,
net increase. Both sub-classes are real and verified by hand.

**Imports are deliberately not bindings.** ``import json`` and ``from x import y`` create no
``Name``/``Store`` node, so an imported callee is never flagged — which is correct, since an
imported name genuinely does refer to something outside this file.

This module **measures only**. It changes no fact the extractor emits; the graph is
byte-identical with and without it. Fixing the front-end is a separate ticket, which this
number exists to prove.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.facts import EdgeKind, FactBatch

_MAX_EXAMPLES = 15


@dataclass(frozen=True)
class InventedCall:
    """A ``CALLS`` edge to a name that is bound in the caller's own scope."""

    src: str
    dst: str
    file: str
    line: int
    name: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line} {self.src} -CALLS-> {self.dst} ({self.name} is local)"


@dataclass(frozen=True)
class InventionReport:
    """How much of the call graph is fiction."""

    invented: tuple[InventedCall, ...]
    total_calls: int
    external_calls: int
    candidates: int
    unexamined: int

    @property
    def rate(self) -> float | None:
        """Share of all ``CALLS`` edges that are invented. ``None`` when there are none."""
        return len(self.invented) / self.total_calls if self.total_calls else None

    @property
    def examples(self) -> tuple[str, ...]:
        return tuple(str(i) for i in self.invented[:_MAX_EXAMPLES])


# ---- scope-aware binding resolution ---------------------------------------


def _direct_bindings(body: list[ast.stmt]) -> set[str]:
    """Names bound *directly* in one scope's body — not those in nested function scopes.

    Descending into a nested ``def`` would make its parameters look like bindings of the
    enclosing scope, which is how a file-scoped test over-reports.
    """
    names: set[str] = set()

    def walk(node: ast.AST, *, top: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(child.name)  # the def itself binds its name here
                continue  # but its body belongs to a different scope
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                names.add(child.id)
            elif isinstance(child, ast.ExceptHandler) and child.name:
                names.add(child.name)
            walk(child, top=False)

    for stmt in body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(stmt.name)
            continue
        walk(stmt, top=True)
    return names


def _scopes(tree: ast.Module) -> list[tuple[int, int, frozenset[str]]]:
    """``(start_line, end_line, bound_names)`` for the module and every function in it.

    A call site is resolved against every scope whose line range contains it — the module
    body plus the chain of functions enclosing the call.
    """
    out: list[tuple[int, int, frozenset[str]]] = [(1, 1 << 30, frozenset(_direct_bindings(tree.body)))]
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        params = {a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
        if node.args.vararg:
            params.add(node.args.vararg.arg)
        if node.args.kwarg:
            params.add(node.args.kwarg.arg)
        end = node.end_lineno or node.lineno
        out.append((node.lineno, end, frozenset(params | _direct_bindings(node.body))))
    return out


def _visible_names(scopes: list[tuple[int, int, frozenset[str]]], line: int) -> set[str]:
    return {n for start, end, names in scopes if start <= line <= end for n in names}


# ---- detection -------------------------------------------------------------


def find_invented_calls(batch: FactBatch, root: Path | str) -> InventionReport:
    """Every ``CALLS`` edge whose target is a name bound in the caller's own scope."""
    base = Path(root)
    external = {n.id for n in batch.nodes if n.external}
    calls = [e for e in batch.edges if e.kind is EdgeKind.CALLS]

    # Candidates only: a multi-segment id (`py:json.dumps`) names a real module path, and a
    # single-segment one may still be a builtin (`py:ValueError`). The binding test decides.
    candidates = [e for e in calls if e.dst in external and "." not in e.dst.partition(":")[2]]

    cache: dict[str, list[tuple[int, int, frozenset[str]]] | None] = {}

    def scopes_for(rel: str) -> list[tuple[int, int, frozenset[str]]] | None:
        if rel not in cache:
            try:
                cache[rel] = _scopes(ast.parse((base / rel).read_text(encoding="utf-8", errors="replace")))
            except (OSError, SyntaxError):
                cache[rel] = None
        return cache[rel]

    invented: list[InventedCall] = []
    unexamined = 0
    for edge in candidates:
        if edge.provenance is None:
            unexamined += 1  # no call site to resolve against; not clean, just unknown
            continue
        scopes = scopes_for(edge.provenance.file)
        if scopes is None:
            unexamined += 1
            continue
        name = edge.dst.partition(":")[2]
        if name in _visible_names(scopes, edge.provenance.line):
            invented.append(
                InventedCall(edge.src, edge.dst, edge.provenance.file, edge.provenance.line, name)
            )

    return InventionReport(
        invented=tuple(sorted(invented, key=lambda i: (i.file, i.line, i.dst))),
        total_calls=len(calls),
        external_calls=sum(1 for e in calls if e.dst in external),
        candidates=len(candidates),
        unexamined=unexamined,
    )


# ---- the sampler -----------------------------------------------------------


def sample_edges(batch: FactBatch, kind: EdgeKind, count: int) -> list[str]:
    """A reviewable, **deterministic** sample of emitted edges with their source line.

    For the invention classes no detector can reach — ``CONSUMES`` matched on ``(verb, path)``,
    ``EXPOSES`` composed from mount prefixes, ORM ``REFERENCES`` guessing a class name. Each is
    a place where a wrong edge is *plausible*, and only a person reading the source can say.

    Evenly spaced over a sorted population rather than randomly drawn, so two people auditing
    the same commit review the same facts and can compare notes.
    """
    edges = sorted(
        (e for e in batch.edges if e.kind is kind),
        key=lambda e: (e.src, e.dst, str(e.provenance)),
    )
    if not edges or count <= 0:
        return []
    step = max(1, len(edges) // count)
    picked = edges[::step][:count]
    return [f"{e.provenance or '?'}  {e.src} -{e.kind.value}-> {e.dst}" for e in picked]


def score_invention(repo: Path | str, *, sql_dialect: str | None = None) -> InventionReport:
    """Extract ``repo`` and report how much of its call graph is fiction."""
    from orchestrator.pkg.extractor import RepoCodeExtractor

    root = Path(repo)
    if not root.is_dir():
        raise ValueError(f"{root}: not a directory")
    return find_invented_calls(RepoCodeExtractor(sql_dialect=sql_dialect).extract(root), root)


__all__ = [
    "InventedCall",
    "InventionReport",
    "find_invented_calls",
    "sample_edges",
    "score_invention",
]
