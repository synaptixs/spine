"""Investigation brief (C4): a ticket × the codebase, before any design.

Answers the three questions a senior engineer asks *before* proposing a change,
grounded and deterministic (no LLM):

* **Where does this land in the code?** — lexical retrieval over the PKG
  (`GroundedRetriever.relevant_symbols`) surfaces the real symbols a ticket's
  words point at, with `file:line` and how many callers each has (touch-risk).
* **What project knowledge is relevant?** — the committed `episteme/` domain
  model + glossary (`memory_bank_grounding`), so the brief speaks the codebase's
  own language.
* **Has this been done before?** — cross-run *prior notes* (conventions/pitfalls/
  fixes learned on past runs). These live in the registry DB, so they're passed
  in best-effort by the caller; the brief renders them when present and is silent
  when not (the CLI runs zero-infra and simply omits them).

The brief is the connective tissue between intake (a `jira://` ticket, C3) and
design (C1): research first, then design with the findings in hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import NodeKind


@dataclass(frozen=True)
class Landing:
    """One place in the code a ticket lexically lands."""

    name: str
    where: str  # "file:line"
    kind: str  # Function | Type | Module | …
    callers: int
    module: str  # owning module (touch-risk context)
    #: Dependents in **other** repositories — what breaks elsewhere if this changes.
    #:
    #: `callers` counts inbound ``CALLS`` and nothing else, which is right for a function and
    #: catastrophic for an HTTP handler: nothing in the source *calls* one, so it reports
    #: **0 callers** while a client in another service depends on it entirely. Reading that as
    #: "nothing depends on this" is the most dangerous answer the graph can give, and it is the
    #: exact question a multi-repo graph exists to answer. Computed from ``impact_of``, which
    #: follows ``CALLS`` then ``EXPOSES`` then ``CONSUMES`` — so a handler reaches the endpoint
    #: it serves and then the code, anywhere, that calls it.
    cross_repo: int = 0
    #: Which repository, in a merged multi-repo graph. Empty for the single-repo case.
    #:
    #: Not decoration. Module *names* are not scoped — only ids are — so two services that
    #: both have `app.models` produce two landing sites reading `app.models`, and a reader
    #: cannot tell which checkout to open. `where` does not disambiguate either: both say
    #: `app/models.py:14`.
    repo: str = ""
    #: Tickets this symbol was **last changed for**, from `SERVES`. Empty unless the recorded
    #: intent tier was scanned (`--intents`), and empty is *"not scanned or not attributed"* —
    #: never *"no prior work"*. The distinction is why the report states coverage once rather
    #: than leaving a reader to infer it from blanks.
    intents: tuple[str, ...] = ()

    @property
    def location(self) -> str:
        """`repo:file:line` when the repo is known, else `file:line`.

        Rendered only. `where` keeps its shape because six call sites parse it back with
        `split(":", 1)[0]` to recover the file — see `pkg/facts.Provenance`.
        """
        return f"{self.repo}:{self.where}" if self.repo and self.where else self.where


@dataclass
class Investigation:
    title: str
    problem: str
    landing: list[Landing] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)  # distinct owning modules
    #: Repositories the landing sites fall in, when the graph is merged. Empty single-repo.
    repos: list[str] = field(default_factory=list)
    #: Symbols that matched but were cut by ``max_symbols``. Bounded honestly: a truncated
    #: list must read as "top N of M", never as the complete answer (`CLAUDE.md` invariant 7).
    elided: int = 0
    knowledge: str = ""  # episteme excerpt, or ""
    prior_notes: list[str] = field(default_factory=list)  # cross-run recall, best-effort
    grounded: bool = False  # the PKG had grounded nodes


def _owning_module(store: FactStore, node_id: str, parents: dict[str, str]) -> str:
    """Walk CONTAINS upward to the owning MODULE; fall back to the provenance file."""
    cur = node_id
    for _ in range(16):  # cap the walk; graphs can't nest this deep, but never loop
        parent = parents.get(cur)
        if parent is None:
            break
        pnode = store.node(parent)
        if pnode is not None and pnode.kind is NodeKind.MODULE:
            return pnode.name
        cur = parent
    node = store.node(node_id)
    return (node.provenance.file if node and node.provenance else "") or ""


def _cross_repo_dependents(store: FactStore, node_id: str, repo: str) -> int:
    """How many symbols in *other* repositories depend on this one, transitively.

    Zero for a single-repo graph, where every id is unscoped and there is no "other". The walk
    is `impact_of`'s, so it crosses a boundary the only way the graph allows: through the
    endpoint a handler serves and on to whatever consumes it.
    """
    if not repo:
        return 0
    from orchestrator.pkg.scoping import unscope_id

    return sum(1 for node, _ in store.impact_of(node_id) if unscope_id(node.id)[0] not in ("", repo))


def build_investigation(
    title: str,
    problem: str,
    *,
    store: FactStore,
    root: Path | str | None = None,
    prior_notes: list[str] | None = None,
    max_symbols: int = 10,
) -> Investigation:
    """Research ``title``/``problem`` against the PKG + episteme. Deterministic."""
    from orchestrator.pkg.retrieval import GroundedRetriever
    from orchestrator.pkg.scoping import unscope_id

    retriever = GroundedRetriever(store)
    # One extra, so `elided` can distinguish "these are all of them" from "this is the top N".
    symbols = retriever.relevant_symbols(f"{title}\n{problem}", limit=max_symbols + 1)
    elided = max(0, len(symbols) - max_symbols)
    symbols = symbols[:max_symbols]
    parents = store.parents_index()

    landing: list[Landing] = []
    areas: list[str] = []
    repos: list[str] = []
    for n in symbols:
        module = _owning_module(store, n.id, parents)
        repo, _ = unscope_id(n.id)
        landing.append(
            Landing(
                name=n.name,
                where=str(n.provenance) if n.provenance else "",
                kind=n.kind.value,
                callers=len(store.callers_of(n.id)),
                module=module,
                repo=repo,
                cross_repo=_cross_repo_dependents(store, n.id, repo),
                # Sorted so the brief is byte-identical for a given commit: `SERVES` edges come
                # out in blame order, which is stable but not meaningful, and a brief that
                # reorders between runs cannot be diffed.
                intents=tuple(sorted(i.name for i in store.intents_for(n.id))),
            )
        )
        # Areas are qualified by repo, or two services that both have `app.models` collapse
        # into one area and the brief claims a change is narrower than it is.
        area = f"{repo}:{module}" if repo and module else module
        if area and area not in areas:
            areas.append(area)
        if repo and repo not in repos:
            repos.append(repo)

    knowledge = ""
    if root is not None:
        from orchestrator.knowledge.access import memory_bank_grounding

        knowledge = memory_bank_grounding(root)

    return Investigation(
        title=title,
        problem=problem.strip(),
        landing=landing,
        areas=areas,
        knowledge=knowledge,
        prior_notes=list(prior_notes or []),
        grounded=store.summary().get("grounded_nodes", 0) > 0,
        repos=repos,
        elided=elided,
    )


def render_investigation_md(inv: Investigation) -> str:
    """Render the brief as markdown. Honest when a section has nothing grounded."""
    out: list[str] = [f"# Investigation — {inv.title or 'ticket'}\n"]
    if inv.problem:
        out.append(f"## Problem\n{inv.problem}\n")

    out.append("## Where it lands in the code")
    if inv.landing:
        out.append("_Lexically-retrieved from the knowledge graph — start here, confirm before trusting._\n")
        for hit in inv.landing:
            loc = f" — {hit.location}" if hit.where else ""
            in_mod = f" _(in {hit.module})_" if hit.module and hit.module != hit.name else ""
            # The repo goes first, before the symbol: in a merged graph it is the field that
            # decides which checkout a reader opens, and burying it after the line number
            # makes two identically-named landings look like one.
            prefix = f"**{hit.repo}** · " if hit.repo else ""
            # Stated separately rather than folded into the caller count: they are different
            # facts, and an HTTP handler with 0 callers and 3 dependents in another service is
            # exactly the row a reader must not skim past.
            reach = f", **{hit.cross_repo} dependent(s) in other repos**" if hit.cross_repo else ""
            # Bounded at three: a symbol edited across a dozen tickets says "this is hot", which
            # the count conveys, and listing all twelve would bury the landing site itself.
            served = ""
            if hit.intents:
                shown = ", ".join(hit.intents[:3])
                more = f" +{len(hit.intents) - 3} more" if len(hit.intents) > 3 else ""
                served = f" — last changed for {shown}{more}"
            out.append(
                f"- {prefix}`{hit.name}` ({hit.kind}, {hit.callers} caller(s){reach}){in_mod}{loc}{served}"
            )
        if any(hit.intents for hit in inv.landing):
            # Stated once, at report level, and only when the tier actually ran. Per symbol it
            # would be noise on every line; omitted entirely, a reader would take the symbols
            # with no ticket for symbols with no prior work — and on this repository that is
            # nine landings in ten. The rate is the difference between a finding and a claim.
            attributed = sum(1 for hit in inv.landing if hit.intents)
            out.append(
                f"\n_Recorded intent covers {attributed} of {len(inv.landing)} landing(s). "
                "A landing with no ticket was not attributed — which is not the same as "
                "having no prior work; see the coverage rate the scan reports._"
            )
        if len(inv.repos) > 1:
            out.append(f"\n_This ticket lands in {len(inv.repos)} repositories: {', '.join(inv.repos)}._")
        if inv.elided:
            # "Top N of M", never a clipped list implying completeness.
            out.append(f"\n_Showing the top {len(inv.landing)}; {inv.elided} further match(es) not listed._")
        if inv.areas:
            out.append(f"\n_Likely areas: {', '.join(inv.areas)}_")
    elif not inv.grounded:
        out.append("_No knowledge graph yet (greenfield/empty repo) — nothing to ground against._")
    else:
        out.append(
            "_No symbols matched the ticket's terms — it may name new behavior, "
            "or use words the code doesn't._"
        )
    out.append("")

    out.append("## Relevant project knowledge")
    out.append(
        inv.knowledge
        if inv.knowledge
        else "_No committed `episteme/` found — run `orchestrator understand .` to build one._"
    )
    out.append("")

    out.append("## Prior art / related work")
    if inv.prior_notes:
        out.append("_From cross-run memory (past runs on this repo):_\n")
        out.extend(f"- {note}" for note in inv.prior_notes)
    else:
        out.append("_None surfaced (cross-run memory needs the registry DB; the CLI runs without it)._")
    out.append("")

    out.append("## Suggested next step")
    out.append("Feed this into `orchestrator design` to produce a grounded, blast-radius-aware design.")
    return "\n".join(out) + "\n"


__all__ = ["Investigation", "Landing", "build_investigation", "render_investigation_md"]
