"""Planning from a requirements source, shared by `sdlc plan --source` and the MCP `sdlc_plan`
(SSPN-279).

**Why one path.** An editor's *Plan from requirement* and a terminal's `sdlc plan --source` must
render the same document from the same ticket and leave the same spec and source text beside it:
the approval gate re-derives the document from those files, and a plan made one way and gated
another is refused as changed (D102). So the spec derivation, the fresh fetch §8 checks the
criteria against, and the render all live here. The CLI prints what this returns; the MCP tool
returns it. Nothing here prints: warnings come back as text, refusals as :class:`PlanSourceError`
carrying the CLI's exit code.

**What spends.** Only an extraction calls the intake model, on whatever key the caller loaded. A
cached spec is reused by the rule `analyze_cached` applies (:func:`reusable_plan`): an entry for a
local file that changed since is extracted again. The model is metered through the
`BudgetedLLMClient` runs use, with no cap — the spend is reported, never enforced, as intake had no
cap before either.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator import core
from orchestrator.core.llm.budget import BudgetedLLMClient, RunBudget
from orchestrator.core.llm.client import LLMError
from orchestrator.intake import cache as intake_cache
from orchestrator.intake import factory
from orchestrator.intake.service import SourceUriError, parse_source_uri
from orchestrator.sdlc import spec_context

# The intake entry points are looked up when called, never bound here: tests (and the CLI's own,
# from before this module) replace them on their home modules.

if TYPE_CHECKING:
    from orchestrator.intake.intents import PromptFit
    from orchestrator.intake.service import BacklogPlan
    from orchestrator.intake.source import FetchTreeResult


class PlanSourceError(Exception):
    """A source that cannot be planned: the message as the CLI prints it, and its exit code."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def source_state(source: str, *, follow_links: bool = False) -> dict[str, Any]:
    """Whether ``source`` is already extracted, and its intents: no model, nothing written (D105)."""
    parse_source_uri(source)
    plan = intake_cache.reusable_plan(source, variant=intake_cache.FOLLOW_LINKS if follow_links else "")
    intents = [{"id": s.intent_id, "title": s.title} for s in plan.specs] if plan is not None else []
    return {"source": source, "cached": plan is not None, "intents": intents, "spent_usd": 0}


async def fetch_ticket_documents(
    source: str, *, follow_links: bool = False, service: Any = None
) -> tuple[list[Any], FetchTreeResult, list[str]]:
    """The ticket's documents, fetched fresh and never analysed — what `source.txt` is built from.

    A source that cannot be read is refused, whatever failed underneath: a missing file, an HTTP
    error, an MCP server that refused (ledger N12). A source that answers with *nothing* is said
    out loud rather than planned as if the ticket were empty (N13): §8 then has only the spec.
    """
    from orchestrator.intake.source import document_text

    try:
        service = service if service is not None else factory.build_service_for(source, dry_run=True)
        root_id = parse_source_uri(source)[1]
        fetched = await (
            service.fetch_source_documents(root_id, follow_links=True)
            if follow_links
            else service.fetch_source_documents(root_id)
        )
    except (SourceUriError, factory.IntakeNotConfiguredError) as exc:
        raise PlanSourceError(str(exc)) from exc
    except factory.source_read_errors() as exc:
        raise PlanSourceError(f"could not read {source} — {type(exc).__name__}: {exc}") from exc
    documents = list(fetched.documents)
    warnings = []
    if not any(document_text(d).strip() for d in documents):
        # No documents, or documents with nothing in them — a blank page reads the same to §8.
        warnings.append(f"{source} returned no text — the criteria are checked against the spec alone.")
    return documents, fetched, warnings


@dataclass
class Derived:
    """A spec derived from a source, with what §8 and the document header need beside it."""

    plan: BacklogPlan
    #: ``None`` when the source holds several intents and none was named (``choose_first=False``).
    spec: dict[str, Any] | None
    documents: list[Any] = field(default_factory=list)
    fetched: FetchTreeResult | None = None
    extraction: PromptFit | None = None
    issue_type: str = ""
    #: Whether the intake model ran (a cache miss, a changed file, or ``refresh``).
    extracted: bool = False
    spent_usd: float = 0.0
    #: Part of ``spent_usd`` was estimated for a model with no known price.
    spent_estimated: bool = False
    warnings: list[str] = field(default_factory=list)


