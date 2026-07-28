"""Graph findings that need no new facts — only questions nobody was asking yet.

Phase 5 of the understand enhancement plan. Each of these falls out of the graph as
it already stands; what they have in common is that they answer a question a *reader*
has ("where do I start?", "what can I ignore?", "what's tangled?") rather than
describing what the extractor found.

Two of the seven ideas in Finding 10 aren't here, for reasons worth recording:

- **Tests → module mapping** shipped in Phase 3 as the module page's "Tested by" line.
- **Churn per module** cannot go in a *committed* artifact at all. It reads the last
  ~60 commits, so its value changes on every commit — including the commit that lands
  the knowledge base — which would make the bank stale the moment it was written and
  ``understand --check`` fail permanently. It stays in the ephemeral ``state`` report.

Everything here is deterministic and bounded: sorted output, honest caps, and no
claim the edges can't support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import EdgeKind, Node, NodeKind

if TYPE_CHECKING:
    from orchestrator.pkg.store import FactStore


# How each language marks "not part of the public surface". These are *language*
# conventions, not one universal rule — assuming Python's underscore everywhere
# reported 19,212 public vs 32 internal on a C codebase, which is a number that
# looks computed and means nothing.
_UNDERSCORE_LANGS = frozenset({"python", "typescript", "javascript"})
_C_LANGS = frozenset({"c", "cpp"})

#: Human-readable rule per language, so a page can say how it decided.
VISIBILITY_RULES = {
    "python": "a leading underscore",
    "typescript": "a leading underscore",
    "javascript": "a leading underscore",
    "c": "`static` (internal linkage)",
    "cpp": "`static` (internal linkage)",
    "go": "a lower-case initial (unexported)",
}


def is_public(node: Node) -> bool | None:
    """Is this symbol part of the surface another project could use?

    Returns ``None`` when the language gives no signal the graph can read — Java and
    C# express visibility with keywords the front-ends don't record, and guessing
    would be worse than declining to answer.

    Per language:

    - **Python / TypeScript / JavaScript** — a leading underscore on the symbol *or*
      on any segment of its owning module.
    - **C / C++** — ``static`` storage class, which the front-end already encodes by
      keying internal-linkage symbols as ``file.c::name``. This is the real linkage
      rule; C has no underscore convention, and applying Python's made the split
      meaningless.
    - **Go** — an upper-case initial is exported; that *is* the language rule.
    """
    body = node.id.partition(":")[2]
    if node.language in _C_LANGS:
        return "::" not in body
    if node.language == "go":
        return node.name[:1].isupper()
    if node.language in _UNDERSCORE_LANGS:
        return not any(seg.startswith("_") for seg in body.replace("/", ".").split(".") if seg)
    return None


@dataclass(frozen=True)
class ApiSplit:
    """The public surface against everything behind it."""

    public: list[Node]
    internal_count: int
    # Symbols in languages whose visibility the graph can't read (Java, C#). Counted
    # apart so a page never presents "everything is public" when it simply can't tell.
    unknown_count: int = 0
    # Languages actually classified, for naming the rule that was applied.
    languages: frozenset[str] = frozenset()

    @property
    def total(self) -> int:
        return len(self.public) + self.internal_count

    @property
    def rules(self) -> str:
        """ "a leading underscore" / "`static` (internal linkage)" — how it decided."""
        seen = {VISIBILITY_RULES[lang] for lang in sorted(self.languages) if lang in VISIBILITY_RULES}
        return ", ".join(sorted(seen))


def api_split(store: FactStore) -> ApiSplit:
    """Split first-party symbols into the public surface and the internals.

    "40 public symbols, 1,900 internal" reframes a 2,000-symbol repo as approachable:
    most of it is machinery you don't have to read to use the thing. Symbols whose
    language offers no readable signal are excluded from both counts rather than
    defaulted into one.
    """
    from orchestrator.knowledge.renderers import _under_tests

    public: list[Node] = []
    internal = 0
    unknown = 0
    languages: set[str] = set()
    for n in store.nodes:
        if n.kind not in (NodeKind.TYPE, NodeKind.FUNCTION) or not n.grounded or _under_tests(n):
            continue
        verdict = is_public(n)
        if verdict is None:
            unknown += 1
            continue
        languages.add(n.language)
        if verdict:
            public.append(n)
        else:
            internal += 1
    return ApiSplit(
        public=public,
        internal_count=internal,
        unknown_count=unknown,
        languages=frozenset(languages),
    )


def import_cycles(imports: dict[str, set[str]], *, limit: int | None = None) -> list[list[str]]:
    """Groups of modules that (transitively) import each other.

    A real architectural finding and a genuine bug class — and one that was impossible
    to detect before intra-package imports resolved, because the graph saw almost no
    first-party import edges at all.

    Returns the strongly-connected components of the module graph, each sorted, the
    list itself ordered largest-first then by name so the page diffs cleanly. Iterative
    Tarjan: a deep dependency chain would blow the recursion limit on a real repo.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    components: list[list[str]] = []

    for root in sorted(imports):
        if root in index:
            continue
        # (node, iterator over its successors) — an explicit call stack.
        work: list[tuple[str, list[str]]] = [(root, sorted(imports.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, successors = work[-1]
            if successors:
                nxt = successors.pop(0)
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(imports.get(nxt, ()))))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component))

    # Largest first: a twelve-module tangle matters more than a mutual pair. Caller caps
    # for display and reports the cut, so the full count stays available here.
    components.sort(key=lambda c: (-len(c), c))
    return components if limit is None else components[:limit]


