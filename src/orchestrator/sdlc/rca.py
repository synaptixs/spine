"""Root-cause analysis (C2): a bug → grounded RCA + fix approach, before code.

Composes the fault localizer (C6), the graph impact primitives (C1), and recent
git churn into a **gated** RCA report: the fault site, ranked root-cause
*hypotheses* with evidence, the regression surface a fix must cover, and a
scoped fix approach. Deterministic-first (no LLM); an optional LLM enriches the
hypotheses + prose from the *same* evidence.

It stops at the report — a human decides whether to build the fix. RCA is
**hypotheses-with-evidence, never asserted cause**: the graph supplies what a
good engineer would look at (who calls the fault, what changed recently, what
the exception implies); the ranking is a starting point, not a verdict.

The bug can arrive as a stack trace (C6), a Jira bug (C3's `jira://`), or inline
text — all reduce to "text we localize + ground against the PKG".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.sdlc import brief
from orchestrator.sdlc.brief import Brief, Tier
from orchestrator.sdlc.churn import changed_recently
from orchestrator.sdlc.localize import Localization, localize_trace

logger = logging.getLogger("orchestrator.sdlc.rca")

#: Bounds the "Not verified" caveat now quotes, so the number a reader sees and the number
#: we actually cut at cannot drift apart — the defect that produced "reaches 13" beside a
#: list of eight.
_MAX_CALLERS = 10
_MAX_SURFACE = 15

# Exception class → a generic but grounded starting hypothesis. These are the
# "what does this error usually mean" priors an engineer applies before reading.
_EXC_HINTS: dict[str, str] = {
    "TypeError": "A type mismatch at the fault site — an argument's type differs from what the code expects.",
    "ValueError": "An invalid value reached the fault site — validate/guard the input before it's used.",
    "KeyError": "A missing key — the code indexes a mapping without ensuring the key is present.",
    "AttributeError": "A None or wrong-type object — an expected attribute is absent (often unhandled None).",
    "IndexError": "An out-of-range index — a sequence is shorter than assumed (often empty).",
    "ZeroDivisionError": "A division by zero — a denominator wasn't guarded.",
    "FileNotFoundError": "A missing file/path — the path is wrong or the file was never created.",
    "TimeoutError": "An operation exceeded its deadline — a slow/blocked dependency or missing timeout.",
    "AssertionError": "An invariant the code assumed did not hold — trace back what established it.",
}


@dataclass(frozen=True)
class Hypothesis:
    claim: str
    evidence: tuple[str, ...] = ()
    confidence: str = "medium"  # high | medium | low


@dataclass
class RCAReport:
    problem: str = ""
    exception: str = ""
    fault_site: str = ""  # "func at file:line"
    fault_module: str = ""
    #: The source at the fault line, fenced, or "". An RCA whose hypotheses are templates over
    #: code nobody quoted asks the reader to take the ranking on faith; the point of ranking
    #: by evidence is that the evidence is on the page.
    fault_source: str = ""
    callers: list[str] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    regression_surface: list[str] = field(default_factory=list)
    recently_changed: bool = False
    fix_approach: str = ""
    grounded: bool = False
    llm: bool = False


def _exception_class(exception: str) -> str:
    return exception.split(":", 1)[0].split(".")[-1].strip() if exception else ""


def _regression_surface(store: FactStore, fault_file: str) -> list[str]:
    """Who depends on the fault's module + its call hotspots — a fix must not break these."""
    from orchestrator.sdlc.impact import blast_radius

    br = blast_radius(store, [fault_file])
    surface: list[str] = []
    for m in br.modules:
        if m.importer_names:
            surface.append(f"{m.module} — imported by {', '.join(m.importer_names)}")
        for hot in m.hotspots:
            surface.append(f"{hot.name} ({hot.callers} caller(s)) — {hot.where}")
    return surface


def _deterministic_hypotheses(
    loc: Localization, *, recently_changed: bool, fault_module: str
) -> list[Hypothesis]:
    hyps: list[Hypothesis] = []
    exc_class = _exception_class(loc.exception)

    if recently_changed and fault_module:
        hyps.append(
            Hypothesis(
                claim=f"Recent change to `{fault_module}` — likely a regression; review its latest commits.",
                evidence=(f"`{fault_module}` was modified in the recent git history.",),
                confidence="high",
            )
        )
    if exc_class in _EXC_HINTS:
        hyps.append(
            Hypothesis(
                claim=_EXC_HINTS[exc_class],
                evidence=(f"Exception: {loc.exception}",),
                confidence="medium",
            )
        )
    if loc.fault is not None and loc.callers:
        hyps.append(
            Hypothesis(
                claim=f"A call site may pass invalid input to `{loc.fault.func}` — check the callers below.",
                evidence=tuple(loc.callers[:5]),
                confidence="medium",
            )
        )
    if loc.fault is None and loc.stated_files:
        # A ticket is not a traceback. It names a file and describes a symptom, and saying
        # "nothing resolved" for that is wrong twice over: something *was* established (the
        # file, by the ticket itself), and what is missing is only the line.
        named = ", ".join(f"`{f}`" for f in loc.stated_files[:3])
        them = "it" if len(loc.stated_files) == 1 else "them"
        hyps.append(
            Hypothesis(
                claim=f"The fault is in {named} — named by the ticket, not localized to a line. "
                "Read the file before assuming where.",
                evidence=(f"The text names {named}, and the graph knows {them}.",),
                confidence="medium",
            )
        )
    elif loc.fault is None:
        hyps.append(
            Hypothesis(
                claim="No trace frame resolved to a repo symbol — the fault may be in a dependency, "
                "or the failing file isn't in this repo's graph.",
                evidence=(f"Exception: {loc.exception}" if loc.exception else "No resolvable frames.",),
                confidence="low",
            )
        )
    return hyps


