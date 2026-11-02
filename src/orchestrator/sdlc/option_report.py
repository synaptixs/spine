"""The options table of a change — one composition, rendered by the CLI and the MCP tools alike
(SSPN-297, P6 — D9, P6-a).

``openspec options`` and ``requirements_options`` each open the repository their own way (the
CLI's ``_repo_arg``, the server's ``open_repo``), hand the opened path to :func:`read_repo`, and
then call the same functions with the same inputs — so the two surfaces cannot disagree about a
table, which is what the parity test checks.

**What is read where** (P6-f). Landing sites and untraced calls come from the code-only store,
as every tool queries it. The measures come from a **doc-linked copy** of the graph, because the
docs measure counts ``MENTIONS`` edges and the tools keep those out of the store they query; the
copy keeps that true, and the batch the caller holds is never changed.

**What is said** (§2.2). The table is rendered every time and never stored, so a stale one cannot
be read as current. An option's untraced calls sit beside its caller floor and never in it: the
sum of the untraced call sites over its measured function anchors, and *unknown* (``None`` with a
note) when its language is not tracked, the list did not load, or the option is ungrounded —
never a zero nobody measured. Option text is a model's or a person's, so every cell is made inert
(``proof_bundle.inert_text``, B96).

No model is called here unless :func:`generate_for` is: building, measuring and rendering read
the graph and the change, nothing else.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.intake.options import OptionsBlock, load_options
from orchestrator.intake.requirements import CHOICE_QUESTION
from orchestrator.sdlc import ideation
from orchestrator.sdlc.option_measures import OptionMeasure, measure_options, recommend

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.core.llm.client import LLMClient
    from orchestrator.intake.requirements import LoadedChange
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.facts import FactBatch
    from orchestrator.pkg.unbound import UnboundIndex

__all__ = [
    "NO_REPO",
    "RepoInputs",
    "default_client",
    "generate_for",
    "generation_dict",
    "generation_prompt",
    "options_report",
    "read_repo",
    "render_markdown",
]


@dataclass(frozen=True)
class RepoInputs:
    """What one repository gives the table: its graph, the opened checkout (for its docs), whether
    the tree is clean, and the calls the graph could not bind (``None``, with ``unbound_note``
    saying why, when that list did not load)."""

    batch: FactBatch | None
    root: Path | None
    trusted: bool
    unbound: UnboundIndex | None
    unbound_note: str = ""


#: No repository: every option is ungrounded, and the table says so.
NO_REPO = RepoInputs(batch=None, root=None, trusted=True, unbound=None, unbound_note="no repository")


def read_repo(repo: Path) -> RepoInputs:
    """The graph of an opened checkout, with its unbound calls; the code answer survives a list
    that cannot be loaded, as ``blast_radius``'s does."""
    from orchestrator.pkg import load_or_extract, persistence

    try:
        batch, index = persistence.load_with_unbound(repo)
        note = ""
    except Exception as exc:  # noqa: BLE001 — extra evidence; the table must survive
        batch, index, note = load_or_extract(repo), None, type(exc).__name__
    _sha, dirty = persistence.repo_state(repo)
    return RepoInputs(batch=batch, root=Path(repo), trusted=not dirty, unbound=index, unbound_note=note)


def default_client() -> LLMClient:
    """The client a generation uses. Looked up when called, so a test can replace it."""
    from orchestrator.core.llm import LiteLLMClient

    return LiteLLMClient()


# --- the graph views ------------------------------------------------------------------------------


def _copy(batch: FactBatch) -> FactBatch:
    from orchestrator.pkg.facts import FactBatch

    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node)
    for edge in batch.edges:
        out.add_edge(edge)
    return out


def _linked(repo: RepoInputs, pages: Sequence[Any] | None = None) -> tuple[FactStore | None, str]:
    """A doc-linked store over a copy of the graph, and a note when the docs could not be read."""
    from orchestrator.pkg import FactStore

    if repo.batch is None:
        return None, "no repository"
    if repo.root is None:
        return FactStore(repo.batch), "docs not read: no checkout to read them from"
    from orchestrator.pkg.doc_link import link_docs

    try:
        return FactStore(link_docs(_copy(repo.batch), repo.root, pages=list(pages) if pages else None)), ""
    except Exception as exc:  # noqa: BLE001 — docs are extra evidence; the measures survive
        return FactStore(repo.batch), f"docs not read: {type(exc).__name__}: {exc}"


# --- the report -----------------------------------------------------------------------------------


