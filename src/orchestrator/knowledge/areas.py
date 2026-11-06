"""Areas — the coarse component grouping, shared by `state` and the episteme.

An **area** is a component one zoom level above a module: the first two segments of a
module's path or dotted namespace (``src/smf/smf-sm.c`` → ``src/smf``; ``App.Api.Users``
→ ``App.Api``). A **zone** is one level coarser still — the first segment (``src``,
``App``).

This lives in one place on purpose. ``state``'s architecture flowchart and the episteme's
area pages both group by area, and if they each derived it their own way they would show
different architectures for the same commit — and a reader who noticed would stop trusting
both. One definition, two renderers.

The subtle rule, learned the hard way (see ``FactStore.owning_module``): a node's area comes from
the module it *lives in*, resolved by walking CONTAINS upward — never from its bare symbol
id.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from orchestrator.pkg.facts import Node, NodeKind
from orchestrator.pkg.kotlin_kmp import multiplatform_modules, source_set_of
from orchestrator.pkg.store import FactStore

#: How much of a project's dotted modules a prefix must cover before it counts as an
#: organisational prefix rather than architecture. Measured rather than picked: on the
#: Android validation app the vendor prefix (five reverse-DNS segments) covers
#: **94.4%** of first-party modules and the very next segment drops to **57.7%** — that
#: cliff is where the namespace stops naming the organisation and starts naming components.
#: On this repository the deepest majority prefix is `orchestrator` at **48.1%**, so the
#: rule is a no-op here, which is the point: `orchestrator.pkg` already *is* the area.
#:
#: The threshold sits in that measured gap rather than at its top edge, because the fraction
#: is sensitive to how many modules there are: one dissenting module out of six is 17%, and a
#: rule tuned to a 71-module repository would refuse to fire on a small one for the same
#: single outlier.
_PREFIX_COVERAGE = 0.75


def common_namespace_prefix(names: Iterable[str]) -> tuple[str, ...]:
    """The reverse-DNS prefix a project's modules share, as segments — ``()`` if none.

    A reverse-DNS namespace spends its first several segments on the *organisation*
    (``com.example.apps.<product>``) and only then names a component. Grouping by
    the first two segments therefore answers ``com.google`` for every module in the
    repository — one area containing the entire application, which is not an architecture.

    **Not "shared by every module", which is what this was specified as and does not
    survive contact with a real repository.** The validation app has exactly one
    first-party module declaring ``package androidx.test.uiautomator`` — a file extending a
    third-party namespace on purpose — and that single module drags the common prefix of
    all 71 down to nothing. A coverage threshold keeps the answer stable against outliers,
    and modules outside the prefix simply keep their own grouping.
    """
    dotted = sorted({n for n in names if "." in n and "/" not in n})
    if len(dotted) < 2:
        return ()
    split = [n.split(".") for n in dotted]
    best: tuple[str, ...] = ()
    for depth in range(1, max(len(s) for s in split)):
        # Only modules with something left after the prefix can vote for it: a prefix that
        # consumes a module whole would leave it with no name to be grouped under.
        counts: Counter[tuple[str, ...]] = Counter(tuple(s[:depth]) for s in split if len(s) > depth)
        if not counts:
            break
        candidate, hits = counts.most_common(1)[0]
        if hits / len(dotted) < _PREFIX_COVERAGE:
            break
        best = candidate
    return best


def store_namespace_prefix(nodes: Iterable[Node]) -> tuple[str, ...]:
    """The namespace prefix for a whole node set — **the** definition, for every caller.

    :func:`common_namespace_prefix` takes names, so each caller had to decide which
    modules got to vote, and they did not agree: ``state`` and ``AreaIndex`` voted over
    every non-external module while ``collect_areas`` voted over non-*test* modules only.
    A prefix is decided by a majority of the voters, so a different electorate is a
    different prefix — and then ``state`` and ``understand`` draw two different
    architectures for the same commit, which is precisely what this module's docstring
    says it exists to prevent. Measured on this repository before the fix: 103 area
    labels one way, 386 the other.

    Tests vote. They are modules of the repository and they carry its namespace, so they
    are evidence about what that namespace is; whether a test area is *drawn* is a
    separate decision each renderer still makes for itself.
    """
    return common_namespace_prefix(
        node.name for node in nodes if node.kind is NodeKind.MODULE and not node.external
    )


def area_of_name(name: str, prefix: tuple[str, ...] = ()) -> str:
    """Group a module name into its area (a coarse component).

    Slash-path modules (C/C++ translation units, e.g. ``src/smf/smf-sm.c``) group by
    their first two path segments (``src/smf``); dotted namespaces (Python/Java/C#/Kotlin)
    group by the first two dotted segments (``App.Api``).

    ``prefix`` — from :func:`common_namespace_prefix` — is dropped first, so a reverse-DNS
    project groups by the segments that actually differ (``core.data``, ``feature.foryou``)
    instead of by its vendor prefix. A module that does not carry the prefix is untouched.
    """
    if "/" in name:
        return "/".join(name.split("/")[:2])
    segments = name.split(".")
    if prefix and tuple(segments[: len(prefix)]) == prefix:
        # `or segments[-1:]`: a module that *is* the prefix keeps its own last segment
        # rather than becoming the empty area.
        segments = segments[len(prefix) :] or segments[-1:]
    return ".".join(segments[:2])


def zone_of(area: str) -> str:
    """The architectural zone an area belongs to — its first path/namespace segment
    (``src/smf`` → ``src``; ``App.Api`` → ``App``)."""
    return area.split("/")[0].split(".")[0]


#: The prefix the Gradle reader mints module ids under (kotlin-support-roadmap.md D11).
_GRADLE_PREFIX = "gradle:"


def build_module_paths(nodes: Iterable[Node]) -> tuple[str, ...]:
    """Gradle module directories, longest first, for :func:`area_of_file`.

    Empty for a repository with no Gradle build, which is what keeps this a no-op
    for every other language.
    """
    paths = {
        node.id[len(_GRADLE_PREFIX) :]
        for node in nodes
        if node.id.startswith(_GRADLE_PREFIX) and node.kind is NodeKind.MODULE
    }
    paths.discard("<root>")  # the root project owns no sources of its own
    # Longest first, then alphabetically. Sorting a *set* by length alone leaves ties in
    # iteration order, which for a set of strings is hash order and so varies with
    # PYTHONHASHSEED — and this tuple decides which module `area_of_file` matches a file
    # to, on a path CLAUDE.md requires to be deterministic.
    return tuple(sorted(paths, key=lambda path: (-len(path), path)))


def area_of_file(
    file: str, module_paths: tuple[str, ...], multiplatform: frozenset[str] = frozenset()
) -> str | None:
    """The Gradle module a source file belongs to, by path — or ``None``.

    This is the whole of D11's "link through provenance path only": a Gradle module
    is a *directory*, so a file under ``core/data/src/main/java/...`` belongs to
    ``core/data`` and nothing needs to be invented to say so. No ownership edge is
    fabricated, because Gradle modules do not contain packages — several modules
    routinely publish into the same one.

    Longest match wins, so ``core/data-test`` is not swallowed by ``core/data``.

    A module named in ``multiplatform`` is split one level finer, by **source set**
    (``shared/commonMain``, ``shared/iosMain``) — D17. In a KMP module the source set is
    the architectural boundary that matters: ``commonMain`` is the shared contract and
    each platform set implements it, and collapsing them into one component hides the
    only structure such a module has. Ordinary Android modules are deliberately left
    whole, because their source sets (``main``/``test``/``androidTest``) are a build
    convention rather than a component split.
    """
    for module_path in module_paths:  # already longest-first
        if file == module_path or file.startswith(f"{module_path}/"):
            if module_path in multiplatform:
                source_set = source_set_of(file)
                return f"{module_path}/{source_set}" if source_set else module_path
            return module_path
    return None


class AreaIndex:
    """Resolves any node to the area it lives in.

    Which module a node belongs to is the store's one answer (``FactStore.owning_module``);
    this class only names the area that module is in. It used to walk ``CONTAINS`` itself and
    fall back to the node's file path, so a route (which no front-end gives a parent) landed in
    ``src/app`` while its module was ``app.web`` (KG track P5).
    """

    def __init__(self, store: FactStore) -> None:
        self._store = store
        # Resolved once per store: the prefix is a property of the whole module set, so
        # deriving it per node would be both wrong (one node knows nothing about the set)
        # and quadratic.
        self.prefix = store_namespace_prefix(store.nodes)

    def module_of(self, node: Node) -> Node | None:
        """The module a node belongs to: itself, if it already is one."""
        return self._store.owning_module(node.id)

    def area_of(self, node: Node) -> str:
        """The component a node lives in.

        Its module's name (dotted namespace / file path). When the graph holds no module for it
        at all, the source file it is defined in, and **never** the bare symbol id: C/C++ ids
        are symbols (``cpp:HSL2RGB``), not locations, so id-grouping would make every function
        its own area (and, via ``zone_of``, its own zone), flooding any layout with thousands
        of one-function entries.
        """
        mod = self._store.owning_module(node.id)
        name = mod.name if mod is not None else None
        if name is None and node.provenance is not None:
            name = node.provenance.file
        if not name:
            name = node.id.split(":", 1)[-1]
        return area_of_name(name, self.prefix)


__all__ = [
    "AreaIndex",
    "area_of_name",
    "common_namespace_prefix",
    "store_namespace_prefix",
    "multiplatform_modules",
    "zone_of",
]
