"""What the code says about a drafted change — and, when it says nothing, why.

A drafted OpenSpec change mixes a model's prose with the graph's facts on one page, and a
reader cannot tell them apart by looking. If a ``file:line`` makes an unreliable requirement
*look* verified, grounding has made the draft worse than the blind one it replaces. So this
module's job is as much about **absence** as evidence: every way the graph can fail to answer
gets its own words, because a reader who cannot distinguish them will assume the flattering
one.

There are four states, and three of them are kinds of silence:

``ungrounded``
    No repository was given. The blind draft is legitimate — bootstrapping a greenfield repo
    has no graph — but a reader must not have to guess that is what happened.
``empty``
    A repository *was* read and the graph came back with nothing. This is not rare: Spine's
    front-ends do not cover every language, and a repo it cannot parse yields zero nodes while
    looking exactly like a repo with nothing to find. Rendered as "we looked and found
    nothing", never as "this ticket touches nothing".
``untrusted``
    The tree has uncommitted work, so every citation is real right now and unreproducible at a
    commit. The CLI already warns about this on stderr — which scrolls away, while a drafted
    change file is committed and read months later. So it is recorded *in the file*.
``grounded``
    The graph answered. Only here may a citation appear.

The distinction ``empty`` draws against ``ungrounded`` is the one worth the code: treating a
zero-node graph as "no repository given" would claim nothing was consulted when something was.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.criteria_binding import CriteriaBinding

__all__ = [
    "DraftGrounding",
    "GroundingState",
    "LandingGroup",
    "absence_section",
    "banner_sentence",
    "fact_section",
    "from_store",
    "ungrounded",
    "with_facts",
]


@dataclass(frozen=True)
class LandingGroup:
    """Landing bullets for one repository, already rendered by `sdlc.landings`.

    Pre-rendered on purpose. The bullets come from the renderer `investigate` and the codegen
    agent share, and calling it from here would make `intake` import `sdlc` — which the CLI
    exists to prevent by composing both. So this package receives strings it does not format.
    """

    #: Repository key. Empty for the single-repo case, where there is nothing to disambiguate.
    repo: str
    bullets: tuple[str, ...]
    #: A repository the change lands in that had nothing to show. **Named, not skipped** (D14):
    #: silence would read as "this repository has nothing to say", which is a different claim.
    absent: bool = False


GroundingState = Literal["ungrounded", "empty", "untrusted", "grounded"]


@dataclass(frozen=True)
class DraftGrounding:
    """The standing of the code evidence behind one drafted change."""

    state: GroundingState
    #: The path or ``.spine/repos.yaml`` that was read. Empty only when ``ungrounded``.
    where: str = ""
    nodes: int = 0
    grounded_nodes: int = 0
    #: Languages the extractor actually emitted nodes for. Empty when the graph is empty —
    #: which is the finding, not a missing field.
    languages: tuple[str, ...] = ()
    #: Repository keys with uncommitted work, when the graph is merged.
    untrusted: tuple[str, ...] = ()
    #: Per-spec facts, attached by `with_facts` after the per-spec retrieval (D21). Empty on
    #: the base grounding, which describes the *repository* rather than any one change.
    landings: tuple[LandingGroup, ...] = ()
    #: Matches cut by the retrieval bound. Stated as "top N of M", never a clipped list
    #: implying completeness.
    elided: int = 0
    areas: tuple[str, ...] = ()
    binding: CriteriaBinding | None = None
    #: Was the working tree consulted for file mentions, or only the graph? False for a merged
    #: graph of several repositories, where `bind_criteria`'s single ``root`` cannot name one
    #: of them. Carried so the page can say it: reporting a criterion as unfindable when
    #: nothing looked on disk is a claim of absence the evidence does not support.
    tree_checked: bool = True

    @property
    def cites(self) -> bool:
        """May this draft carry a ``file:line`` at all?

        ``untrusted`` still cites: the evidence is true of the tree on disk, and suppressing it
        would lose a real finding to protect a reproducibility property the page now states
        for itself.
        """
        return self.state in ("grounded", "untrusted")


def ungrounded() -> DraftGrounding:
    """No repository was given — the blind draft, said out loud."""
    return DraftGrounding(state="ungrounded")


def from_store(store: FactStore, *, where: str, untrusted: tuple[str, ...] = ()) -> DraftGrounding:
    """Classify what a store came back with. The only place a state is decided."""
    summary = store.summary()
    grounded_nodes = int(summary.get("grounded_nodes", 0))
    languages = tuple(sorted({n.language for n in store.nodes if n.language and n.grounded}))
    if grounded_nodes == 0:
        # Checked before trust: a graph with nothing in it has nothing to be untrusted about,
        # and reporting "uncommitted work" over "we read nothing" buries the actionable half.
        return DraftGrounding(
            state="empty",
            where=where,
            nodes=int(summary.get("nodes", 0)),
            grounded_nodes=0,
            untrusted=untrusted,
        )
    return DraftGrounding(
        state="untrusted" if untrusted else "grounded",
        where=where,
        nodes=int(summary.get("nodes", 0)),
        grounded_nodes=grounded_nodes,
        languages=languages,
        untrusted=untrusted,
    )


def with_facts(
    base: DraftGrounding,
    *,
    landings: tuple[LandingGroup, ...] = (),
    elided: int = 0,
    areas: tuple[str, ...] = (),
    binding: CriteriaBinding | None = None,
    tree_checked: bool = True,
) -> DraftGrounding:
    """Attach one change's facts to the repository-level grounding.

    Two steps because they have different scopes: the repository is read once (extraction is
    the expensive half, D9), while retrieval and binding are per change — a source drafts N of
    them, and one shared block would cite identical sites in all N the moment the specs diverge
    (D21).
    """
    return replace(
        base,
        landings=landings,
        elided=elided,
        areas=areas,
        binding=binding,
        tree_checked=tree_checked,
    )


def banner_sentence(g: DraftGrounding) -> str:
    """One line for the draft banner — what a reader who skims `proposal.md` must still see."""
    if g.state == "ungrounded":
        return "**Not grounded:** no repository was read, so nothing here is checked against code."
    if g.state == "empty":
        return f"**Grounded against `{g.where}`, which yielded no graph** — see *Grounding* below."
    if g.state == "untrusted":
        return f"**Grounded against `{g.where}`, uncommitted** — citations cannot be re-derived at a commit."
    return f"**Grounded against `{g.where}`** — cited lines are facts from the graph; the prose above is not."


def absence_section(g: DraftGrounding) -> str:
    """The *Grounding* section body: what was read, and what that does and does not prove."""
    if g.state == "ungrounded":
        return (
            "No repository was read, so every requirement above is derived from the source "
            "document alone — unchecked against any code. That is a legitimate mode "
            "(a greenfield repo has no graph to check against), and it is stated here so a "
            "reader never has to infer it.\n\n"
            "Pass a repository path, or `--repos <.spine/repos.yaml>`, to ground the draft."
        )
    if g.state == "empty":
        walked = (
            f"{g.nodes} node(s) were read but none were grounded in this repository's own source"
            if g.nodes
            else "the graph came back empty"
        )
        return (
            f"`{g.where}` **was** read and {walked}. This says nothing about the ticket: a "
            "language Spine has no front-end for yields zero nodes and looks exactly like a "
            "repository with nothing to find.\n\n"
            "Read this as *“we looked and found nothing”*, never as *“this ticket "
            "touches nothing”*. Run `orchestrator pkg extract <path>` to see what the "
            "extractor emits for this repository."
        )
    langs = ", ".join(f"`{lang}`" for lang in g.languages) or "no language"
    read = f"`{g.where}` — {g.grounded_nodes} grounded node(s) across {langs}."
    if g.state == "untrusted":
        keys = ", ".join(f"`{k}`" for k in g.untrusted)
        return (
            f"{read}\n\n**NOT REPRODUCIBLE — {keys} has uncommitted work or is not a git "
            "repository.** The citations below are true of the tree that was on disk when this "
            "was drafted, and cannot be re-derived at a commit. Re-draft from a clean checkout "
            "before treating any line number as durable."
        )
    return (
        f"{read}\n\nCited lines are facts, re-derivable from the graph at this commit. "
        "The requirements and scenarios above are the model's prose and carry no citation — "
        "if a line has no `file:line`, nothing has checked it."
    )


def _landings_md(g: DraftGrounding) -> list[str]:
    """Where this change lands, grouped by repository (D14)."""
    if not g.landings:
        return [
            "### Where it lands",
            "",
            (
                "_No symbol matched this change's terms. Retrieval is **lexical** — a landing "
                "site that uses different words for the same thing is not here._"
            ),
        ]
    out = ["### Where it lands", ""]
    single = len(g.landings) == 1 and not g.landings[0].repo
    for group in g.landings:
        # An unscoped hit can reach a merged graph (an id with no repo prefix), and heading it
        # with an empty code span would name a repository that does not exist. No heading is
        # the honest rendering: the bullets still carry their own `repo:file:line`.
        if not single and group.repo:
            out.append(f"#### `{group.repo}`")
            out.append("")
        if group.absent:
            # Named rather than skipped: this change reaches this repository, and "nothing
            # matched here" is a finding a reader should see, not an omission to infer.
            out.append("_This change lands in this repository, but no symbol matched its terms._")
        else:
            out.extend(group.bullets)
        out.append("")
    if g.elided:
        # **Not a count.** `build_investigation` retrieves `max_symbols + 1` precisely so
        # `elided` can distinguish "these are all of them" from "this is the top N" — so it
        # saturates at 1, and printing it as a number tells a reader that exactly one match
        # was cut when it may have been three hundred. In a brief that scrolls past, the
        # overstatement is cheap; in a committed change file someone reads next quarter, it
        # reads as "the list is essentially complete". `investigate` still words this as a
        # count — inherited, and its own to fix, since changing it moves bytes a test pins.
        # Bullets, not entries. `render_landings` appends an excerpt as its **own** list
        # entry, so `len(bullets)` counts rendered lines and would inflate the figure the
        # moment a caller asks for source. No caller does yet — which is exactly when this is
        # cheap to get right.
        shown = sum(1 for x in g.landings if not x.absent for b in x.bullets if b.startswith("- "))
        out.append(f"_Showing the top {shown} — **further matches were cut** and are not listed._")
    if g.areas:
        out.append(f"_Likely areas: {', '.join(g.areas)}_")
    return out


def _criteria_md(g: DraftGrounding) -> list[str]:
    """Each stated criterion against the graph — and what that does *not* establish.

    Only ``acceptance_criteria`` reach the binder (`criteria_binding._criteria_text`), so a
    criterion the model invented can never acquire a citation here. That is not a filter this
    module applies; it is one the binder already refuses to lift.

    **There is no separate "already met" section, deliberately.** The binder cannot judge
    whether code *satisfies* a criterion — `specs.py` is explicit that no deterministic pass
    can — so the candidate set is exactly the bound set seen as a question. Two headings over
    one set of rows would read as two findings.
    """
    binding = g.binding
    if binding is None or not binding.rows:
        return []
    out = ["### Criteria against the code", ""]
    if binding.bound:
        out.append(
            "**These name code that already exists.** That is evidence, not a verdict: "
            "confirm whether the behaviour is already satisfied before building it. SSPN-49 "
            "filed six criteria of which two described behaviour that already existed, and a "
            "run would have reported them met having changed nothing."
        )
        out.append("")
        for row in binding.bound:
            out.append(f"- {row.text}")
            for anchor in row.anchors:
                seen = " · in the landing files" if anchor.in_evidence else ""
                out.append(f"  - `{anchor.symbol}` — `{anchor.where}`{seen}")
        out.append("")
    if binding.unbound:
        out.append(
            "**These name code the graph cannot find.** Either the work is new, or the "
            "criterion names something by a word the code does not use — the two look "
            "identical from here, and only a human can tell them apart."
        )
        out.append("")
        for row in binding.unbound:
            claims = ", ".join(f"`{c}`" for c in row.claims)
            out.append(f"- {row.text}" + (f" — unresolved: {claims}" if claims else ""))
        out.append("")
    if not g.tree_checked and (binding.unbound or binding.no_claim):
        # Said once, at section level. Per row it would be noise; omitted entirely, an unbound
        # criterion naming a config file would read as "the graph cannot find this" when the
        # truth is "nobody looked on disk, because several repositories were merged and there
        # is no single tree to look in".
        out.append(
            "_File mentions were checked against the graph only. With several repositories "
            "merged there is no single working tree to search, so a criterion naming a file "
            "the extractor never parsed — a config, a markdown page — could not be resolved "
            "here even if it exists._"
        )
        out.append("")
    if binding.no_claim:
        out.append(
            f"_{len(binding.no_claim)} further criterion(s) make no claim about existing code, "
            "so there was nothing to bind. That is not a failure to bind._"
        )
        out.append("")
    return out


def fact_section(g: DraftGrounding) -> str:
    """The fenced fact region: everything here is re-derivable from the graph.

    Returns ``""`` for a state that may not cite, which is what keeps the rule mechanical —
    the honest-absence text lives in `absence_section` and a page never carries both a "we
    could not read this" notice and a citation.
    """
    if not g.cites:
        return ""
    parts = _landings_md(g)
    criteria = _criteria_md(g)
    if criteria:
        parts += ["", *criteria]
    return "\n".join(parts).rstrip() + "\n"