def _deterministic_fix_approach(loc: Localization) -> str:
    if loc.fault is None:
        return (
            "Reproduce the failure with a focused test, then localize the fault (it appears to be "
            "outside this repo's graph — check dependencies and the failing input)."
        )
    exc = f" the `{_exception_class(loc.exception)}`" if loc.exception else " the failure"
    return (
        f"Add a regression test that reproduces{exc} at `{loc.fault.func}` ({loc.fault.where}) first "
        "(red → green), then guard/handle the offending input at the fault site. Re-run the tests "
        "over the regression surface above before merging."
    )


async def _llm_enrich(report: RCAReport, llm: Any) -> RCAReport:
    """Optional: let an LLM synthesise richer hypotheses + a fix approach from the
    evidence already gathered. Falls back to the deterministic report on any error."""
    import json

    from orchestrator.core.llm.client import Message
    from orchestrator.sdlc.codegen import resolve_codegen_model

    evidence = {
        "problem": report.problem[:2000],
        "exception": report.exception,
        "fault_site": report.fault_site,
        "callers": report.callers[:10],
        "recently_changed": report.recently_changed,
        "regression_surface": report.regression_surface[:10],
        "deterministic_hypotheses": [h.claim for h in report.hypotheses],
    }
    prompt = (
        "You are debugging an issue in an existing codebase. Using ONLY the grounded evidence "
        "below (from a knowledge graph + git history), produce ranked root-cause HYPOTHESES — "
        'not assertions — and a concise fix approach. Respond as JSON: {"hypotheses": '
        '[{"claim": str, "evidence": [str], "confidence": "high|medium|low"}], '
        '"fix_approach": str}.\n\nEVIDENCE:\n' + json.dumps(evidence, indent=2)
    )
    result = await llm.complete(
        [
            Message(role="system", content="You are a senior engineer doing grounded root-cause analysis."),
            Message(role="user", content=prompt),
        ],
        model=resolve_codegen_model(),
        json_object=True,
        temperature=0.2,
    )
    data = json.loads(result.text)
    hyps = [
        Hypothesis(
            claim=str(h.get("claim", "")),
            evidence=tuple(str(e) for e in (h.get("evidence") or [])),
            confidence=str(h.get("confidence", "medium")),
        )
        for h in (data.get("hypotheses") or [])
        if h.get("claim")
    ]
    if hyps:
        report.hypotheses = hyps
    if data.get("fix_approach"):
        report.fix_approach = str(data["fix_approach"])
    report.llm = True
    return report


async def build_rca(
    problem: str,
    *,
    store: FactStore,
    root: Path | str | None = None,
    llm: Any = None,
) -> RCAReport:
    """Localize + ground a bug into an RCA report. Deterministic unless ``llm`` is given."""
    loc = localize_trace(problem, store=store)
    fault = loc.fault
    # A resolved frame first; failing that, a file the text itself named. The second is
    # weaker — a module, not a line — but it is what turns "nothing resolved" into a
    # regression surface and a recently-changed check for the majority of bug tickets,
    # which arrive as prose and not as tracebacks.
    fault_file = fault.where.split(":", 1)[0] if fault else (loc.stated_files[0] if loc.stated_files else "")
    # The same churn pass the enhancement profile now runs on its landing sites, from one
    # implementation — a bug and a feature asking "has this changed lately?" must not get two
    # different answers. Here it is crossed with the fault file, which is what makes it a
    # regression signal rather than a note about a busy area.
    recently_changed = bool(changed_recently([fault_file], root)) if fault_file else False

    from orchestrator.sdlc.excerpt import source_at

    excerpt = source_at(root, fault.where) if fault else None

    report = RCAReport(
        problem=problem.strip(),
        exception=loc.exception,
        fault_site=f"{fault.func} at {fault.where}" if fault else "",
        fault_source=excerpt.fenced() if excerpt is not None else "",
        fault_module=fault.module if fault else fault_file,
        callers=loc.callers,
        hypotheses=_deterministic_hypotheses(
            # The resolved module, not the frame's: with only a stated file the report still
            # says "changed recently", and a banner whose hypothesis is missing from the
            # ranked list below it reads as a bug in the report.
            loc,
            recently_changed=recently_changed,
            fault_module=fault.module if fault else fault_file,
        ),
        regression_surface=_regression_surface(store, fault_file) if fault_file else [],
        recently_changed=recently_changed,
        fix_approach=_deterministic_fix_approach(loc),
        grounded=store.summary().get("grounded_nodes", 0) > 0,
    )
    if llm is not None:
        try:
            report = await _llm_enrich(report, llm)
        except Exception as exc:  # noqa: BLE001 — LLM/parse failure → keep the deterministic report
            logger.warning("sdlc.rca.llm_enrich_failed", extra={"error": str(exc)[:200]})
    return report


