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


@dataclass
class Investigation:
    title: str
    problem: str
    landing: list[Landing] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)  # distinct owning modules
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

    retriever = GroundedRetriever(store)
    symbols = retriever.relevant_symbols(f"{title}\n{problem}", limit=max_symbols)
    parents = store.parents_index()

    landing: list[Landing] = []
    areas: list[str] = []
    for n in symbols:
        module = _owning_module(store, n.id, parents)
        landing.append(
            Landing(
                name=n.name,
                where=str(n.provenance) if n.provenance else "",
                kind=n.kind.value,
                callers=len(store.callers_of(n.id)),
                module=module,
            )
        )
        if module and module not in areas:
            areas.append(module)

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
            loc = f" — {hit.where}" if hit.where else ""
            in_mod = f" _(in {hit.module})_" if hit.module and hit.module != hit.name else ""
            out.append(f"- `{hit.name}` ({hit.kind}, {hit.callers} caller(s)){in_mod}{loc}")
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
