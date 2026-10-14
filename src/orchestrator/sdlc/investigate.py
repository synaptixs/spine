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
from orchestrator.pkg.retrieval import GroundedRetriever, ScoredSymbol
from orchestrator.sdlc import brief
from orchestrator.sdlc.brief import Brief, Tier
from orchestrator.sdlc.landings import Landing, render_landings


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
    #: Symbols that matched but were not listed — cut by ``max_symbols``, or by the display rule's
    #: per-file and eleventh-file limits. Bounded honestly: a truncated list must read as "top N of
    #: M", never as the complete answer (`CLAUDE.md` invariant 7).
    elided: int = 0
    #: ``elided`` is a lower bound: the retriever was stopped at the walk bound, so more than
    #: ``elided`` matches were not listed. Only the display rule can set it.
    elided_floor: bool = False
    #: The list is up to ten files with a few symbols each, not the top *N* symbols: a higher-ranked
    #: symbol may be missing while a lower-ranked one in another file is shown, so "the top N" would
    #: overstate it. False for an explicit ``max_symbols``, which keeps the old wording.
    by_file: bool = False
    knowledge: str = ""  # episteme excerpt, or ""
    prior_notes: list[str] = field(default_factory=list)  # cross-run recall, best-effort
    grounded: bool = False  # the PKG had grounded nodes
    #: What `--follow-links` read and could not, as `FollowReport.summary` says it; empty when
    #: links were not followed. Stated under the problem, which is where those pages now are.
    linked_pages: str = ""


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


#: What a brief shows by default: up to ten distinct *files*, a few symbols each. Ten symbols often
#: name six or seven files, so a symbol cap answered "where does this land" with fewer places than it
#: claimed — measured on 38 labelled issues, the same ranking puts the fixing file in the first ten
#: symbols for 23 and in the first ten files for 27, with no change to the ranking at all (SSPN-176).
#: Three symbols a file keeps a file from filling the brief; thirty bounds its size; two hundred bounds
#: the walk. Fixed before their effect was computed (pre-registration C8), not tuned.
_MAX_FILES = 10
_PER_FILE = 3
_MAX_LISTED = 30
_WALK_BOUND = 200


def _display_slice(
    hits: list[ScoredSymbol], *, max_files: int, per_file: int, max_listed: int
) -> list[ScoredSymbol]:
    """The ranked symbols to show: ranking order kept, at most ``per_file`` of each file.

    The walk ends at the first symbol that would start file number ``max_files + 1``. A listed
    file's later-ranked symbols that come *before* that point are still shown; the listed files and
    their order are the same either way, which is what a reader and the scorer see.
    """
    shown: list[ScoredSymbol] = []
    counts: dict[tuple[str, str], int] = {}
    for hit in hits:
        prov = hit.node.provenance
        key = (prov.repo, prov.file) if prov is not None else ("", hit.node.id)
        if key not in counts:
            if len(counts) >= max_files:
                break
            counts[key] = 0
        if counts[key] >= per_file:
            continue
        shown.append(hit)
        counts[key] += 1
        if len(shown) >= max_listed:
            break
    return shown


def build_investigation(
    title: str,
    problem: str,
    *,
    store: FactStore,
    root: Path | str | None = None,
    repo_roots: Mapping[str, Path] | None = None,
    prior_notes: list[str] | None = None,
    max_symbols: int | None = None,
) -> Investigation:
    """Research ``title``/``problem`` against the PKG + episteme. Deterministic.

    ``root`` is the single repository whose ``episteme/`` to read. ``repo_roots`` is the
    multi-repo form — every declared repository by key — from which only the repos this
    ticket actually lands in are read (D8). Pass one or the other, not both.

    ``max_symbols=None`` (the default) lists up to ten distinct files, at most three symbols each
    (:func:`_display_slice`). An explicit integer keeps the older meaning — the top *N* symbols —
    for a caller that sized its own list, as ``design`` does.
    """
    from orchestrator.pkg.scoping import unscope_id

    retriever = GroundedRetriever(store)
    if max_symbols is None:
        # One past the walk bound, so `elided` can say "there were more" even at the bound.
        fetched = retriever.scored_symbols(f"{title}\n{problem}", limit=_WALK_BOUND + 1)
        hits = _display_slice(
            fetched[:_WALK_BOUND], max_files=_MAX_FILES, per_file=_PER_FILE, max_listed=_MAX_LISTED
        )
        elided = len(fetched) - len(hits)
        elided_floor = len(fetched) > _WALK_BOUND
    else:
        elided_floor = False
        # One extra, so `elided` can distinguish "these are all of them" from "this is the top N".
        fetched = retriever.scored_symbols(f"{title}\n{problem}", limit=max_symbols + 1)
        elided = max(0, len(fetched) - max_symbols)
        hits = fetched[:max_symbols]
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
        elided_floor=elided_floor,
        by_file=max_symbols is None,
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
        floor = "At least " if inv.elided_floor else ""
        notes.append(
            f"- {floor}{inv.elided} further match(es) were not listed — the ranking is a bound, "
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
        linked = f"\n\n**Linked pages:** {inv.linked_pages}" if inv.linked_pages else ""
        doc.add(brief.PROBLEM, inv.problem + linked)

    out: list[str] = []
    excerpts = _excerpts_for(inv)
    if inv.landing:
        out.append("_Lexically-retrieved from the knowledge graph — start here, confirm before trusting._\n")
        # The bullet itself lives in `landings`, shared with `evidence` (whose tool output the
        # codegen agent reads) and with the grounded OpenSpec draft. Everything below stays
        # here: the footers are this brief's honesty to state, not the renderer's.
        out.extend(render_landings(inv.landing, excerpts=excerpts))
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
            more = f"{'at least ' if inv.elided_floor else ''}{inv.elided} further match(es) not listed"
            if inv.by_file:
                # Not "the top N": a per-file cap can leave a higher-ranked symbol out of a shown file.
                out.append(
                    f"\n_Showing {len(inv.landing)} landing(s) across up to ten files, at most three "
                    f"symbols each; {more}._"
                )
            else:
                out.append(f"\n_Showing the top {len(inv.landing)}; {more}._")
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
