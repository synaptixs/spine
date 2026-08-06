"""Tier-1 graph invariants — self-consistency checks that need no oracle.

The dangling-import bug (relative imports never joined) survived 204 tests
because every existing assertion checked *soundness* — what we assert is true —
and none checked *completeness* — what's true, we assert. These invariants are
the standing completeness detector: they run on any repo, need no ground
truth, and fail loudly when a front-end stops resolving what it should.

Checks, cheapest first:

- **dangling-edge** (error) — every edge endpoint exists as a node.
- **stale-provenance** (error) — every grounded node's ``file:line`` resolves
  to a real file and a line inside it.
- **orphan-rate** (error) — the share of first-party modules no other
  first-party module imports. 91% of a real package being "never imported" is
  how the bug looked from the outside; a healthy package is far below the
  threshold.
- **external-ratio** (error) — the share of ``IMPORTS`` edges still pointing
  at ``external`` targets. Near-100% on a multi-module repo means resolution
  is broken, not that the repo only uses third-party code.
- **phantom-module** (warning) — an ``external`` module whose basename
  collides with a first-party module's basename (``py:types`` next to
  ``py:click.types``). After the import join this is usually stdlib shadowing
  — worth a human's glance, not a CI failure; the rate checks above are the
  tripwires when it's systematic.
- **source-parity** (warning) — the source plainly declares something the graph
  holds no node of. Every other check asks whether the graph is self-consistent;
  this is the only one that asks whether it is *complete with respect to the
  source*, and it is the only class of check that can catch a front-end falling
  behind the vocabulary. A graph missing an entire node kind is perfectly
  self-consistent — which is how this repo shipped 77 route decorators and zero
  ``Endpoint`` nodes while ``pkg verify`` reported OK.

The rate checks only apply to languages with enough modules and import edges
to make a percentage meaningful; tiny fixtures and single-file scripts are
exempt by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.facts import EdgeKind, FactBatch, Node, NodeKind

# Rate checks need a population: fewer modules/edges than this and a percentage
# is noise, so the check is skipped rather than guessed at.
MIN_MODULES = 8
MIN_IMPORT_EDGES = 20
ORPHAN_RATE_LIMIT = 0.8
EXTERNAL_RATIO_LIMIT = 0.95
_MAX_EXAMPLES = 5

# Synthetic locators (live-DB schema nodes) aren't files; skip them in the
# provenance check.
_SYNTHETIC_PREFIXES = ("db://",)


@dataclass(frozen=True)
class VerifyIssue:
    """One failed invariant, aggregated per check (examples capped, count honest)."""

    check: str
    severity: str  # "error" | "warning"
    message: str


@dataclass(frozen=True)
class VerifyReport:
    issues: tuple[VerifyIssue, ...]

    @property
    def errors(self) -> tuple[VerifyIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[VerifyIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors


def _examples(items: list[str]) -> str:
    shown = ", ".join(items[:_MAX_EXAMPLES])
    rest = len(items) - _MAX_EXAMPLES
    return shown + (f", +{rest} more" if rest > 0 else "")


def _check_dangling_edges(batch: FactBatch, ids: set[str]) -> list[VerifyIssue]:
    dangling = [
        f"{e.src} -{e.kind.value}-> {e.dst}" for e in batch.edges if e.src not in ids or e.dst not in ids
    ]
    if not dangling:
        return []
    return [
        VerifyIssue(
            "dangling-edge",
            "error",
            f"{len(dangling)} edge(s) reference a node that doesn't exist: {_examples(dangling)}",
        )
    ]


def _check_provenance(batch: FactBatch, root: Path) -> list[VerifyIssue]:
    line_counts: dict[str, int | None] = {}

    def lines_in(rel: str) -> int | None:
        if rel not in line_counts:
            target = root / rel
            try:
                line_counts[rel] = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                line_counts[rel] = None
        return line_counts[rel]

    stale: list[str] = []
    for node in batch.nodes:
        prov = node.provenance
        if prov is None or not node.grounded or prov.file.startswith(_SYNTHETIC_PREFIXES):
            continue
        count = lines_in(prov.file)
        if count is None:
            stale.append(f"{node.id} @ {prov.file}:{prov.line} (missing file)")
        elif prov.line < 1 or prov.line > max(count, 1):
            # max(count, 1): an empty file's module node legitimately sits at line 1
            stale.append(f"{node.id} @ {prov.file}:{prov.line} ({count} lines)")
    if not stale:
        return []
    return [
        VerifyIssue(
            "stale-provenance",
            "error",
            f"{len(stale)} grounded node(s) whose provenance doesn't resolve: {_examples(stale)}",
        )
    ]


def _basename(node: Node) -> str:
    """The last path/dot segment of a module id's body — the collision key."""
    body = node.id.partition(":")[2]
    return body.rsplit(".", 1)[-1].rsplit("/", 1)[-1]


