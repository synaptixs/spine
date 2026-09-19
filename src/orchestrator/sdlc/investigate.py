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

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import NodeKind
from orchestrator.sdlc import brief
from orchestrator.sdlc.brief import Brief, Tier


@dataclass(frozen=True)
class Landing:
    """One place in the code a ticket lexically lands."""

    name: str
    where: str  # "file:line"
    kind: str  # Function | Type | Module | …
    callers: int
    module: str  # owning module (touch-risk context)
    #: Does any test transitively reach this symbol? **`None` means "cannot tell"** — the
    #: language has no call graph — and is rendered as silence, never as "untested". A front
    #: end that emits no `CALLS` edges has not proven an absence of tests.
    covered: bool | None = None
    #: The graph node this landing *is*. Kept because the brief has it in hand while building
    #: the row and used to throw it away — and re-deriving it later from ``name`` can return a
    #: different node, so the excerpt and the coverage line would describe a symbol the reader
    #: is not looking at. Empty only for a `Landing` constructed outside the retriever.
    node_id: str = ""
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
    #: The retrieval score and the query tokens the name shared, and whether that evidence is
    #: **weak** — resting only on words that name other files too. Carried because the score used
    #: to be discarded here, which left every consumer unable to tell "matched on `oauth2`"
    #: from "matched on `client`": NSS-1231's five wrong files were all the second kind, and
    #: the design promoted them to "Files to touch" with no way to know.
    score: float = 0.0
    matched: tuple[str, ...] = ()
    weak: bool = False

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
    #: Repo key → checkout root, for reading the files the landings point at. Empty key is
    #: the single-repo case. Render-time only; never part of what the brief asserts.
    roots: dict[str, Path] = field(default_factory=dict)
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


#: The single-repo grounding budget, split across the repositories a merged brief reads —
#: never multiplied by them. A section that grows with the repository count crowds out the
#: landing sites, which are the thing the reader came for.
_MERGED_KNOWLEDGE_BUDGET = 2500


def _merged_knowledge(repo_roots: Mapping[str, Path], repos: list[str]) -> str:
    """Each landed-in repository's own ``episteme/``, headed by its key (D8).

    **Only the repositories this ticket lands in.** A merged graph may declare four
    repositories; a brief that landed in one should not carry another service's domain model,
    and the brief already knows which repos its landing sites are in.

    **A declared repository whose bank is missing is named, not skipped.** Silence would read
    as "that repository has nothing to say", when what it means is "nobody ran `understand`
    there" — the same distinction the brief keeps everywhere else.
    """
    from orchestrator.knowledge.access import memory_bank_grounding

    landed = [key for key in repos if key in repo_roots]
    if not landed:
        return ""

    per_repo = max(_MERGED_KNOWLEDGE_BUDGET // len(landed), 400)
    blocks: list[str] = []
    absent: list[str] = []
    for key in landed:
        body = memory_bank_grounding(repo_roots[key], budget=per_repo)
        if body:
            blocks.append(f"### `{key}`\n\n{body}")
        else:
            absent.append(key)

    if absent:
        names = ", ".join(f"`{k}`" for k in absent)
        blocks.append(
            f"_No committed `episteme/` in {names} — run `orchestrator understand .` there. "
            "Absent, not empty._"
        )
    return "\n\n".join(blocks)


def build_investigation(
    title: str,
    problem: str,
    *,
    store: FactStore,
    root: Path | str | None = None,
    repo_roots: Mapping[str, Path] | None = None,
    prior_notes: list[str] | None = None,
    max_symbols: int = 10,
) -> Investigation:
    """Research ``title``/``problem`` against the PKG + episteme. Deterministic.

    ``root`` is the single repository whose ``episteme/`` to read. ``repo_roots`` is the
    multi-repo form — every declared repository by key — from which only the repos this
    ticket actually lands in are read (D8). Pass one or the other, not both.
    """
    from orchestrator.pkg.retrieval import GroundedRetriever
    from orchestrator.pkg.scoping import unscope_id

    retriever = GroundedRetriever(store)
    # One extra, so `elided` can distinguish "these are all of them" from "this is the top N".
    hits = retriever.scored_symbols(f"{title}\n{problem}", limit=max_symbols + 1)
    elided = max(0, len(hits) - max_symbols)
    hits = hits[:max_symbols]
    parents = store.parents_index()

    # Built once for the whole brief, not per landing: `build_regression_plan` rebuilds a
    # predecessor index over every edge each time it is called, which its own docstring calls
    # "far too slow" for exactly this — one lookup per landing site.
    from orchestrator.sdlc.coverage import CoverageIndex

    coverage = CoverageIndex(store)

    landing: list[Landing] = []
    areas: list[str] = []
    repos: list[str] = []
    for hit in hits:
        n = hit.node
        module = _owning_module(store, n.id, parents)
        repo, _ = unscope_id(n.id)
        landing.append(
            Landing(
                name=n.name,
                node_id=n.id,
                covered=coverage.is_covered(n.id) if coverage.call_graph_available else None,
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
                score=round(hit.score, 2),
                matched=hit.matched,
                weak=hit.weak,
            )
        )
        # Areas are qualified by repo, or two services that both have `app.models` collapse
        # into one area and the brief claims a change is narrower than it is.
        area = f"{repo}:{module}" if repo and module else module
        if area and area not in areas:
            areas.append(area)
        if repo and repo not in repos:
            repos.append(repo)

    # Kept so the renderer can read the files these landings point at. Not a `Path` on the
    # Landing itself: a landing is a fact about the graph, and where that repository sits on
    # this machine is not.
    roots: dict[str, Path] = {}
    if repo_roots:
        roots = {k: Path(v) for k, v in repo_roots.items()}
    elif root is not None:
        roots = {"": Path(root)}

    knowledge = ""
    if root is not None:
        from orchestrator.knowledge.access import memory_bank_grounding

        knowledge = memory_bank_grounding(root)
    elif repo_roots:
        knowledge = _merged_knowledge(repo_roots, repos)

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
        roots=roots,
    )


