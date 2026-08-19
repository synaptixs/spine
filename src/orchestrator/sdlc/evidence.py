"""Evidence — what the graph knows about a ticket, before anyone designs anything.

Three deterministic passes compose into one artifact:

* **investigate** — the symbols this ticket lexically lands on, each with its ``file:line``,
  kind, caller count and owning module.
* **rca** — fault site, regression surface, recently-changed, ranked hypotheses. Deterministic;
  ``build_rca`` only reaches a model when handed one, and nothing here hands it one.
* **blast radius** — computed from **the landing sites**, not from a design's proposal.

That last point is the reason this module exists rather than a helper inside ``design.py``.
Today ``design.py`` calls ``blast_radius(store, design["files_to_touch"])`` — the impact
analysis describes the files the design *guessed at*. When the guess is wrong the result is a
faithful analysis of a fiction, and it reads as verification. Keyed off the landing sites
instead, the blast radius is a fact about the ticket rather than a fact about a proposal.

**Phase 1 produces this and nothing consumes it.** ``design``, ``codegen`` and the acceptance
criteria are wired to it in Phase 2 (see ``docs/specs/graphir-sdlc-workflow.md``). Producing it
first is deliberate: it ships in shadow with zero behaviour change, and every later phase has
something true to be judged against.

**Determinism, and one honest caveat.** Everything here is a pure function of the tree except
``rca``'s recently-changed check, which shells out to ``git log -n 40``. That makes the digest a
pure function of *the commit* rather than of the working tree alone — stable at a given commit,
different if history is rewritten. That satisfies the boundary's ``(commit, inputs) → identical
output`` wording, and is said here rather than discovered later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg import FactStore

if TYPE_CHECKING:  # `runtime.tool_registry` imports this module to register its tools.
    from orchestrator.runtime.tool_registry import ToolRegistry

__all__ = [
    "Evidence",
    "LandingFact",
    "build_evidence",
    "evidence_digest",
    "evidence_from_parts",
    "landing_files",
    "rca_problem",
    "register_sdlc_tools",
    "render_evidence_md",
    "to_dict",
]

# Landing sites feeding the blast radius. More than a handful stops being a blast radius and
# starts being the repository; the investigation itself caps symbols at 10.
_MAX_BLAST_FILES = 10


@dataclass(frozen=True)
class LandingFact:
    """One place the ticket lands — the whole fact, not the filename.

    ``autorun`` currently reduces this to ``where.split(":")[0]`` before anything downstream
    sees it, so design and codegen receive filenames where the research proved symbols. Keeping
    the structure is defect 3 in the spec.
    """

    name: str
    where: str  # "file:line"
    kind: str
    callers: int
    module: str

    @property
    def file(self) -> str:
        return self.where.split(":", 1)[0] if self.where else ""


@dataclass(frozen=True)
class Evidence:
    """The deterministic answer to "what does the graph know about this ticket?"."""

    title: str = ""
    problem: str = ""
    issue_type: str = ""
    landing: tuple[LandingFact, ...] = ()
    areas: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    rca: dict[str, Any] = field(default_factory=dict)
    blast_radius: dict[str, Any] = field(default_factory=dict)
    # The PKG had grounded nodes at all. When false, every section below is legitimately empty
    # and says so — an empty Evidence that announces itself beats a confident-looking one
    # assembled from nothing.
    grounded: bool = False


def _landing_files(landing: tuple[LandingFact, ...]) -> tuple[str, ...]:
    """Distinct files behind the landing symbols, first-seen order preserved."""
    out: list[str] = []
    for hit in landing:
        name = hit.file
        if name and name not in out:
            out.append(name)
        if len(out) >= _MAX_BLAST_FILES:
            break
    return tuple(out)


def rca_problem(title: str, problem: str) -> str:
    """The text RCA localizes against. One definition, so the shadow and the API agree."""
    return f"{title}\n{problem}".strip()


def landing_files(landing_rows: list[dict[str, Any]]) -> tuple[str, ...]:
    """Distinct files behind investigate's landing rows — the blast radius's input."""
    return _landing_files(
        tuple(
            LandingFact(
                name=str(row.get("name", "")),
                where=str(row.get("where", "")),
                kind=str(row.get("kind", "")),
                callers=int(row.get("callers", 0)),
                module=str(row.get("module", "")),
            )
            for row in landing_rows
        )
    )


def evidence_from_parts(
    *,
    title: str,
    problem: str,
    issue_type: str,
    investigate: dict[str, Any],
    rca: dict[str, Any],
    blast: dict[str, Any],
) -> Evidence:
    """Assemble Evidence from the three tool outputs.

    Both paths that produce Evidence — ``build_evidence`` below, and the shadow pass that runs
    the tools through the registry — end here. Two assemblers would be two definitions of the
    same artifact, and the first divergence between them would be reported as a divergence in
    the *pipeline*, which is exactly the confusion the shadow comparison exists to remove.
    """
    rows = list(investigate.get("landing") or [])
    landing = tuple(
        LandingFact(
            name=str(row.get("name", "")),
            where=str(row.get("where", "")),
            kind=str(row.get("kind", "")),
            callers=int(row.get("callers", 0)),
            module=str(row.get("module", "")),
        )
        for row in rows
    )
    return Evidence(
        title=title,
        problem=problem.strip(),
        issue_type=issue_type,
        landing=landing,
        areas=tuple(str(a) for a in (investigate.get("areas") or [])),
        files=landing_files(rows),
        rca=dict(rca),
        blast_radius=dict(blast),
        grounded=bool(investigate.get("grounded", False)),
    )


async def build_evidence(
    title: str,
    problem: str,
    *,
    store: FactStore,
    root: Path | str | None = None,
    issue_type: str = "",
) -> Evidence:
    """Compose the three passes into one artifact. Deterministic, no model.

    Calls the same functions the tool nodes name — no ``llm=`` argument appears anywhere in this
    module, on purpose: a tool node that reached a model would be a model node wearing a tool's
    label, and the whole boundary rests on the two being distinguishable.
    """
    investigate = _tool_investigate(store=store, title=title, problem=problem, root=root)
    rca = await _tool_rca(store=store, problem=rca_problem(title, problem), root=root)
    files = landing_files(list(investigate.get("landing") or []))
    blast = _tool_blast_radius(store=store, files=list(files))
    return evidence_from_parts(
        title=title,
        problem=problem,
        issue_type=issue_type,
        investigate=investigate,
        rca=rca,
        blast=blast,
    )


def _rca_to_dict(report: Any) -> dict[str, Any]:
    return {
        "exception": report.exception,
        "fault_site": report.fault_site,
        "fault_module": report.fault_module,
        "callers": list(report.callers),
        "hypotheses": [
            {"claim": h.claim, "evidence": list(h.evidence), "confidence": h.confidence}
            for h in report.hypotheses
        ],
        "regression_surface": list(report.regression_surface),
        "recently_changed": report.recently_changed,
        "fix_approach": report.fix_approach,
        "grounded": report.grounded,
        # Recorded so a reader can tell a deterministic report from an enriched one without
        # inferring it. Always false here; ``build_rca`` is called with no model.
        "llm": report.llm,
    }


def to_dict(ev: Evidence) -> dict[str, Any]:
    """Serialisable form — what ``evidence.json`` holds and what the digest is taken over."""
    return {
        "title": ev.title,
        "problem": ev.problem,
        "issue_type": ev.issue_type,
        "grounded": ev.grounded,
        "areas": list(ev.areas),
        "files": list(ev.files),
        "landing": [
            {
                "name": hit.name,
                "where": hit.where,
                "kind": hit.kind,
                "callers": hit.callers,
                "module": hit.module,
            }
            for hit in ev.landing
        ],
        "rca": ev.rca,
        "blast_radius": ev.blast_radius,
    }


def evidence_digest(ev: Evidence) -> str:
    # Imported here, not at module scope. `runtime.tool_registry.default_registry()` imports this
    # module to register the SDLC's tools, so a module-level import back into it is a genuine
    # cycle — one CodeQL flagged, correctly, even though laziness on the other side kept it from
    # biting at runtime. Only the annotation and this one call need the module, so neither has to
    # be an import-time edge.
    from orchestrator.runtime.tool_registry import digest_of

    return digest_of(to_dict(ev))


def render_evidence_md(ev: Evidence) -> str:
    """Render Evidence for a human. Honest when a section has nothing behind it."""
    out: list[str] = [f"# Evidence — {ev.title or 'ticket'}\n"]
    out.append(
        "_Deterministic: the graph, the call sites and git history answer here. No model ran, "
        "and the same commit produces the same bytes._\n"
    )
    if not ev.grounded:
        out.append(
            "> **The knowledge graph has no grounded nodes for this repository.** Every section "
            "below is empty because there is nothing to ground against — not because the ticket "
            "is clean.\n"
        )

    out.append("## Where it lands")
    if ev.landing:
        for hit in ev.landing:
            loc = f" — `{hit.where}`" if hit.where else ""
            in_mod = f" _(in {hit.module})_" if hit.module and hit.module != hit.name else ""
            out.append(f"- `{hit.name}` ({hit.kind}, {hit.callers} caller(s)){in_mod}{loc}")
        if ev.areas:
            out.append(f"\n_Areas: {', '.join(ev.areas)}_")
    else:
        out.append("_No symbol matched the ticket's terms._")
    out.append("")

    out.append("## Root cause")
    rca = ev.rca or {}
    if rca.get("fault_site"):
        out.append(f"**Fault site:** {rca['fault_site']}")
        if rca.get("recently_changed"):
            out.append("\n⚠ This module changed recently — a regression is the leading hypothesis.")
    else:
        out.append("_Not localized to a repo symbol._")
    hypotheses = rca.get("hypotheses") or []
    if hypotheses:
        out.append("\n_Hypotheses, ranked by evidence:_")
        out.extend(f"{i}. **[{h['confidence']}]** {h['claim']}" for i, h in enumerate(hypotheses, 1))
    surface = rca.get("regression_surface") or []
    if surface:
        out.append(f"\n_Regression surface ({len(surface)}):_")
        out.extend(f"- {s}" for s in surface[:10])
    out.append("")

    out.append("## Blast radius")
    out.append("_Computed from the landing sites above — a fact about the ticket, not about a proposal._\n")
    from orchestrator.sdlc.impact import render_md as _render_blast

    # ``impact.render_md`` emits its own "## Blast radius" heading for design.md; keeping both
    # printed the section header twice.
    rendered = _render_blast(ev.blast_radius or {}).strip()
    if rendered.startswith("## Blast radius"):
        rendered = rendered.split("\n", 1)[1].strip() if "\n" in rendered else ""
    out.append(rendered or "_Nothing to compute: no landing site resolved to a file._")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------------------
# Tool bindings — the callables a GraphIR ``tool`` node names.
#
# Each returns a JSON-serialisable value so the registry can digest it. They are thin on
# purpose: the logic belongs to the modules that already own it, and a tool that reimplemented
# any of it would be a second source of truth for the same fact.
# --------------------------------------------------------------------------------------


def _tool_investigate(
    *, store: FactStore, title: str, problem: str, root: Path | str | None = None
) -> dict[str, Any]:
    from orchestrator.sdlc.investigate import build_investigation

    inv = build_investigation(title, problem, store=store, root=root)
    return {
        "landing": [
            {
                "name": hit.name,
                "where": hit.where,
                "kind": hit.kind,
                "callers": hit.callers,
                "module": hit.module,
            }
            for hit in inv.landing
        ],
        "areas": list(inv.areas),
        "grounded": inv.grounded,
    }


async def _tool_rca(*, store: FactStore, problem: str, root: Path | str | None = None) -> dict[str, Any]:
    from orchestrator.sdlc.rca import build_rca

    return _rca_to_dict(await build_rca(problem, store=store, root=root))


def _tool_blast_radius(*, store: FactStore, files: list[str]) -> dict[str, Any]:
    from orchestrator.sdlc.impact import blast_radius
    from orchestrator.sdlc.impact import to_dict as blast_to_dict

    return blast_to_dict(blast_radius(store, list(files))) if files else {}


def _tool_validity(
    *,
    store: FactStore,
    spec: dict[str, Any],
    landing: list[str],
    issue_type: str = "",
    issue_key: str = "",
    prior_runs: list[Any] | None = None,
    root: Path | str | None = None,
    context_budget: int = 0,
) -> dict[str, Any]:
    from orchestrator.sdlc.validity import assess

    assessment = assess(
        spec,
        store=store,
        landing=list(landing),
        issue_type=issue_type,
        issue_key=issue_key,
        prior_runs=list(prior_runs or []),
        root=root,
        context_budget=context_budget,
    )
    return {
        "verdict": assessment.verdict.value,
        "findings": [
            {"check": f.check, "detail": f.detail, "evidence": f.evidence} for f in assessment.findings
        ],
    }


def register_sdlc_tools(registry: ToolRegistry) -> None:
    """Register the SDLC's deterministic tools. Called lazily by ``default_registry()``."""
    registry.register("sdlc.investigate", _tool_investigate)
    registry.register("sdlc.rca", _tool_rca)
    registry.register("sdlc.blast_radius", _tool_blast_radius)
    registry.register("sdlc.validity", _tool_validity)