async def derive(
    source: str,
    *,
    root: str | Path,
    intent: str | None = None,
    follow_links: bool = False,
    refresh: bool = False,
    issue_type: str = "",
    choose_first: bool = True,
    previous: BacklogPlan | None = None,
    on_analyzed: Callable[[BacklogPlan], None] | None = None,
) -> Derived:
    """The spec ``source`` holds for ``intent`` (or its first, or none when ``choose_first`` is off
    and it holds several), the ticket's documents fetched fresh, and what the extraction spent.

    ``previous`` is the cached plan from before a ``refresh``, so a renamed intent is said to be
    one; ``on_analyzed`` sees the plan before an intent is chosen, as the CLI's refresh warnings
    must be said even when the choice then fails.
    """
    variant = intake_cache.FOLLOW_LINKS if follow_links else ""
    extracted = refresh or intake_cache.reusable_plan(source, variant=variant) is None
    budget = RunBudget(max_cost_usd=0.0)
    try:
        service = factory.build_service_for(
            source, dry_run=True, llm=BudgetedLLMClient(core.llm.LiteLLMClient(), budget)
        )
    except (SourceUriError, factory.IntakeNotConfiguredError, ValueError) as exc:
        raise PlanSourceError(str(exc)) from exc
    # The spec writer is told what this checkout is written in and which of its symbols the
    # ticket's words match (NSS-1243 invented a JavaScript file for a C# repository). Only a fresh
    # extraction reads it; a cached spec is what it was.
    spec_context.attach_repo_context(service, root)
    try:
        plan = await intake_cache.analyze_cached(
            service, source, refresh=refresh, log=lambda _m: None, follow_links=follow_links
        )
    except factory.IntakeNotConfiguredError as exc:
        raise PlanSourceError(str(exc)) from exc
    except factory.source_read_errors() as exc:
        # A cold cache reads the source here, before the fresh fetch below.
        raise PlanSourceError(f"could not read {source} — {type(exc).__name__}: {exc}") from exc
    except LLMError as exc:
        raise PlanSourceError(str(exc)) from exc
    spent_usd, spent_estimated = budget.spent(), budget.estimated() > 0
    if on_analyzed is not None:
        on_analyzed(plan)
    if not plan.specs:
        raise PlanSourceError("No specs derived from the source — nothing to plan.", code=3)
    if intent:
        chosen = next((s for s in plan.specs if s.intent_id == intent), None)
        if chosen is None:
            ids = ", ".join(s.intent_id for s in plan.specs)
            renamed = previous is not None and any(s.intent_id == intent for s in previous.specs)
            why = " — the re-extraction renamed or dropped it" if renamed else ""
            raise PlanSourceError(f"Intent {intent!r} not found. Available: {ids}{why}", code=3)
    elif len(plan.specs) > 1 and not choose_first:
        return Derived(
            plan=plan, spec=None, extracted=extracted, spent_usd=spent_usd, spent_estimated=spent_estimated
        )
    else:
        chosen = plan.specs[0]
    # The spec comes from the cache — re-extracting it could move an approved plan — but the ticket
    # text §8 checks against is read fresh, with no model call: what the ticket says *now*,
    # attachments uncut, which a cache entry written before either could not hold (Track E, D3).
    documents, fetched, warnings = await fetch_ticket_documents(
        source, follow_links=follow_links, service=service
    )
    # The cache holds the bounded documents the extractor was given, so their fit is what the spec
    # was derived from — not the fresh fetch, which may say something newer. Only when the
    # extractor ran: a structured source (OpenSpec) is parsed verbatim, so nothing in it was cut.
    from orchestrator.intake.intents import extraction_fit

    extraction = extraction_fit(plan.documents) if getattr(service, "uses_the_extractor", True) else None
    if not issue_type:
        from orchestrator.intake.ticket_meta import resolve_ticket_meta

        issue_type = resolve_ticket_meta(plan, chosen).issue_type
    return Derived(
        plan=plan,
        spec=chosen.model_dump(),
        documents=documents,
        fetched=fetched,
        extraction=extraction,
        issue_type=issue_type,
        warnings=warnings,
        extracted=extracted,
        spent_usd=spent_usd,
        spent_estimated=spent_estimated,
    )


