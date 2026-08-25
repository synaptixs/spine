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

**The same shape exists in four other front-ends, and this module could not see it.** The
Python detector keys on an *external, single-segment* target, because that is what the
Python front-end produced for an unresolved bare name. TypeScript, Go, C++ and C# do not
produce that: they resolve the shadowed name against their file-level tables and emit an
edge to a **real first-party node** — `ts:a.outer -CALLS-> ts:a.send` where `send` is the
caller's own parameter. Nothing dangles, `pkg verify` reports zero, and the corpus scores
1.00 because no fixture carried the shape. So the detector is stated language-neutrally
here, over :mod:`orchestrator.pkg.scope`:

    a ``CALLS`` edge is invented when the call site at ``file:line`` is a **bare-identifier**
    call whose name matches the target, and that name is **bound inside the calling
    function**.

Both halves are load-bearing. Without the bare-call test, ``this.send()`` on a line that also
declares a local ``send`` would be flagged. Without the *inside the function* test, every
correct call to a file-level definition would be, since a file-level ``function send()`` binds
``send`` too — the difference between a declaration and a shadow is which scope holds it.

C is the control: it already refuses these (``c_extractor._bound_names``), and it is walked
here rather than assumed, by an independent implementation — see the preamble of
:mod:`orchestrator.pkg.scope` for why the oracle must not reuse the extractor's own answer.

Java and SQL are **excluded with a reason** rather than reported as clean; ``status`` carries
it, because "0" and "not measured" are the two readings this project keeps confusing.

This module **measures only**. It changes no fact the extractor emits; the graph is
byte-identical with and without it. Fixing the front-ends is a separate phase, which this
number exists to prove.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch

_MAX_EXAMPLES = 15


@dataclass(frozen=True)
class InventedCall:
    """A ``CALLS`` edge to a name that is bound in the caller's own scope."""

    src: str
    dst: str
    file: str
    line: int
    name: str
    language: str = ""

    def __str__(self) -> str:
        return f"{self.file}:{self.line} {self.src} -CALLS-> {self.dst} ({self.name} is local)"


#: A language was looked at and answered; was excluded for a stated reason; or has no walker
#: and was **not measured**. Only ``MEASURED`` licenses reading a count of 0 as "clean".
MEASURED = "measured"
NOT_APPLICABLE = "not-applicable"
UNWALKED = "unwalked"


@dataclass(frozen=True)
class LanguageInvention:
    """One front-end's answer, with the standing of that answer attached.

    ``status`` exists because this project's recurring failure is a zero that means "nothing
    ran". A row is only evidence of a clean call graph when ``status == MEASURED``.
    """

    language: str
    status: str
    reason: str = ""
    invented: tuple[InventedCall, ...] = ()
    total_calls: int = 0
    examined: int = 0
    unexamined: int = 0
    #: Edges whose call site is a bare identifier — the only ones a shadow can reach. The
    #: honest denominator: `0 of 1677 CALLS` reads as a far wider clean sweep than `0 of 214
    #: bare calls`, and only the second is what was actually at risk.
    shadowable: int = 0

    @property
    def rate(self) -> float | None:
        return len(self.invented) / self.total_calls if self.total_calls else None

    @property
    def shadowable_rate(self) -> float | None:
        return len(self.invented) / self.shadowable if self.shadowable else None


@dataclass(frozen=True)
class InventionReport:
    """How much of the call graph is fiction."""

    invented: tuple[InventedCall, ...]
    total_calls: int
    external_calls: int
    candidates: int
    unexamined: int
    by_language: tuple[LanguageInvention, ...] = ()

    @property
    def measured_languages(self) -> tuple[str, ...]:
        return tuple(e.language for e in self.by_language if e.status == MEASURED)

    @property
    def unmeasured_languages(self) -> tuple[str, ...]:
        """Languages carrying ``CALLS`` edges that nothing here examined."""
        return tuple(e.language for e in self.by_language if e.status == UNWALKED and e.total_calls)

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


def _terminal_name(node_id: str) -> str:
    """The name as it is written at a bare call site, from a target id.

    Ids are language-prefixed and then segmented differently per front-end
    (``ts:a.send``, ``cpp:Ns::func``, ``go:<root>.Send``, ``ts:react:useState``), so the
    name is whatever follows the last separator of either kind.
    """
    body = node_id.partition(":")[2] or node_id
    for sep in (".", ":"):
        body = body.rpartition(sep)[2] or body
    return body