def _not_verified(report: RCAReport) -> str:
    """What this analysis did not establish. Conditional on state, never boilerplate.

    An RCA is the document most likely to be read as a conclusion, and its header's promise
    — "ranked by evidence, not asserted" — is easy to skim past. Naming the specific thing
    that was not done is harder to skim than a disclaimer.
    """
    notes: list[str] = []
    if report.hypotheses:
        notes.append(
            "- The hypotheses were **ranked from static evidence, not reproduced**. None has "
            "been executed against the failure."
        )
    if not report.fault_site:
        notes.append(
            "- The fault was not localized to a symbol, so everything below rests on the failure text alone."
        )
    if len(report.regression_surface) > _MAX_SURFACE:
        notes.append(
            f"- The regression surface lists {_MAX_SURFACE} of {len(report.regression_surface)} "
            "— the remainder are equally affected, not less so."
        )
    if len(report.callers) > _MAX_CALLERS:
        notes.append(
            f"- {len(report.callers) - _MAX_CALLERS} further caller(s) were not listed as trigger paths."
        )
    if report.llm:
        notes.append(
            "- The fix approach was written by a model from the evidence above; it is a "
            "suggestion, and nothing verified it."
        )
    return "\n".join(notes)


def render_rca_md(report: RCAReport) -> str:
    """Render the report as markdown, against the shared section vocabulary.

    Titles and order come from :mod:`orchestrator.sdlc.brief`. The tier is EVIDENCE: an RCA
    ranks hypotheses *by evidence* and must not assert a conclusion, which is the same
    discipline the header has always claimed ("ranked by evidence, not asserted") and which
    is now enforced rather than described.
    """
    origin = "LLM-enriched" if report.llm else "deterministic (no LLM)"
    # D7: the document's own type follows its content. Enrichment overwrites `fix_approach`
    # with the model's text, so an enriched report is not an Evidence-tier document and must
    # not present itself as one. The deterministic path is unchanged and stays EVIDENCE.
    doc = Brief("Root-cause analysis", tier=Tier.JUDGEMENT if report.llm else Tier.EVIDENCE)

    preamble = [f"_{origin}; hypotheses ranked by evidence, not asserted._"]
    if report.exception:
        preamble.append(f"\n**Exception:** `{report.exception}`")
    doc.add(brief.PROBLEM, "\n".join(preamble))

    if report.fault_site:
        site = [report.fault_site + (f" (in {report.fault_module})" if report.fault_module else "")]
        if report.recently_changed:
            site.append("\n⚠ This module changed recently — treat a regression as the leading hypothesis.")
        if report.fault_source:
            site.append("\n" + report.fault_source)
        if report.callers:
            site.append("\n_Called by (potential trigger paths):_")
            site.extend(f"- {c}" for c in report.callers[:_MAX_CALLERS])
        doc.add(brief.FAULT_SITE, "\n".join(site))
    else:
        doc.add(brief.FAULT_SITE)

    if report.hypotheses:
        rows: list[str] = []
        for i, h in enumerate(report.hypotheses, 1):
            rows.append(f"{i}. **[{h.confidence}]** {h.claim}")
            rows.extend(f"   - {e}" for e in h.evidence)
        doc.add(brief.HYPOTHESES, "\n".join(rows))
    else:
        doc.add(brief.HYPOTHESES)

    if report.regression_surface:
        surface = ["_A fix must not break these (the fault module's dependents + hotspots):_\n"]
        surface.extend(f"- {s}" for s in report.regression_surface[:_MAX_SURFACE])
        doc.add(brief.REGRESSION_SURFACE, "\n".join(surface))
    else:
        doc.add(brief.REGRESSION_SURFACE)

    doc.add(brief.NOT_VERIFIED, _not_verified(report))
    # Labelled per rendering, because this one section's provenance changes with the run:
    # `_deterministic_fix_approach` computed it, or the model replaced it.
    doc.add(
        brief.FIX_APPROACH,
        report.fix_approach,
        label=brief.MODEL if report.llm else brief.DETERMINISTIC,
    )
    doc.add(
        brief.NEXT_STEP,
        "Review + approve, then `orchestrator design` the fix and implement it with a regression "
        "test that reproduces the failure first (red → green). This report stops at analysis — "
        "no code is changed.",
    )
    return doc.render()


__all__ = ["Hypothesis", "RCAReport", "build_rca", "render_rca_md"]