def _untraced(measure: OptionMeasure, code: FactStore | None, repo: RepoInputs) -> tuple[Any, str]:
    """The untraced call sites over an option's measured function anchors, or ``None`` and why."""
    if code is None or measure.grounding == "ungrounded":
        return None, f"ungrounded: {measure.reason}" if measure.reason else "ungrounded"
    from orchestrator.pkg.untraced import UntracedLookup

    lookup = UntracedLookup(
        code, {"": repo.unbound} if repo.unbound is not None else None, repo.unbound_note or "not loaded"
    )
    count = functions = 0
    for anchor in measure.anchors:
        for node_id in anchor.measured:
            node = code.node(node_id)
            found = lookup.for_node(node) if node is not None else {}
            if not found:
                continue  # not a function: the question does not apply
            if found["unresolved_calls"] is None:
                return None, str(found["unresolved_calls_note"])
            count += found["unresolved_calls"]["count"]
            functions += 1
    return {"count": count, "functions": functions}, ""


def _grounding(repo: RepoInputs) -> str:
    if repo.batch is None:
        return "ungrounded"
    if not repo.batch.nodes:
        return "empty"
    return "grounded" if repo.trusted else "untrusted"


def options_report(
    change: LoadedChange, repo: RepoInputs = NO_REPO, *, generation: ideation.GenerationResult | None = None
) -> dict[str, Any]:
    """The change's options, each measured against the graph, the rule's recommendation, and the
    step that records the choice. JSON-ready, keys in a fixed order; plus ``markdown``."""
    from orchestrator.pkg import FactStore

    block: OptionsBlock = load_options(change)
    code = FactStore(repo.batch) if repo.batch is not None else None
    linked, docs_note = _linked(repo)
    measures = measure_options(
        block.options, linked, trusted=repo.trusted, docs_read=linked is not None and not docs_note
    )
    rows = []
    for k, (option, measure) in enumerate(zip(block.options, measures, strict=True), 1):
        untraced, note = _untraced(measure, code, repo)
        rows.append(
            {
                "number": k,
                "title": option.title,
                "approach": option.approach,
                "anchors": list(option.anchors),
                "creates": list(option.creates),
                "risks": option.risks,
                "source": option.source,
                "measure": measure.to_dict(),
                "unresolved_calls": untraced,
                "unresolved_calls_note": note,
            }
        )
    # Quoted: the path comes from disk, and the step is a command a person or an agent runs.
    where = shlex.quote(str(change.directory.resolve()))
    if block.options:
        recommendation: dict[str, Any] | None = recommend(measures).to_dict()
        step = (
            f'Record the choice: orchestrator openspec answer {where} --question "{CHOICE_QUESTION}" '
            '--answer "Option <n>"'
        )
    else:
        recommendation = None
        step = (
            f"No options yet: write a ## Options section in {change.design_path.name}, or run: "
            f"orchestrator openspec options {where} <repo> --generate (one capped model call)"
        )
    report: dict[str, Any] = {
        "change": change.change_id,
        "grounding": _grounding(repo),
        "options_present": block.present,
        "problems": list(block.problems),
        "options": rows,
        "recommendation": recommendation,
        "question": CHOICE_QUESTION,
        "next": step,
        "docs": {"read": linked is not None and not docs_note, "note": docs_note},
        "generation": generation_dict(generation) if generation is not None else None,
    }
    report["markdown"] = render_markdown(report)
    return report


def generation_dict(result: ideation.GenerationResult) -> dict[str, Any]:
    refusal = result.refusal
    return {
        "status": result.status,
        "model": result.model,
        "cost_usd": round(result.cost_usd, 6),
        "cost_estimated": result.cost_estimated,
        "estimate_usd": round(result.estimate.usd, 6) if result.estimate is not None else None,
        "session_spent_usd": round(result.session_spent_usd, 6),
        "written": [o.title for o in result.options],
        "problems": list(result.problems),
        "refusal": None
        if refusal is None
        else {
            "estimate_usd": round(refusal.estimate_usd, 6),
            "breaches": [
                {"limit": b.limit, "limit_usd": b.limit_usd, "would_be_usd": round(b.would_be_usd, 6)}
                for b in refusal.breaches
            ],
            "message": refusal.message,
            "choices": list(refusal.choices),
        },
    }


# --- the markdown ---------------------------------------------------------------------------------

_DASH = "—"
_HEADER = (
    "| # | Option | Source | Anchors resolved | Callers ≥ | Untraced calls | Untested reached "
    "| Docs | Areas | Reuse |"
)


def _cell(text: object, limit: int = 200) -> str:
    """Text from ``design.md`` or a model — a title, a source, an anchor, a problem — made inert.
    Spine's own sentences (the next step, a refusal) are written as they are: they carry a command
    to copy, and an escaped ``_`` in a path would break it."""
    from orchestrator.sdlc.proof_bundle import inert_text

    return inert_text(text, limit) or _DASH


def _prose(text: object) -> str:
    return _cell(text, 2000)