def _check_phantoms(batch: FactBatch) -> list[VerifyIssue]:
    first_party: dict[tuple[str, str], list[str]] = {}
    for node in batch.nodes:
        if node.kind is NodeKind.MODULE and node.grounded:
            first_party.setdefault((node.language, _basename(node)), []).append(node.id)
    phantoms: list[str] = []
    for node in batch.nodes:
        if node.kind is not NodeKind.MODULE or not node.external:
            continue
        collision = first_party.get((node.language, _basename(node)))
        if collision:
            phantoms.append(f"{node.id} (vs {collision[0]})")
    if not phantoms:
        return []
    return [
        VerifyIssue(
            "phantom-module",
            "warning",
            f"{len(phantoms)} external module(s) share a first-party module's basename "
            f"(stdlib shadowing, or an unjoined import): {_examples(phantoms)}",
        )
    ]


def _module_of(node_id: str, parents: dict[str, str], nodes: dict[str, Node]) -> Node | None:
    """Walk CONTAINS parents up to the owning ``Module`` node (cycle-safe)."""
    seen: set[str] = set()
    current: str | None = node_id
    while current is not None and current not in seen:
        seen.add(current)
        node = nodes.get(current)
        if node is not None and node.kind is NodeKind.MODULE:
            return node
        current = parents.get(current)
    return None


def _check_rates(batch: FactBatch, nodes: dict[str, Node]) -> list[VerifyIssue]:
    parents: dict[str, str] = {}
    for edge in batch.edges:
        if edge.kind is EdgeKind.CONTAINS:
            parents.setdefault(edge.dst, edge.src)

    modules_by_lang: dict[str, list[Node]] = {}
    for node in batch.nodes:
        if node.kind is NodeKind.MODULE and node.grounded:
            modules_by_lang.setdefault(node.language, []).append(node)

    edges_by_lang: dict[str, int] = {}
    external_by_lang: dict[str, int] = {}
    imported: dict[str, set[str]] = {}  # module id -> importing module ids
    for edge in batch.edges:
        if edge.kind is not EdgeKind.IMPORTS:
            continue
        src_mod = _module_of(edge.src, parents, nodes)
        if src_mod is None:
            continue
        lang = src_mod.language
        edges_by_lang[lang] = edges_by_lang.get(lang, 0) + 1
        dst = nodes.get(edge.dst)
        if dst is None or dst.external:
            external_by_lang[lang] = external_by_lang.get(lang, 0) + 1
            continue
        dst_mod = _module_of(edge.dst, parents, nodes)
        if dst_mod is not None and dst_mod.grounded and dst_mod.id != src_mod.id:
            imported.setdefault(dst_mod.id, set()).add(src_mod.id)

    issues: list[VerifyIssue] = []
    for lang, modules in sorted(modules_by_lang.items()):
        total_edges = edges_by_lang.get(lang, 0)
        if len(modules) < MIN_MODULES or total_edges < MIN_IMPORT_EDGES:
            continue
        orphans = [m for m in modules if not imported.get(m.id)]
        orphan_rate = len(orphans) / len(modules)
        if orphan_rate >= ORPHAN_RATE_LIMIT:
            issues.append(
                VerifyIssue(
                    "orphan-rate",
                    "error",
                    f"{lang}: {len(orphans)} of {len(modules)} first-party modules "
                    f"({orphan_rate:.0%}) are imported by nothing — import resolution is "
                    f"likely broken. E.g. {_examples([m.id for m in orphans])}",
                )
            )
        external_ratio = external_by_lang.get(lang, 0) / total_edges
        if external_ratio >= EXTERNAL_RATIO_LIMIT:
            issues.append(
                VerifyIssue(
                    "external-ratio",
                    "error",
                    f"{lang}: {external_ratio:.0%} of {total_edges} import edges still point "
                    f"at external targets — in-repo imports aren't joining.",
                )
            )
    return issues


