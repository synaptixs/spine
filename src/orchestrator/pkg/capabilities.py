"""Which facts each language front-end can emit — read off the front-ends themselves.

The PKG's vocabulary is universal (``facts.py``), but the front-ends are not: Python emits
four of seven node kinds, C# emits six. A reader who doesn't know that reads
``impact_of(handler) -> []`` as *"nothing calls this"* rather than *"this front-end has no
``EXPOSES`` edge to offer"*. The difference matters — one is a safe refactor, the other is a
public API with clients outside the language.

**Why this is derived rather than written down.** A hand-maintained table is a claim about
code that drifts the moment a front-end grows a kind, and drifts silently: nothing fails. A
generated one is a *reading* of the code, and `tests/pkg/test_capabilities.py` fails the
build when the committed table stops matching. An earlier hand-authored attempt at this
matrix was measured 22% wrong, which is the whole argument.

**What it reports.** What each front-end's code is *capable* of emitting — every ``NodeKind``
/ ``EdgeKind`` its module names — not what your repository happens to contain. A front-end
that can emit ``Endpoint`` still emits none for a repo with no routes. The distinction is
deliberate: this table answers "would Spine see it?", and `pkg verify`'s ``source-parity``
check answers "did it, here?".

Deterministic and no-LLM, like every comprehension surface: same source in, same table out.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.facts import EdgeKind, NodeKind


@dataclass(frozen=True)
class FrontEnd:
    """A language front-end and the module + class implementing it."""

    language: str
    module: str
    cls: str


# In the order ``default_extractors()`` builds them: Python first because it is the only one
# that is always on (stdlib ``ast``, no extra), then the tree-sitter languages, then SQL.
FRONT_ENDS: tuple[FrontEnd, ...] = (
    FrontEnd("python", "extractor.py", "PythonExtractor"),
    FrontEnd("java", "java_extractor.py", "JavaExtractor"),
    FrontEnd("typescript", "typescript_extractor.py", "TypeScriptExtractor"),
    FrontEnd("csharp", "csharp_extractor.py", "CSharpExtractor"),
    FrontEnd("c", "c_extractor.py", "CExtractor"),
    FrontEnd("cpp", "cpp_extractor.py", "CppExtractor"),
    FrontEnd("go", "go_extractor.py", "GoExtractor"),
    FrontEnd("sql", "sql_extractor.py", "SqlExtractor"),
)

# Passes that add facts for *every* language, so their kinds belong in no front-end's column.
# Without them the matrix reads as "no language can do docs", which is exactly backwards.
# Only the module and the reason are written here; the kinds are derived, like the rest.
SHARED_PASSES: tuple[tuple[str, str], ...] = (
    ("doc_link.py", "documentation ingestion — runs for every language"),
    ("import_link.py", "the whole-repo import join"),
    ("data_layer_link.py", "a live database, via `mcp ingest-db`"),
)


@dataclass(frozen=True)
class Capability:
    """What one front-end can emit."""

    language: str
    node_kinds: tuple[str, ...]
    edge_kinds: tuple[str, ...]


def _kinds_in(source: str, *, keep_class: str | None) -> tuple[set[str], set[str]]:
    """``NodeKind.X`` / ``EdgeKind.Y`` named anywhere in ``source``.

    With ``keep_class``, other ``*Extractor`` classes are skipped so a module holding more
    than one — as ``extractor.py`` does, carrying the whole-repo ``RepoCodeExtractor``
    dispatcher beside the Python front-end — attributes only its own class. Module-level
    helpers are always kept: Java's ``Endpoint`` and C#'s ``Entity`` are built in free
    functions, not in the class.
    """
    tree = ast.parse(source)
    if keep_class is not None:
        tree.body = [
            stmt
            for stmt in tree.body
            if not (
                isinstance(stmt, ast.ClassDef) and stmt.name.endswith("Extractor") and stmt.name != keep_class
            )
        ]
    nodes: set[str] = set()
    edges: set[str] = set()
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Attribute) and isinstance(stmt.value, ast.Name):
            if stmt.value.id == "NodeKind":
                nodes.add(stmt.attr)
            elif stmt.value.id == "EdgeKind":
                edges.add(stmt.attr)
    return nodes, edges


def _delegate_modules(source: str) -> list[str]:
    """``pkg`` modules a front-end builds facts *through*, so their kinds count as its own.

    The SQL front-end is why this exists: it parses DDL and hands the result to
    ``schema.sql_source_to_facts``, which is where ``Field`` and ``REFERENCES`` are actually
    created. A module-local reading called SQL fieldless — and the runtime cross-check in
    ``tests/pkg/test_capabilities.py`` caught it, which is the argument for keeping that
    test alongside this one.

    ``facts`` is excluded (it *defines* the vocabulary, it doesn't emit it) and so are the
    other front-ends — ``cpp_extractor`` borrows helpers from ``c_extractor``, and inheriting
    C's whole column would be a fabrication. One level deep, deliberately: deeper would
    re-attribute half of ``pkg`` to whichever front-end imported it first.
    """
    out: list[str] = []
    for stmt in ast.walk(ast.parse(source)):
        if not isinstance(stmt, ast.ImportFrom) or not (stmt.module or "").startswith("orchestrator.pkg."):
            continue
        name = (stmt.module or "").rsplit(".", 1)[-1]
        if name == "facts" or name == "extractor" or name.endswith("_extractor"):
            continue
        if name not in out:
            out.append(name)
    return out


def _value_of(enum: type[NodeKind] | type[EdgeKind], member: str) -> str | None:
    """``"ENDPOINT"`` → ``"Endpoint"``; ``None`` for a member that no longer exists."""
    try:
        return str(enum[member].value)
    except KeyError:  # a renamed kind — report nothing rather than a stale name
        return None


def front_end_capabilities(*, package_dir: Path | None = None) -> tuple[Capability, ...]:
    """Read each front-end's source and report the kinds it names.

    ``package_dir`` overrides where the front-ends are read from (tests only).
    """
    root = package_dir or Path(__file__).resolve().parent
    out: list[Capability] = []
    for fe in FRONT_ENDS:
        path = root / fe.module
        if not path.is_file():  # source-stripped install — say nothing rather than guess
            raise FileNotFoundError(f"cannot read front-end source: {path}")
        source = path.read_text(encoding="utf-8")
        raw_nodes, raw_edges = _kinds_in(source, keep_class=fe.cls)
        for delegate in _delegate_modules(source):
            delegate_path = root / f"{delegate}.py"
            if not delegate_path.is_file():
                continue
            more_nodes, more_edges = _kinds_in(delegate_path.read_text(encoding="utf-8"), keep_class=None)
            raw_nodes |= more_nodes
            raw_edges |= more_edges
        nodes = sorted(v for m in raw_nodes if (v := _value_of(NodeKind, m)))
        edges = sorted(v for m in raw_edges if (v := _value_of(EdgeKind, m)))
        out.append(Capability(language=fe.language, node_kinds=tuple(nodes), edge_kinds=tuple(edges)))
    return tuple(out)


def _table(caps: tuple[Capability, ...], columns: list[str], pick: str) -> list[str]:
    header = f"| Front-end | {' | '.join(f'`{c}`' for c in columns)} |"
    rule = f"|---|{'---|' * len(columns)}"
    rows = [
        f"| `{c.language}` | " + " | ".join("✓" if col in getattr(c, pick) else "·" for col in columns) + " |"
        for c in caps
    ]
    return [header, rule, *rows]


def shared_pass_kinds(*, package_dir: Path | None = None) -> list[tuple[str, str, list[str]]]:
    """``(module, why it runs, kinds it emits)`` for the language-independent passes."""
    root = package_dir or Path(__file__).resolve().parent
    out: list[tuple[str, str, list[str]]] = []
    for module, why in SHARED_PASSES:
        path = root / module
        if not path.is_file():
            continue
        raw_nodes, raw_edges = _kinds_in(path.read_text(encoding="utf-8"), keep_class=None)
        kinds = sorted(v for m in raw_nodes if (v := _value_of(NodeKind, m))) + sorted(
            v for m in raw_edges if (v := _value_of(EdgeKind, m))
        )
        out.append((module, why, kinds))
    return out


def render_markdown(caps: tuple[Capability, ...] | None = None) -> str:
    """The matrix as it appears in ``KNOWLEDGE_GRAPH.md`` (✓ = emitted, · = not)."""
    caps = caps or front_end_capabilities()
    shared = [
        f"| `pkg/{module}` | {why} | {', '.join(f'`{k}`' for k in kinds)} |"
        for module, why, kinds in shared_pass_kinds()
    ]
    lines = [
        "**Nodes**",
        "",
        *_table(caps, [k.value for k in NodeKind], "node_kinds"),
        "",
        "**Edges**",
        "",
        *_table(caps, [k.value for k in EdgeKind], "edge_kinds"),
        "",
        "Read a `·` as *this front-end has no code that emits that kind* — not as *your repo",
        "has none*. A front-end that can emit `Endpoint` still emits none for a repo without",
        "routes; `pkg verify`'s `source-parity` check is what answers that question.",
        "",
        "`Doc` is empty down the whole column because no *language* produces it. These passes",
        "do, for every language, and they are why the matrix is not the full picture:",
        "",
        "| Pass | Runs for | Emits |",
        "|---|---|---|",
        *shared,
    ]
    return "\n".join(lines)