@dataclass(frozen=True)
class OnboardingStep:
    module: Node
    why: str


def onboarding_path(
    store: FactStore,
    importers: dict[str, set[str]],
    entry_points: list[str],
    *,
    limit: int = 5,
) -> list[OnboardingStep]:
    """ "New here? Read these modules, in this order."

    Starts where the program starts (the modules holding declared entry points), then
    follows fan-in: the modules the most other modules depend on are the ones whose
    vocabulary the rest of the code assumes you already have. Each step says why it's
    there, because an unexplained reading list is just another ranking.
    """
    from orchestrator.knowledge.renderers import _is_test_module

    steps: list[OnboardingStep] = []
    chosen: set[str] = set()

    entry_files = {ep.split("@")[-1].strip().split(":")[0] for ep in entry_points if "@" in ep}
    modules = [
        n for n in store.nodes if n.kind is NodeKind.MODULE and n.grounded and not _is_test_module(n.name)
    ]
    for mod in sorted(modules, key=lambda n: (n.name, n.id)):
        if mod.provenance and mod.provenance.file in entry_files and mod.id not in chosen:
            chosen.add(mod.id)
            steps.append(OnboardingStep(mod, "where execution starts"))

    ranked = sorted(modules, key=lambda n: (-len(importers.get(n.id, ())), n.name, n.id))
    for mod in ranked:
        if len(steps) >= limit:
            break
        count = len(importers.get(mod.id, ()))
        if mod.id in chosen or not count:
            continue
        chosen.add(mod.id)
        steps.append(OnboardingStep(mod, f"{count} module{'s' if count != 1 else ''} depend on it"))
    return steps[:limit]


@dataclass
class DeadCode:
    """Symbols nothing appears to use — *candidates*, never verdicts."""

    candidates: list[Node] = field(default_factory=list)
    # Total found before capping the display list, and the pool it was drawn from —
    # a truncated list that doesn't say so implies it is the whole answer.
    total: int = 0
    considered: int = 0


def dead_code_candidates(store: FactStore, *, limit: int = 20) -> DeadCode:
    """Internal symbols with no caller the graph can find.

    Restricted to **internal** symbols on purpose. A public function with no in-repo
    caller is usually API — that's what public means — so listing it would be noise at
    best and wrong at worst. An internal one can only be called from inside this
    codebase, which is exactly the region the call graph covers well, so "nothing calls
    it" is a defensible thing to raise.

    Still only a candidate: call resolution skips ambiguous attribute chains, and
    nothing sees dynamic dispatch, registries, or reflection. Phase 3 is the cautionary
    tale — the same reasoning applied loosely reported ``click.Context`` as untested.
    """
    from orchestrator.knowledge.renderers import _under_tests

    called: set[str] = {e.dst for e in store.edges_of_kind(EdgeKind.CALLS)}
    # A subclass implicitly uses its base even with no call edge.
    called |= {e.dst for e in store.edges_of_kind(EdgeKind.IMPLEMENTS)}
    # So does a doc that names it: it's referenced, whatever the code does.
    called |= {e.dst for e in store.edges_of_kind(EdgeKind.MENTIONS)}

    out = DeadCode()
    for n in store.nodes:
        if n.kind not in (NodeKind.TYPE, NodeKind.FUNCTION) or not n.grounded or _under_tests(n):
            continue
        # Only *definitely* internal symbols qualify. `is_public` returns None where
        # the language hides visibility from us (Java, C#); treating that as "internal"
        # would put real API on a possibly-unused list.
        if is_public(n) is not False or n.name.startswith("__"):
            continue
        out.considered += 1
        if n.id not in called:
            out.candidates.append(n)
    out.candidates.sort(key=lambda n: (n.name, n.id))
    out.total = len(out.candidates)
    del out.candidates[limit:]
    return out


__all__ = [
    "ApiSplit",
    "DeadCode",
    "OnboardingStep",
    "api_split",
    "dead_code_candidates",
    "import_cycles",
    "is_public",
    "onboarding_path",
]