def _not_verified(inv: Investigation) -> str:
    """What this brief did *not* establish, derived from its own state.

    The section is required, so the alternative to writing this is the placeholder — and
    "nothing was deliberately left unchecked" is false of every investigation: retrieval here
    is lexical, the list is bounded, and three of the four sections degrade silently when
    their source is absent. A limits section that is wrong is worse than none, because a
    reader who sees one stops looking for the limits themselves.

    Every line is conditional on real state. A brief with genuinely nothing to declare falls
    back to the section's own copy rather than inventing a caveat.
    """
    notes: list[str] = []
    if inv.landing:
        notes.append(
            "- Retrieval is **lexical**, not semantic: these symbols matched the ticket's "
            "words. A landing site that uses different words for the same thing is not here."
        )
    if inv.elided:
        notes.append(
            f"- {inv.elided} further match(es) were not listed — the ranking is a bound, "
            "not a judgement that the rest are irrelevant."
        )
    if not inv.knowledge:
        notes.append("- No `episteme/` was read, so committed project knowledge is absent, not empty.")
    if not inv.prior_notes:
        notes.append(
            "- Prior runs were not consulted (cross-run memory needs the registry DB) — "
            "this ticket may have been attempted before."
        )
    if len(inv.repos) > 1:
        notes.append(
            f"- {len(inv.repos)} repositories were merged. Anything in a repository that was "
            "not declared is invisible here, and reads the same as nothing to find."
        )
    if inv.landing and not any(hit.intents for hit in inv.landing):
        notes.append(
            "- No landing site carries a recorded intent, which is not the same as no prior "
            "work having touched it."
        )
    return "\n".join(notes)


#: Landing sites whose source is quoted. Three, not all of them: a brief is read at a gate,
#: and evidence that runs past the reader's attention has failed differently, not less. The
#: rest keep their bullet, and the count of what was not quoted is stated (invariant 7).
_MAX_EXCERPTS = 3


def _excerpts_for(inv: Investigation) -> dict[int, str]:
    """Fenced source for the first few landings, by index. Silently skips what it cannot read.

    The brief already computed the exact `file:line` for every row and then declined to open
    it — which is the whole defect this closes: a document *about* code, containing none.
    """
    from orchestrator.sdlc.excerpt import source_at

    out: dict[int, str] = {}
    for i, hit in enumerate(inv.landing):
        if len(out) >= _MAX_EXCERPTS:
            break
        # Never spend the budget on a row the brief itself doubts. A weak landing matched on
        # a fragment other files share — quoting twelve lines of it costs the reader exactly
        # the attention that should have gone to a strong one. When *every* landing is weak
        # the brief already says so in words, and no excerpt is the honest answer.
        if hit.weak:
            continue
        root = inv.roots.get(hit.repo) or inv.roots.get("")
        excerpt = source_at(root, hit.where)
        if excerpt is not None:
            out[i] = excerpt.fenced()
    return out


