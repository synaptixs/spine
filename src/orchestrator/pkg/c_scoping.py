"""Separate programs' same-named C/C++ definitions get separate nodes, where nothing is lost (B142).

C and C++ name a file-scope symbol by its name alone (`c:loop`, `c:main`, `cpp:Fixture`), because
that is what the linker does *within one program*. A repository is often several programs: a
library's examples, a test suite of standalone executables. Each has its own `main` and its own
global `loop`, and they all merged into one node. libuv's `c:main` was claimed by 34 files; the
node had one file's provenance and another file's owning module, so anything grouped by owner put
it in an arbitrary component (SSPN-339).

**The rule (G5 decision D2): split only where the split loses nothing.** A name is re-keyed to one
id per defining file, `<prefix><file>::<name>` (the form a C `static` already has), when all of:

1. it is **defined** (a body, or a non-`extern` variable) in two or more source files;
2. every edge into it (and into its members) comes from one of the files that define it, and
   every edge out of it is written in one of them, so each edge has exactly one place to go.
   That includes a declaration's: a header or another source file that names it with a
   prototype or an `extern` CONTAINS it from its own module, and a declaration is a promise of
   *the* definition, which one the build links not being in the source.

Anything else stays one node, as before. That is the honest answer for platform alternatives:
libuv's `uv_close` is defined in `src/unix/core.c` and `src/win/handle.c` and called 305 times
from `test/`; which one a test links is a build decision, so splitting would have to drop those
calls or guess. Measured before building: libuv splits 117 of 305 multiply-defined names and keeps
188; fmt splits 16 of 17; no edge is lost.

**Why the front-end records per file.** :class:`~orchestrator.pkg.facts.FactBatch` keeps one node
per id, so by `finalize` the other files' copies of `c:main` are gone, and with them their
provenance. :meth:`ProgramScope.record` keeps each file's own node for the candidate names while
the walk reads them; :meth:`ProgramScope.split` uses those.

**What it does not touch:** a declaration and its one definition (`lib/api.h` + `lib/api.c`) stay
one node, because they are one entity. Header-only alternatives (`unix.h` / `win.h`) are never
split, by rule 1. In C++ a name one file declares more than once at file scope (an overload, or
a prototype beside its definition) stays whole, since which entity a call reaches is overload
resolution; a C++ `extern` global is a declaration, as in C. The clang post-pass spells a split
name by USR (`cpp:main`); `link_clang` maps a caller to the per-file id of the file it is written
in, and a target to the per-file id of the file holding its definition when the translation unit
sees that definition. Seen only as a declaration, the target is refused.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind

#: Files a program's definitions live in. A header is shared by whoever includes it, so a header
#: never makes a name per-program.
SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".c++")

#: Node kinds a file defines at file scope: functions, globals (`FIELD` with module CONTAINS) and
#: types (`struct`, `class`), whose members move with them.
_SCOPED = frozenset({NodeKind.FUNCTION, NodeKind.FIELD, NodeKind.TYPE})


def scoped_id(node_id: str, prefix: str, file: str) -> str:
    """The per-file id a split name takes: `c:main` defined in `prog1/main.c` is
    `c:prog1/main.c::main`, the form a C `static` already has."""
    return f"{prefix}{file}::{node_id[len(prefix) :]}"


@dataclass
class ProgramScope:
    """What each file defined at file scope, recorded during one extraction.

    ``prefix`` is the id namespace (`c:` or `cpp:`) and ``sep`` the member separator (`.` for C's
    `c:state.n`, `::` for C++'s `cpp:Fixture::go`).
    """

    prefix: str
    sep: str
    #: Whether one name can stand for several entities in one file (C++ overloads). Then a name
    #: a file declares more than once at file scope stays whole: which one a call reaches is
    #: overload resolution, which this pass does not do.
    overloads: bool = False
    #: ``name id -> {defining file: that file's node}``.
    defined: dict[str, dict[str, Node]] = field(default_factory=dict)
    #: ``(member id, file) -> that file's node``, for the members of a defined type.
    members: dict[tuple[str, str], Node] = field(default_factory=dict)
    #: Names some file declares more than once at file scope (with ``overloads``): never split.
    ambiguous: set[str] = field(default_factory=set)
    _noted: set[tuple[str, str]] = field(default_factory=set)
    #: The ids the last :meth:`split` re-keyed, so a front-end can re-key what it holds by id.
    moved: frozenset[str] = frozenset()

    def note(self, node_id: str, rel: str) -> None:
        """A front-end with ``overloads`` reports each file-scope declaration it emits; a name
        declared twice in one file (however laid out, one line included) is never split."""
        key = (node_id, rel)
        if key in self._noted:
            self.ambiguous.add(node_id)
        self._noted.add(key)

    def reset(self) -> None:
        """Forget everything recorded: called when an extraction starts, so one that ended before
        `finalize` (an unreadable file) leaves nothing for the next."""
        self.defined, self.members, self.ambiguous, self.moved = {}, {}, set(), frozenset()
        self._noted = set()

    def record(self, batch: FactBatch, module_id: str, rel: str) -> None:
        """Note what one file's batch defines at file scope, and each defined type's members."""
        nodes = {n.id: n for n in batch.nodes}
        source = rel.endswith(SOURCE_SUFFIXES)
        top: set[str] = set()
        declared_at: dict[str, set[int]] = {}
        for edge in batch.edges:
            if edge.kind is not EdgeKind.CONTAINS or edge.src != module_id:
                continue
            if edge.provenance is not None:
                declared_at.setdefault(edge.dst, set()).add(edge.provenance.line)
            node = nodes.get(edge.dst)
            if node is None or node.kind not in _SCOPED or not node.id.startswith(self.prefix):
                continue
            if node.id.startswith(f"{self.prefix}{rel}::"):
                continue  # a `static`: file-scoped already
            if source and not node.external:  # a declaration is refused by `split`'s edge rule
                self.defined.setdefault(node.id, {})[rel] = node
                top.add(node.id)
        if self.overloads:
            self.ambiguous.update(i for i, lines in declared_at.items() if len(lines) > 1)
        for member in _descendants(top, batch.edges):
            if member in nodes:
                self.members[(member, rel)] = nodes[member]

    def split(self, batch: FactBatch) -> FactBatch:
        """Re-key every name the rule allows (see the module docstring); the rest are left alone.

        Clears what was recorded, so a later extraction starts clean.
        """
        defined, members, ambiguous = self.defined, self.members, self.ambiguous
        self.reset()
        candidates = {
            name: files for name, files in defined.items() if len(files) >= 2 and name not in ambiguous
        }
        if not candidates:
            return batch
        ids = {n.id for n in batch.nodes}
        modules = {n.id for n in batch.nodes if n.kind is NodeKind.MODULE}

        # A name's family is what it CONTAINS, transitively: a type's members. Never ids that merely
        # start with the name, which caught `c:main.c` (a module) as a member of `c:main`.
        families = {name: {name} | (_descendants({name}, batch.edges) & ids) for name in sorted(candidates)}
        # An id can still be in two families: `struct A { int f(); }` and `namespace A { int f(); }`
        # both name `cpp:A::f`, which is then struct A's member *and* a candidate of its own. Such an
        # id has no single owner to move with, so every name it belongs to stays whole.
        claimants: dict[str, set[str]] = {}
        for name, fam in families.items():
            for member in fam:
                claimants.setdefault(member, set()).add(name)
        blocked = {name for names in claimants.values() if len(names) > 1 for name in names}
        owner = {member: next(iter(names)) for member, names in claimants.items() if len(names) == 1}
        for edge in batch.edges:
            for end, other in ((edge.dst, edge.src), (edge.src, edge.dst)):
                owned = owner.get(end)
                if owned is None or owned in blocked:
                    continue
                file = edge.provenance.file if edge.provenance is not None else None
                if file not in candidates[owned]:
                    blocked.add(owned)  # an edge with no single place to go: keep the name whole
                elif end != owned and (end, file) not in members:
                    blocked.add(owned)  # a member that file's own type does not have
                elif (
                    edge.kind is EdgeKind.CONTAINS
                    and end == edge.dst
                    and other not in modules
                    and owner.get(other) != owned
                ):
                    blocked.add(owned)  # contained by something outside the family
        split = {name: fam for name, fam in families.items() if name not in blocked}
        if not split:
            return batch
        moving = {member: name for name, fam in split.items() for member in fam}
        self.moved = frozenset(moving)

        def rekey(node_id: str, file: str) -> str:
            return scoped_id(node_id, self.prefix, file)

        out = FactBatch()
        for node in batch.nodes:
            if node.id not in moving:
                out.add_node(node)
        for name in sorted(split):
            for file, node in sorted(candidates[name].items()):
                out.add_node(replace(node, id=rekey(name, file)))
                for member in sorted(split[name] - {name}):
                    own = members.get((member, file))
                    if own is not None:
                        out.add_node(replace(own, id=rekey(member, file)))
        for edge in batch.edges:
            if edge.src not in moving and edge.dst not in moving:
                out.add_edge(edge)
                continue
            file = edge.provenance.file if edge.provenance is not None else ""
            src = rekey(edge.src, file) if edge.src in moving else edge.src
            dst = rekey(edge.dst, file) if edge.dst in moving else edge.dst
            out.add_edge(Edge(src, dst, edge.kind, edge.provenance))
        out.walk_health = batch.walk_health
        return out


def _descendants(roots: set[str], edges: Iterable[Edge]) -> set[str]:
    """Every id reachable from ``roots`` by CONTAINS, ``roots`` excluded."""
    children: dict[str, list[str]] = {}
    for edge in edges:
        if edge.kind is EdgeKind.CONTAINS:
            children.setdefault(edge.src, []).append(edge.dst)
    out: set[str] = set()
    stack = [c for r in roots for c in children.get(r, ())]
    while stack:
        node = stack.pop()
        if node not in out and node not in roots:
            out.add(node)
            stack.extend(children.get(node, ()))
    return out
