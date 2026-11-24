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
split, by rule 1. The clang post-pass resolves USRs to `c:<name>`; for a split name that id no
longer exists, so `link_clang`'s existence check drops the edge instead of inventing one.
"""

from __future__ import annotations

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
    #: ``name id -> {defining file: that file's node}``.
    defined: dict[str, dict[str, Node]] = field(default_factory=dict)
    #: ``(member id, file) -> that file's node``, for the members of a defined type.
    members: dict[tuple[str, str], Node] = field(default_factory=dict)
    #: The ids the last :meth:`split` re-keyed, so a front-end can re-key what it holds by id.
    moved: frozenset[str] = frozenset()

    def record(self, batch: FactBatch, module_id: str, rel: str) -> None:
        """Note what one file's batch defines at file scope, and each defined type's members."""
        nodes = {n.id: n for n in batch.nodes}
        source = rel.endswith(SOURCE_SUFFIXES)
        top: set[str] = set()
        for edge in batch.edges:
            if edge.kind is not EdgeKind.CONTAINS or edge.src != module_id:
                continue
            node = nodes.get(edge.dst)
            if node is None or node.kind not in _SCOPED or not node.id.startswith(self.prefix):
                continue
            if node.id.startswith(f"{self.prefix}{rel}::"):
                continue  # a `static`: file-scoped already
            if source and not node.external:  # a declaration is refused by `split`'s edge rule
                self.defined.setdefault(node.id, {})[rel] = node
                top.add(node.id)
        for node in nodes.values():
            if any(node.id.startswith(t + self.sep) for t in top):
                self.members[(node.id, rel)] = node

    def split(self, batch: FactBatch) -> FactBatch:
        """Re-key every name the rule allows (see the module docstring); the rest are left alone.

        Clears what was recorded, so a later extraction starts clean.
        """
        defined, members = self.defined, self.members
        self.defined, self.members = {}, {}
        self.moved = frozenset()
        candidates = {name: files for name, files in defined.items() if len(files) >= 2}
        if not candidates:
            return batch
        ids = {n.id for n in batch.nodes}
        modules = {n.id for n in batch.nodes if n.kind is NodeKind.MODULE}

        def family(name: str) -> set[str]:
            return {name} | {i for i in ids if i.startswith(name + self.sep)}

        families = {name: family(name) for name in sorted(candidates)}
        owner = {member: name for name, fam in families.items() for member in fam}
        blocked: set[str] = set()
        for edge in batch.edges:
            for end, other in ((edge.dst, edge.src), (edge.src, edge.dst)):
                name = owner.get(end)
                if name is None or name in blocked:
                    continue
                file = edge.provenance.file if edge.provenance is not None else None
                if file not in candidates[name]:
                    blocked.add(name)  # an edge with no single place to go: keep the name whole
                elif end != name and (end, file) not in members:
                    blocked.add(name)  # a member that file's own type does not have
                elif (
                    edge.kind is EdgeKind.CONTAINS
                    and end == edge.dst
                    and other not in modules
                    and owner.get(other) != name
                ):
                    blocked.add(name)  # contained by something outside the family
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
