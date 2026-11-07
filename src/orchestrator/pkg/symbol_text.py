"""The words that stand for a symbol, read from its own source span: the text search's content legs rank on.

The graph's nodes carry no text (``facts.Node``: id, kind, name, language, provenance, external, test), and
search ranked on names and path words only, while a plain BM25 over whole files reads the code itself and
led Spine at top-10 on 250 SWE-bench issues (0.728 against 0.700). This module is the missing text,
**projected** from the graph rather than added to it (hybrid KG search, D3): every grounded node already
records ``file:line–end_line``, so the text of a symbol in any language is a slice of a file on disk.
Nothing here is a new fact, so ``facts.py``, the front-ends and the cache format are untouched (invariant
1).

Two recipes, fixed before measuring (pre-registration §3):

* **A** (:attr:`Recipe.NAME_SIGNATURE`) — the name, the qualified name, the path and the declaration's
  signature, read until its parentheses close.
* **B** (:attr:`Recipe.WITH_BODY`) — A plus the comment or decorators directly above the declaration and the
  first :data:`BODY_LINES` lines of the body (which is where a docstring sits), never past the symbol's own
  end.

A file that is gone, unreadable, too large, or reached through a path or link that leaves the repository
root yields the name and path only: never an exception, and never a byte from outside the root.
Deterministic: the same graph and tree give the same text, keyed in node-id order. Bump
:data:`RECIPE_VERSION` whenever the text a recipe produces changes, so a cache keyed on it
(``vector_cache``, PR2) never serves an old text's vector.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from pathlib import Path

from orchestrator.pkg.facts import Node, NodeKind
from orchestrator.pkg.store import FactStore

#: Bumped whenever a recipe's output changes for the same source.
RECIPE_VERSION = 1

#: Body lines recipe B reads after the signature.
BODY_LINES = 20

#: At most this many lines are read as one signature, however long its parameter list.
_SIGNATURE_MAX_LINES = 8

#: At most this many comment/decorator lines are read above a declaration.
_LEADING_MAX_LINES = 10

#: A file larger than this is not read (generated or vendored code; its name and path still count).
_MAX_FILE_BYTES = 2_000_000

_LEADING_MARKERS = ("#", "//", "/*", "*", "@", "--", "///", "'''", '"""')

#: The node kinds that are code a ticket can land in. ``Doc`` and ``Intent`` are not (B158).
CODE_KINDS = frozenset(
    {NodeKind.MODULE, NodeKind.TYPE, NodeKind.FUNCTION, NodeKind.FIELD, NodeKind.ENDPOINT, NodeKind.ENTITY}
)


class Recipe(str, Enum):
    """Which part of a symbol's source stands for it (pre-registration §3)."""

    NAME_SIGNATURE = "A"
    WITH_BODY = "B"


class SourceLines:
    """Reads a repository's files as lines, once each, refusing anything outside the root.

    ``roots`` maps a merged graph's repository keys (``Provenance.repo``) to their checkouts; ``root``
    serves the single-repository case and any key the map does not hold.
    """

    def __init__(self, root: Path | str, roots: Mapping[str, Path | str] | None = None) -> None:
        self._root = Path(root).resolve()
        self._roots = {k: Path(v).resolve() for k, v in (roots or {}).items()}
        self._cache: dict[tuple[str, str], list[str] | None] = {}

    def lines(self, file: str, repo: str = "") -> list[str] | None:
        """The file's lines, or ``None`` when it cannot or must not be read."""
        key = (repo, file)
        if key not in self._cache:
            self._cache[key] = self._read(self._roots.get(repo, self._root), file)
        return self._cache[key]

    @staticmethod
    def _read(root: Path, file: str) -> list[str] | None:
        if not file or Path(file).is_absolute():
            return None
        try:
            path = (root / file).resolve()
            # `resolve` follows links, so a link that points out of the root fails here like `../` does.
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
                return None
            return path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None


def _qualified(node: Node) -> str:
    return node.id.split(":", 1)[1] if ":" in node.id else node.id


def _signature_end(lines: list[str], start: int, stop: int) -> int:
    """Index one past the signature that begins at ``start``: read until its parentheses balance."""
    depth = 0
    end = start
    while end < stop and end - start < _SIGNATURE_MAX_LINES:
        line = lines[end]
        depth += line.count("(") - line.count(")")
        end += 1
        if depth <= 0:
            break
    return end


def _leading(lines: list[str], start: int) -> list[str]:
    """The comment or decorator lines directly above ``start``, top to bottom; a blank line ends them."""
    out: list[str] = []
    i = start - 1
    while i >= 0 and len(out) < _LEADING_MAX_LINES:
        stripped = lines[i].strip()
        if not stripped or not stripped.startswith(_LEADING_MARKERS):
            break
        out.append(stripped)
        i -= 1
    return out[::-1]


def symbol_text(node: Node, source: SourceLines, recipe: Recipe) -> str:
    """The text that stands for ``node`` under ``recipe``.

    The name and path alone when the source is unreadable or out of the root.
    """
    prov = node.provenance
    parts = [node.name, _qualified(node)]
    if prov is None:
        return "\n".join(parts)
    parts.append(prov.file)
    lines = source.lines(prov.file, prov.repo)
    if lines is None or not 1 <= prov.line <= len(lines):
        return "\n".join(parts)
    start = prov.line - 1
    stop = min(len(lines), prov.end_line) if prov.end_line is not None else len(lines)
    stop = max(stop, start + 1)
    if node.kind is NodeKind.MODULE:
        sig_end = start  # a module has no declaration line; its "body" is the top of the file
    else:
        sig_end = _signature_end(lines, start, stop)
        parts.extend(line.strip() for line in lines[start:sig_end])
    if recipe is Recipe.WITH_BODY:
        parts[3:3] = _leading(lines, start)
        body_stop = min(stop if prov.end_line is not None else len(lines), sig_end + BODY_LINES)
        parts.extend(line.strip() for line in lines[sig_end:body_stop] if line.strip())
    return "\n".join(parts)


def symbol_texts(
    store: FactStore,
    root: Path | str,
    recipe: Recipe,
    *,
    include_tests: bool = False,
    roots: Mapping[str, Path | str] | None = None,
) -> dict[str, str]:
    """Every grounded code node's text, keyed by node id in sorted order.

    Test code (``Node.test``) is left out unless asked for: a ticket lands in the product, and the
    traversal leg walks through tests on its own (D6). ``Doc`` and ``Intent`` nodes are never code
    (:data:`CODE_KINDS`).
    """
    source = SourceLines(root, roots)
    out: dict[str, str] = {}
    for node in sorted(store.nodes, key=lambda n: n.id):
        if not node.grounded or node.kind not in CODE_KINDS:
            continue
        if node.test and not include_tests:
            continue
        out[node.id] = symbol_text(node, source, recipe)
    return out


__all__ = [
    "BODY_LINES",
    "CODE_KINDS",
    "RECIPE_VERSION",
    "Recipe",
    "SourceLines",
    "symbol_text",
    "symbol_texts",
]
