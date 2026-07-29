"""Cross-file import resolution — the join no per-file front-end can do.

Every code front-end records an import as *text*: it creates an ``external``
placeholder module named for what the source wrote (``ts:./utils``,
``go:example.com/mod/pkg``) and links to it. Joining that text to the module it
denotes requires knowing every module in the repo — whole-repo knowledge a
per-file extractor doesn't have. :func:`link_imports` is that missing step: it
indexes the first-party ``Module`` nodes once, and for each ``IMPORTS`` edge
whose target is still ``external`` asks a small per-language matcher whether
the target is actually in-repo. A resolved edge is repointed at the real
module (and the orphaned placeholder dropped); an unresolved one is genuinely
third-party (``os``, ``react``, ``fmt``) and is left alone.

Only the matching rule is per-language:

- **python / java / csharp** — dotted names: the longest dotted prefix that is
  a first-party module wins. Exact ids already join via the ``FactBatch``
  dedup (a grounded node upgrades the placeholder); the prefix walk covers the
  rest — re-exports (``from click import echo`` where ``echo`` lives in
  ``click.utils``), nested classes, static imports.
- **typescript** — ``./`` / ``../`` specifiers resolved against the importing
  file's directory, with the extension stripped and ``index`` collapsed (the
  same normalisation as the front-end's ``module_name``). Bare specifiers are
  packages (or path aliases we can't see) and stay external.
- **go** — the import path is matched against the ``module`` directive in
  ``go.mod``; the remainder is the package directory.
- **c / cpp** — an include the front-end could not resolve is matched as a
  path-suffix of exactly one first-party translation unit (the ``-I
  include-dir`` case); an ambiguous suffix is left alone rather than guessed.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind

_DOTTED_PREFIXES = frozenset({"py", "java", "csharp"})
_C_PREFIXES = ("c", "cpp")
_TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx")


class _Index:
    """First-party module lookups, built once per batch."""

    def __init__(self, batch: FactBatch, root: Path) -> None:
        self.nodes: dict[str, Node] = {n.id: n for n in batch.nodes}
        self.modules: set[str] = {n.id for n in batch.nodes if n.kind is NodeKind.MODULE and n.grounded}
        # C-family module id bodies *are* repo-relative paths; kept sorted so
        # suffix matching is order-independent and deterministic.
        self.c_paths: dict[str, list[str]] = {}
        for prefix in _C_PREFIXES:
            marker = prefix + ":"
            self.c_paths[prefix] = sorted(
                mid[len(marker) :] for mid in self.modules if mid.startswith(marker)
            )
        self.go_module = self._read_go_module(root)

    @staticmethod
    def _read_go_module(root: Path) -> str:
        try:
            text = (root / "go.mod").read_text(encoding="utf-8")
        except OSError:
            return ""
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("module "):
                return line.split(None, 1)[1].strip().strip('"')
        return ""


def _match_dotted(dst_id: str, idx: _Index) -> str | None:
    prefix, _, body = dst_id.partition(":")
    if body.startswith("."):
        return None  # a relative import that escaped the scanned tree — never joinable
    parts = body.split(".")
    for i in range(len(parts), 0, -1):
        cand = f"{prefix}:{'.'.join(parts[:i])}"
        if cand in idx.modules:
            return None if cand == dst_id else cand
    return None


def _match_ts(edge: Edge, idx: _Index) -> str | None:
    spec = edge.dst.partition(":")[2]
    if not spec.startswith("."):
        return None  # bare specifier: a package, or a path alias we can't see
    src = idx.nodes.get(edge.src)
    if src is None or src.provenance is None:
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(src.provenance.file), spec))
    if path.startswith(".."):
        return None  # escapes the scanned tree
    for suffix in _TS_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    for cand in (path, path.removesuffix("/index")):
        mid = f"ts:{cand}"
        if mid in idx.modules:
            return mid
    return None


def _match_go(dst_id: str, idx: _Index) -> str | None:
    if not idx.go_module:
        return None
    imp = dst_id.partition(":")[2]
    if imp == idx.go_module:
        return "go:<root>" if "go:<root>" in idx.modules else None
    if imp.startswith(idx.go_module + "/"):
        mid = "go:" + imp[len(idx.go_module) + 1 :]
        if mid in idx.modules:
            return mid
    return None


def _match_c(dst_id: str, idx: _Index) -> str | None:
    raw = dst_id.partition(":")[2]
    if not raw or raw.startswith("/"):
        return None
    # Search both C-family prefixes: a .cpp file's include of a .h resolves to a
    # module the C front-end owns (and vice versa).
    hits = [
        f"{prefix}:{body}"
        for prefix in _C_PREFIXES
        for body in idx.c_paths[prefix]
        if body == raw or body.endswith("/" + raw)
    ]
    return hits[0] if len(hits) == 1 else None


def _resolve(edge: Edge, idx: _Index) -> str | None:
    prefix = edge.dst.partition(":")[0]
    if prefix in _DOTTED_PREFIXES:
        return _match_dotted(edge.dst, idx)
    if prefix == "ts":
        return _match_ts(edge, idx)
    if prefix == "go":
        return _match_go(edge.dst, idx)
    if prefix in _C_PREFIXES:
        return _match_c(edge.dst, idx)
    return None


def link_imports(batch: FactBatch, root: Path | str) -> FactBatch:
    """Return a batch whose in-repo ``IMPORTS`` edges point at real modules.

    Unresolvable targets (third-party, stdlib, aliases) come back unchanged, so
    wiring this into the extraction path is safe for every repo.
    """
    idx = _Index(batch, Path(root))
    if not idx.modules:
        return batch

    changed = False
    edges: list[Edge] = []
    for edge in batch.edges:
        if edge.kind is EdgeKind.IMPORTS:
            dst = idx.nodes.get(edge.dst)
            if dst is not None and dst.external:
                resolved = _resolve(edge, idx)
                if resolved is not None and resolved != edge.dst:
                    edges.append(Edge(edge.src, resolved, edge.kind, edge.provenance))
                    changed = True
                    continue
        edges.append(edge)
    if not changed:
        return batch

    # Drop placeholders orphaned by the repoint: an external node's only reason
    # to exist is the edge that referenced it.
    referenced = {e.src for e in edges} | {e.dst for e in edges}
    result = FactBatch()
    for node in batch.nodes:
        if node.external and node.id not in referenced:
            continue
        result.add_node(node)
    for edge in edges:
        result.add_edge(edge)
    return result


__all__ = ["link_imports"]