def what_the_spec_saw(
    fetched: FetchTreeResult | None, extraction: PromptFit | None, *, hand_written: bool
) -> tuple[str, str]:
    """The Linked pages line and the warning, from the fresh fetch and the extraction's fit.

    ``hand_written``: the pages reached `source.txt` and nothing else. ``extraction`` is ``None``
    then, and also for a structured source the extractor never read — either way nothing was cut.
    """
    from orchestrator.intake.follow_links import extraction_warning

    if fetched is None:
        return "", ""
    if fetched.follow is not None:
        linked = fetched.follow.summary(extraction=extraction, spec_extracted=not hand_written)
    else:
        linked = fetched.linked_pages
    return linked, extraction_warning(extraction) if extraction is not None else ""


@dataclass
class Rendered:
    intent_key: str
    document: str
    source_text: str
    warnings: list[str] = field(default_factory=list)


async def render(
    spec: dict[str, Any],
    *,
    root: str | Path,
    language: str = "auto",
    issue_type: str = "",
    documents: list[Any] | None = None,
    fetched: FetchTreeResult | None = None,
    extraction: PromptFit | None = None,
    hand_written: bool,
    source_given: bool,
) -> Rendered:
    """The build document for ``spec``, as `sdlc plan` renders it, and the source text beside it."""
    from orchestrator.intake.follow_links import extraction_note
    from orchestrator.intake.source import document_text
    from orchestrator.sdlc.builddoc import build_plan, load_approval, load_journey
    from orchestrator.sdlc.toolchains import resolve_language

    intent_key = str(spec.get("intent_id") or "spec")
    # The whole ticket — every attachment uncut — because §8 checks criteria against its own
    # words; the extractor already had its bounded view (`SourceDocument.full_body`).
    source_text = "\n\n".join(document_text(d) for d in documents or [])
    linked, lost = what_the_spec_saw(fetched, extraction, hand_written=hand_written)
    warnings = (
        [f"{lost} — source.txt still holds every word, and §8 checks the criteria against it."]
        if lost
        else []
    )
    # Resolved against the repo being planned, not left as the literal "auto" — the codegen prompt,
    # the layout and the test environment all read this, and the old `python` default handed a C#
    # repository Python scaffolding without saying so.
    document = await build_plan(
        spec,
        root=root,
        language=resolve_language(Path(root), language),
        issue_type=issue_type,
        source_text=source_text,
        linked_pages=linked or ("not followed — `--follow-links` reads them" if source_given else ""),
        extraction=extraction_note(extraction) if extraction is not None else "",
        # Rendered, never stored in the document: a plan that changed since it was approved shows
        # as stale rather than carrying an approval it outgrew.
        approval=load_approval(intent_key, root=root),
        # What every run of this ticket did, appended beneath the plan. Regenerating is what
        # refreshes the view; the entries themselves are never rewritten.
        journey=load_journey(intent_key, root=root),
    )
    return Rendered(intent_key=intent_key, document=document, source_text=source_text, warnings=warnings)


def persist_rendered(
    rendered: Rendered, spec: dict[str, Any], *, root: str | Path
) -> tuple[Path, Path | None]:
    """Write the plan, with its spec and source text beside it, where the approval gate reads them."""
    from orchestrator.sdlc.builddoc import persist, save_source_text, save_spec

    # Beside the plan, so the gate's re-derivation sees the same section 8 — and the same spec, which
    # a build started later (`sdlc_build`, SSPN-274) hands back to the gate.
    save_source_text(rendered.intent_key, rendered.source_text, root=root)
    save_spec(rendered.intent_key, spec, root=root)
    return persist(rendered.document, intent_id=rendered.intent_key, root=root)


__all__ = [
    "Derived",
    "PlanSourceError",
    "Rendered",
    "derive",
    "fetch_ticket_documents",
    "persist_rendered",
    "render",
    "source_state",
    "what_the_spec_saw",
]
