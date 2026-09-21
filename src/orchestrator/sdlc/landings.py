"""One rendering of a landing site, for every surface that shows one.

**Why this module exists.** The landing block had drifted into two spellings before it had
three consumers: ``investigate`` renders it for a human brief, ``evidence`` renders it for the
codegen agent's tool loop — with a narrower row, no repo prefix and no excerpts — and
``openspec draft`` was about to write a third. Invariant 1 says surfaces *render* facts rather
than re-deriving them, and three renderers of one fact set is how they stop agreeing: the two
that already existed disagreed about whether a location is code-spanned.

The differences that are real are **parameters**, not forks. There is exactly one:

``location_in_code``
    ``evidence`` wraps the location in backticks; ``investigate`` does not. Both spellings
    predate this module and both are load-bearing — ``evidence``'s output is read by the
    codegen agent, so changing it would alter what a model sees. That may well be an
    improvement; it is a separate change with its own evaluation, and not something to smuggle
    into a refactor.

Everything else that looks like a difference is **absence of data**, and falls out of
:class:`Landing`'s defaults: a row with no ``repo`` renders no repo prefix, ``covered=None``
says nothing about tests, and no ``intents`` claims no prior work. That is why a caller with
narrower facts gets a narrower bullet without asking for one — and why ``evidence`` can pass
through this function and get its own bytes back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["Landing", "render_landing", "render_landings"]

#: `Landing` lives beside its renderer, not in `investigate`, so this module imports nothing
#: back. It was the other way around for one commit, with the type pulled in under
#: `TYPE_CHECKING` — free at runtime, and CodeQL's `py/cyclic-import` flagged it as an error
#: anyway, because the rule reads the import graph rather than the runtime one. A guard that
#: fires on a safe construct still costs every future reader the same investigation, and
#: `investigate` re-exports the name, so nothing importing it had to change.


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


def render_landing(hit: Landing, *, location_in_code: bool = False) -> str:
    """One landing site as a markdown bullet. The excerpt, if any, is the caller's to place."""
    loc = ""
    if hit.where:
        loc = f" — `{hit.location}`" if location_in_code else f" — {hit.location}"
    in_mod = f" _(in {hit.module})_" if hit.module and hit.module != hit.name else ""
    # The repo goes first, before the symbol: in a merged graph it is the field that decides
    # which checkout a reader opens, and burying it after the line number makes two
    # identically-named landings look like one.
    prefix = f"**{hit.repo}** · " if hit.repo else ""
    # Stated separately rather than folded into the caller count: they are different facts,
    # and an HTTP handler with 0 callers and 3 dependents in another service is exactly the
    # row a reader must not skim past.
    reach = f", **{hit.cross_repo} dependent(s) in other repos**" if hit.cross_repo else ""
    # Bounded at three: a symbol edited across a dozen tickets says "this is hot", which the
    # count conveys, and listing all twelve would bury the landing site itself.
    served = ""
    if hit.intents:
        shown = ", ".join(hit.intents[:3])
        more = f" +{len(hit.intents) - 3} more" if len(hit.intents) > 3 else ""
        served = f" — last changed for {shown}{more}"
    # A weak hit says what it rests on. "Confirm before trusting" is advice; this is the
    # evidence a reader needs to act on it — and the design drops weak hits rather than
    # promoting them to files to touch.
    shared = ", ".join(f"`{t}`" for t in hit.matched)
    basis = f" — weak: only {shared}, which other files use too" if hit.weak and hit.matched else ""
    # Stated only when the graph can answer it *and* the row is worth the reader's attention.
    # "No test reaches this" is a finding on a strong landing and noise on a weak one — on a
    # real ticket it fired in bold on all ten rows, including DTO fields nobody would test,
    # which is a signal that has stopped being one.
    tested = ""
    if not hit.weak:
        if hit.covered is True:
            tested = " · reached by tests"
        elif hit.covered is False:
            tested = " · **no test reaches this**"
    head = f"- {prefix}`{hit.name}` ({hit.kind}, {hit.callers} caller(s){reach}{tested})"
    return f"{head}{in_mod}{loc}{served}{basis}"


def render_landings(
    landings: Sequence[Landing],
    *,
    excerpts: Mapping[int, str] | None = None,
    location_in_code: bool = False,
) -> list[str]:
    """Bullets for a run of landing sites, in order, as lines to append.

    Returned as lines rather than one joined string because each caller owns what follows
    them: the elision footers, the areas line and the "top N of M" notice are the caller's
    honesty to state, not this function's. An excerpt is appended as its own entry, indented
    so it reads as part of the bullet rather than a sibling of it.
    """
    by_index = excerpts or {}
    out: list[str] = []
    for i, hit in enumerate(landings):
        out.append(render_landing(hit, location_in_code=location_in_code))
        if (excerpt := by_index.get(i)) is not None:
            out.append("\n" + "\n".join("  " + ln for ln in excerpt.splitlines()) + "\n")
    return out