def _row(row: dict[str, Any]) -> str:
    m = row["measure"]
    head = f"| {row['number']} | {_cell(row['title'])} | {_cell(row['source'] or 'not stated')} |"
    if m["grounding"] == "ungrounded":
        return head + " — | — | — | — | — | — | — |"
    total = len(m["resolved"]) + len(m["unresolved"])
    untraced = row["unresolved_calls"]
    cells = [
        f"{len(m['resolved'])} of {total}",
        str(m["callers"]["at_least"]) if m["callers"] is not None else _DASH,
        str(untraced["count"]) if untraced is not None else "n/a",
        str(len(m["untested"])) if m["untested"] is not None else _DASH,
        str(m["docs"]) if m["docs"] is not None else _DASH,
        str(len(m["areas"])),
        f"{round(m['reuse'] * 100)}%" if m["reuse"] is not None else _DASH,
    ]
    return head + " " + " | ".join(cells) + " |"


def render_markdown(report: dict[str, Any]) -> str:
    """The table a person reads. The same report, the same bytes."""
    lines = [f"## Options for {_cell(report['change'])}", ""]
    gen = report["generation"]
    if gen is not None:
        if gen["status"] == "refused":
            lines += [f"**Generation refused:** {gen['refusal']['message']}", ""]
        elif gen["status"] == "failed":
            lines += ["**Generation failed — nothing was written:**"]
            lines += [f"- {_prose(p)}" for p in gen["problems"]] + [""]
        else:
            spent = f"${gen['cost_usd']:.4f}" + (" (estimated)" if gen["cost_estimated"] else "")
            lines += [f"**Generated** {len(gen['written'])} option(s) with {gen['model']} for {spent}.", ""]
    if report["problems"]:
        lines += ["**`## Options` problems:**"] + [f"- {_prose(p)}" for p in report["problems"]] + [""]
    if not report["options"]:
        lines += [report["next"]]
        return "\n".join(lines) + "\n"
    lines += [
        f"_Measured against the code graph: {report['grounding']}. Callers are a floor (≥); untraced "
        "calls are same-name calls the graph could not type — a hint, never a caller._",
        "",
        _HEADER,
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_row(r) for r in report["options"]]
    notes = []
    for r in report["options"]:
        m = r["measure"]
        if m["grounding"] == "ungrounded":
            notes.append(f"- Option {r['number']} is ungrounded: {m['reason']}")
        elif m["unresolved"]:
            missing = _prose(", ".join(m["unresolved"]))
            notes.append(f"- Option {r['number']} names code the graph does not have: {missing}")
        if r["unresolved_calls"] is None and m["grounding"] != "ungrounded":
            notes.append(f"- Option {r['number']} untraced calls: {r['unresolved_calls_note']}")
    if report["docs"]["note"]:
        notes.append(f"- {_prose(report['docs']['note'])}")
    if notes:
        lines += [""] + notes
    rec = report["recommendation"]
    lines.append("")
    if rec["recommended"] is not None:
        number = rec["number"]
        lines.append(f"**Recommended:** Option {number} — {_cell(rec['recommended'])} ({rec['reason']}).")
    else:
        lines.append(f"**No recommendation:** {rec['reason']}.")
    if rec["not_ranked"]:
        lines.append(f"Not ranked (ungrounded): {_prose(', '.join(rec['not_ranked']))}.")
    lines += [f"Rule: {rec['rule']}. {rec['bias']}", "", report["next"]]
    return "\n".join(lines) + "\n"


# --- generation -----------------------------------------------------------------------------------


def generation_prompt(change: LoadedChange, repo: RepoInputs = NO_REPO) -> ideation.IdeationPrompt:
    """The one call's prompt: the why-fields, the landing sites, the doc sections bound to them,
    and a bounded overview — each part in its own share (``ideation.build_prompt``)."""
    intent = change.intent
    if repo.batch is None:
        return ideation.build_prompt(intent)
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.overview import build_overview
    from orchestrator.sdlc.investigate import build_investigation

    code = FactStore(repo.batch)
    problem = (intent.problem or intent.description or "").strip()
    landings = build_investigation(intent.title, problem, store=code, root=repo.root).landing
    pages: dict[str, Any] = {}
    if repo.root is not None:
        from orchestrator.pkg.doc_source import read_doc_pages

        try:
            pages = {p.title: p for p in read_doc_pages(repo.root)}
        except Exception:  # noqa: BLE001 — docs are extra evidence; the prompt survives
            pages = {}
    linked, _note = _linked(repo, list(pages.values()))
    docs = ideation.doc_sections(linked, landings, pages) if linked is not None else ()
    return ideation.build_prompt(intent, landings=landings, docs=docs, overview=build_overview(repo.batch))


async def generate_for(
    change: LoadedChange,
    repo: RepoInputs = NO_REPO,
    *,
    approve_usd: float = 0.0,
    model: str | None = None,
    client: LLMClient | None = None,
) -> ideation.GenerationResult:
    """At most one model call for this change (``ideation.generate``), on the default client."""
    from orchestrator.core.env import load_local_env

    load_local_env()
    prompt = generation_prompt(change, repo)
    return await ideation.generate(
        change,
        prompt,
        client if client is not None else default_client(),
        model=model,
        approve_usd=approve_usd,
    )