def _python_invention(
    calls: list[Edge], external: set[str], base: Path
) -> tuple[LanguageInvention, int, int]:
    """The original detector, unchanged: an external single-segment target bound in scope.

    Kept as its own path rather than folded into the tree-sitter walk. Python's front-end
    fails *differently* — it emitted `py:<name>`, an id for a module that does not exist —
    and this is the number the committed scoreboard carries. A rewrite that moved it would
    make the widening unreviewable.
    """
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
                InventedCall(edge.src, edge.dst, edge.provenance.file, edge.provenance.line, name, "python")
            )
    return (
        LanguageInvention(
            language="python",
            status=MEASURED,
            invented=tuple(invented),
            total_calls=len(calls),
            examined=len(candidates) - unexamined,
            unexamined=unexamined,
            shadowable=len(candidates) - unexamined,
        ),
        len(candidates),
        unexamined,
    )


def _walked_invention(language: str, calls: list[Edge], base: Path) -> LanguageInvention:
    """A tree-sitter front-end's answer: bare call, name matches target, name bound in-function."""
    from orchestrator.pkg import scope as scope_mod

    cache: dict[str, scope_mod.FileScopes | None] = {}

    def scopes_for(rel: str) -> scope_mod.FileScopes | None:
        if rel not in cache:
            path = base / rel
            try:
                cache[rel] = scope_mod.scopes_for_source(path.read_bytes(), language, path.suffix)
            except (OSError, KeyError, RuntimeError, ValueError):
                cache[rel] = None
        return cache[rel]

    invented: list[InventedCall] = []
    examined = unexamined = shadowable = 0
    for edge in calls:
        if edge.provenance is None:
            unexamined += 1
            continue
        scopes = scopes_for(edge.provenance.file)
        if scopes is None:
            unexamined += 1
            continue
        line = edge.provenance.line
        name = _terminal_name(edge.dst)
        examined += 1
        if name not in scopes.bare_call_names(line):
            # A member call (`this.m()`, `ns.f()`) — a different resolution question, and one
            # a scope test cannot answer. Looked at and cleared, but never at risk, so it is
            # not part of the denominator this oracle can claim.
            continue
        shadowable += 1
        if name in scopes.shadowed(line):
            invented.append(InventedCall(edge.src, edge.dst, edge.provenance.file, line, name, language))
    return LanguageInvention(
        language=language,
        status=MEASURED,
        invented=tuple(invented),
        total_calls=len(calls),
        examined=examined,
        unexamined=unexamined,
        shadowable=shadowable,
    )


def find_invented_calls(batch: FactBatch, root: Path | str) -> InventionReport:
    """Every ``CALLS`` edge whose target is a name bound inside the calling function.

    Per language, because "0" is only meaningful next to which front-ends were looked at.
    """
    from orchestrator.pkg import scope as scope_mod

    base = Path(root)
    external = {n.id for n in batch.nodes if n.external}
    calls = [e for e in batch.edges if e.kind is EdgeKind.CALLS]

    # The caller's own node names the front-end that emitted the edge — exact, and independent
    # of which optional extras happen to be installed on this machine.
    node_language = {n.id: n.language for n in batch.nodes}
    by_lang: dict[str, list[Edge]] = {}
    for edge in calls:
        by_lang.setdefault(node_language.get(edge.src, ""), []).append(edge)

    roster: list[LanguageInvention] = []
    invented: list[InventedCall] = []
    candidates = unexamined = 0
    for language in sorted(by_lang):
        edges = by_lang[language]
        if language == "python":
            entry, cand, unex = _python_invention(edges, external, base)
            candidates += cand
            unexamined += unex
        elif language in scope_mod.WALKERS:
            entry = _walked_invention(language, edges, base)
            candidates += entry.examined + entry.unexamined
            unexamined += entry.unexamined
        elif language in scope_mod.NOT_APPLICABLE:
            entry = LanguageInvention(
                language=language,
                status=NOT_APPLICABLE,
                reason=scope_mod.NOT_APPLICABLE[language],
                total_calls=len(edges),
            )
        else:
            entry = LanguageInvention(
                language=language or "<unattributed>",
                status=UNWALKED,
                reason="no scope walker — these edges were not examined",
                total_calls=len(edges),
                unexamined=len(edges),
            )
            unexamined += len(edges)
        roster.append(entry)
        invented.extend(entry.invented)

    return InventionReport(
        invented=tuple(sorted(invented, key=lambda i: (i.file, i.line, i.dst))),
        total_calls=len(calls),
        external_calls=sum(1 for e in calls if e.dst in external),
        candidates=candidates,
        unexamined=unexamined,
        by_language=tuple(roster),
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
    "MEASURED",
    "NOT_APPLICABLE",
    "UNWALKED",
    "InventedCall",
    "InventionReport",
    "LanguageInvention",
    "find_invented_calls",
    "sample_edges",
    "score_invention",
]