def render_investigation_md(inv: Investigation) -> str:
    """Render the brief as markdown. Honest when a section has nothing grounded.

    Section titles and their order come from :mod:`orchestrator.sdlc.brief`, not from this
    function — five modules spelled these by hand and had drifted to three spellings of
    "next step" alone. The tier is EVIDENCE, so this renderer *cannot* emit a verdict or a
    recommendation: an investigation must stay re-derivable from the graph, and the argument
    on top of it belongs to `design`, which has an author behind it.
    """
    doc = Brief(f"Investigation — {inv.title or 'ticket'}", tier=Tier.EVIDENCE)
    if inv.problem:
        doc.add(brief.PROBLEM, inv.problem)

    out: list[str] = []
    excerpts = _excerpts_for(inv)
    if inv.landing:
        out.append("_Lexically-retrieved from the knowledge graph — start here, confirm before trusting._\n")
        for i, hit in enumerate(inv.landing):
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
            # A weak hit says what it rests on. "Confirm before trusting" above is advice; this
            # is the evidence a reader needs to act on it — and the design drops weak hits
            # rather than promoting them to files to touch.
            shared = ", ".join(f"`{t}`" for t in hit.matched)
            basis = f" — weak: only {shared}, which other files use too" if hit.weak and hit.matched else ""
            # Stated only when the graph can answer it *and* the row is worth the reader's
            # attention. "No test reaches this" is a finding on a strong landing and noise on
            # a weak one — on a real ticket it fired in bold on all ten rows, including DTO
            # fields nobody would test, which is a signal that has stopped being one.
            tested = ""
            if not hit.weak:
                if hit.covered is True:
                    tested = " · reached by tests"
                elif hit.covered is False:
                    tested = " · **no test reaches this**"
            head = f"- {prefix}`{hit.name}` ({hit.kind}, {hit.callers} caller(s){reach}{tested})"
            out.append(f"{head}{in_mod}{loc}{served}{basis}")
            if (excerpt := excerpts.get(i)) is not None:
                # Indented so it reads as part of the bullet, not as a sibling of it.
                out.append("\n" + "\n".join("  " + ln for ln in excerpt.splitlines()) + "\n")
        if inv.landing and all(hit.weak for hit in inv.landing):
            out.append(
                "\n_Every match rests only on words other files use too. That is not a landing site — this "
                "ticket may name new behaviour, or use words the code doesn't. The design proposes "
                "no files from it; name the file, class or endpoint involved._"
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
        if excerpts and len(inv.landing) > len(excerpts):
            out.append(
                f"\n_Source shown for {len(excerpts)} of {len(inv.landing)} landing(s) — "
                "the rest carry their location only._"
            )
        if inv.areas:
            out.append(f"\n_Likely areas: {', '.join(inv.areas)}_")
    elif not inv.grounded:
        out.append("_No knowledge graph yet (greenfield/empty repo) — nothing to ground against._")
    else:
        out.append(
            "_No symbols matched the ticket's terms — it may name new behavior, "
            "or use words the code doesn't._"
        )
    doc.add(brief.LANDS, "\n".join(out))
    doc.add(brief.KNOWLEDGE, inv.knowledge)

    if inv.prior_notes:
        notes = ["_From cross-run memory (past runs on this repo):_\n"]
        notes.extend(f"- {note}" for note in inv.prior_notes)
        doc.add(brief.PRIOR_ART, "\n".join(notes))
    else:
        doc.add(brief.PRIOR_ART)

    doc.add(brief.NOT_VERIFIED, _not_verified(inv))
    doc.add(
        brief.NEXT_STEP,
        "Feed this into `orchestrator design` to produce a grounded, blast-radius-aware design.",
    )
    return doc.render()


__all__ = ["Investigation", "Landing", "build_investigation", "render_investigation_md"]