# Source syntax that *must* produce a node kind, per language. Each pattern requires a
# literal argument / value, the same precision rule the extractors hold to: `@cache.get(key)`
# is not a route, and a computed `__tablename__` is not a table.
#
# Python and TypeScript are covered because they are the two front-ends that emit **no**
# `Endpoint` node at all, so they are where a web service goes silently unrepresented.
# Java and C# already emit endpoints; Go, C and C++ have no route idiom common enough to
# match without guessing. Adding a language here is a new entry, nothing else.
_ROUTE_SYNTAX = {
    "python": re.compile(r"@\w[\w.]*\.(?:route|get|post|put|patch|delete|head|options)\s*\(\s*[\"']"),
    # NestJS method decorators, plus Express/Fastify router calls whose path is a literal
    # starting with "/". That leading slash is what separates `app.get("/users", h)` from
    # the far more common `cache.get(key)` — without it this would fire on every Map.
    "typescript": re.compile(
        r"@(?:Get|Post|Put|Patch|Delete|Head|Options|All)\s*\("
        r"|\.\s*(?:get|post|put|patch|delete|all|head|options)\s*\(\s*[\"'`]/"
    ),
}
_ENTITY_SYNTAX = {
    "python": re.compile(r"^\s*__tablename__\s*=\s*[\"']", re.MULTILINE),
    # TypeORM's @Entity / Sequelize's @Table — the class-level marker, not a column.
    "typescript": re.compile(r"@(?:Entity|Table)\s*\("),
}


def _source_signals(batch: FactBatch, root: Path) -> dict[str, tuple[int, int]]:
    """language → (files declaring routes, files declaring tables).

    Only files the graph *already* knows about are read — the grounded ``Module``
    nodes' own provenance — so this never walks the tree a second time, and it
    regex-scans rather than re-parsing: the question is "does this source declare
    any?", not "which ones", so an AST pass would buy precision the check has no
    use for at several times the cost.
    """
    seen: set[str] = set()
    signals: dict[str, list[int]] = {}
    for node in batch.nodes:
        if node.kind is not NodeKind.MODULE or not node.grounded or node.provenance is None:
            continue
        route_re, entity_re = _ROUTE_SYNTAX.get(node.language), _ENTITY_SYNTAX.get(node.language)
        if route_re is None and entity_re is None:
            continue
        rel = node.provenance.file
        if rel in seen or rel.startswith(_SYNTHETIC_PREFIXES):
            continue
        seen.add(rel)
        try:
            source = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts = signals.setdefault(node.language, [0, 0])
        if route_re is not None and route_re.search(source):
            counts[0] += 1
        if entity_re is not None and entity_re.search(source):
            counts[1] += 1
    return {lang: (c[0], c[1]) for lang, c in signals.items()}


def _check_source_parity(batch: FactBatch, root: Path) -> list[VerifyIssue]:
    """Warn when the source declares a construct the graph holds no node of.

    Warning, never error: a repo may legitimately use a framework a front-end has
    not learned yet, and failing a build for that turns the check into something
    people switch off. Silence is the failure mode this exists to prevent, not noise.
    """
    kinds_by_lang: dict[str, set[NodeKind]] = {}
    for node in batch.nodes:
        kinds_by_lang.setdefault(node.language, set()).add(node.kind)

    issues: list[VerifyIssue] = []
    for lang, (route_files, entity_files) in sorted(_source_signals(batch, root).items()):
        present = kinds_by_lang.get(lang, set())
        for files, kind, what in (
            (route_files, NodeKind.ENDPOINT, "route declaration"),
            (entity_files, NodeKind.ENTITY, "__tablename__ declaration"),
        ):
            if files and kind not in present:
                issues.append(
                    VerifyIssue(
                        "source-parity",
                        "warning",
                        f"{lang}: {files} file(s) contain a {what} but the graph holds no "
                        f"{kind.value} node — this front-end doesn't extract them yet, so "
                        f"'what breaks if I change this?' under-reports.",
                    )
                )
    return issues


def verify_batch(batch: FactBatch, root: Path | str) -> VerifyReport:
    """Run every Tier-1 invariant; ``report.ok`` is False on any error."""
    nodes = {n.id: n for n in batch.nodes}
    issues = [
        *_check_dangling_edges(batch, set(nodes)),
        *_check_provenance(batch, Path(root)),
        *_check_rates(batch, nodes),
        *_check_phantoms(batch),
        *_check_source_parity(batch, Path(root)),
    ]
    return VerifyReport(tuple(issues))


__all__ = [
    "EXTERNAL_RATIO_LIMIT",
    "MIN_IMPORT_EDGES",
    "MIN_MODULES",
    "ORPHAN_RATE_LIMIT",
    "VerifyIssue",
    "VerifyReport",
    "verify_batch",
]
