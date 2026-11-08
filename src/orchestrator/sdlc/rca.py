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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg import FactStore
from orchestrator.sdlc import brief
from orchestrator.sdlc.brief import Brief, Tier
from orchestrator.sdlc.churn import DEFAULT_COMMITS, changed_recently
from orchestrator.sdlc.localize import Localization, localize_trace
from orchestrator.sdlc.seed_signals import DEFAULT_HOP_ORIGINS, HopHit, QuoteHit, one_hop, quoted_text_hits

if TYPE_CHECKING:
    from orchestrator.pkg.hybrid import HybridRetriever

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
    #: With no traceback frame resolved and hybrid search on for ``rca`` (D12): the files hybrid search
    #: ranks for
    #: the report's prose, seeded by its exception class and stated files — **ranked, not traced**. Empty
    #: otherwise.
    ranked_files: list[str] = field(default_factory=list)
    #: Each ranked file's evidence ("content (#1), graph via Parser.parse → CALLS"), same order.
    ranked_by: list[str] = field(default_factory=list)
    #: The embedding model the ranking's meaning leg used (D17), or empty when none ran.
    ranked_model: str = ""
    #: Where ``fault_module`` came from: ``traced`` (a traceback frame resolved), ``ticket`` (the
    #: reporter's text names it), ``spec`` (only the intake spec names it — a model wrote that),
    #: ``unchecked`` (the spec names it and no ticket text was kept to check it against — a
    #: hand-written ``--spec``), ``ranked`` (search ranked it first), or "" when there is none. A
    #: reader weighs "the reporter said so" and "a model inferred it" differently; the report must
    #: not blur them (B161).
    fault_origin: str = ""


def _exception_class(exception: str) -> str:
    return exception.split(":", 1)[0].split(".")[-1].strip() if exception else ""


#: How many ranked candidate files an RCA lists when no frame resolved.
_MAX_RANKED_FILES = 10


#: How deep RCA reads the fused ranking (D11, SSPN-390). Measured: at 50 hits, 4 of 9 fix files'
#: first hits were out of reach on two graded tickets, and on SSPN-73 the 50 hits held only 4 files.
#: Still only :data:`_MAX_RANKED_FILES` files are listed.
RCA_HIT_LIMIT = 200
#: One-hop origins (D6); see ``seed_signals.DEFAULT_HOP_ORIGINS``.
_HOP_ORIGINS = DEFAULT_HOP_ORIGINS


@dataclass(frozen=True)
class RankedSearch:
    """RCA's ranked files plus two signals read off the same search (``seed_signals``).

    The signals are reported, not fed back into the ranking: a second pass seeded with them left the
    dev set's top 10 at 15 of 22 fix files under every setting tried (SSPN-390), so it cost a search
    for nothing measurable. They are inputs for hypotheses, each with its own evidence.
    """

    files: list[str]
    evidence: list[str]
    model: str
    quotes: tuple[QuoteHit, ...] = ()
    hops: tuple[HopHit, ...] = ()


def _ranked_candidates(
    problem: str, loc: Localization, store: FactStore, root: Path | str | None, hybrid: HybridRetriever | None
) -> tuple[list[str], list[str], str]:
    """Hybrid search's files for a prose bug report (D12): files, the evidence for each, the model.

    The three-value shape the localization harness reads; :func:`_ranked_with_signals` does the work.
    """
    ranked = _ranked_with_signals(problem, loc, store, root, hybrid)
    return ranked.files, ranked.evidence, ranked.model


def _ranked_with_signals(
    problem: str, loc: Localization, store: FactStore, root: Path | str | None, hybrid: HybridRetriever | None
) -> RankedSearch:
    """Hybrid search's files for a prose bug report, when the ``rca`` stage has it on; else nothing.

    Seeded, beyond the words, by what the report itself names: the exception class and the files it
    states. Read to :data:`RCA_HIT_LIMIT` hits. Alongside the ranking, two signals search cannot
    surface on its own (SSPN-390): the symbols holding the report's quoted text, and one CALLS hop
    from the first hits in the files it names.
    """
    from orchestrator.pkg.hybrid import HybridRetriever, hybrid_enabled

    if hybrid is None and not hybrid_enabled("rca"):
        return RankedSearch([], [], "")
    cls = _exception_class(loc.exception)
    stated = set(loc.stated_files)
    named = sorted(
        n.id
        for n in store.nodes
        if n.grounded
        and ((cls and n.name == cls) or (n.provenance is not None and n.provenance.file in stated))
    )
    if hybrid is None:
        from orchestrator.pkg.embed import default_embedder

        hybrid = HybridRetriever(store, root, embedder=default_embedder())
    hits = hybrid.search(problem, extra_seeds=named, limit=RCA_HIT_LIMIT)
    hops = one_hop(store, [h.node.id for h in hits], stated, k=_HOP_ORIGINS)
    quotes = quoted_text_hits(problem, store, root) if root is not None else []
    files: list[str] = []
    evidence: list[str] = []
    for hit in hits:
        file = hit.node.provenance.file if hit.node.provenance else ""
        if file and file not in files:
            files.append(file)
            evidence.append(hit.found_by)
        if len(files) >= _MAX_RANKED_FILES:
            break
    return RankedSearch(files, evidence, hybrid.model or "", tuple(quotes), tuple(hops))


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


#: A path as prose writes one, with any extension — not only the suffixes codegen resolves. Reading
#: the ticket through ``source_paths.named_paths`` knew no ``.js``, so a file the ticket named was
#: called the model's (B161 review).
_ANY_PATH_RE = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z][A-Za-z0-9]{0,4})(?![\w/])")

#: How each origin is said, wherever a named file is shown (§3's fault line and hypothesis alike).
ORIGIN_WORDS: dict[str, str] = {
    "ticket": "named by the ticket",
    "spec": "named by the intake spec (a model wrote it), not by the ticket",
    "unchecked": "named by the spec — no ticket text was kept to check it against",
    "ranked": "ranked by search, not named or traced",
}


def _named_in(path: str, text: str) -> bool:
    """Does ``text`` name ``path`` — in full, or either one a tail of the other on a directory boundary?

    Both directions: a ticket writes ``pkg/auth.py`` for the graph's ``src/pkg/auth.py``, and on a
    sub-root plan ``svc/pkg/auth.py`` for the graph's ``pkg/auth.py``. ``mypkg/`` is never ``pkg/``.
    """
    for raw in _ANY_PATH_RE.findall(text.replace("\\", "/")):
        named = raw[2:] if raw.startswith("./") else raw
        if named == path or path.endswith("/" + named) or named.endswith("/" + path):
            return True
    return False


def _origin_of(path: str, source_text: str | None) -> str:
    """``ticket``, ``spec`` or ``unchecked`` for a file the read text names (see ``fault_origin``)."""
    if source_text is None:
        return "ticket"  # the text read *is* the reporter's — the `rca` command, MCP `root_cause`
    if not source_text.strip():
        return "unchecked"
    return "ticket" if _named_in(path, source_text) else "spec"


def _deterministic_hypotheses(
    loc: Localization,
    *,
    recently_changed: bool,
    fault_module: str,
    source_text: str | None = None,
) -> list[Hypothesis]:
    hyps: list[Hypothesis] = []
    exc_class = _exception_class(loc.exception)

    if recently_changed and fault_module and loc.fault is not None:
        hyps.append(
            Hypothesis(
                claim=f"Recent change to `{fault_module}` — likely a regression; review its latest commits.",
                evidence=(f"`{fault_module}` was modified in the recent git history.",),
                confidence="high",
            )
        )
    elif recently_changed and fault_module:
        # No frame resolved: the file is named or ranked, not traced. A file every release touches
        # is "recently changed" whatever the bug is, so this is a place to look, not a leading
        # hypothesis — SSPN-264's design limitation was ranked a [high] regression on it (B161).
        hyps.append(
            Hypothesis(
                claim=f"`{fault_module}` changed in the last {DEFAULT_COMMITS} commits — check whether "
                "one of them introduced this. On its own that is not evidence of a regression.",
                evidence=(f"`{fault_module}` was modified in the recent git history.",),
                confidence="low",
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
        shown = loc.stated_files[:3]
        named = ", ".join(f"`{f}`" for f in shown)
        them = "it" if len(loc.stated_files) == 1 else "them"
        # In a plan the text read here is the intake spec, which a model wrote; the ticket is
        # ``source_text``. A path only the spec names is the model's inference (B161). Each file
        # carries its own origin — one label for all three let this line call `a.py` the ticket's
        # while the fault line above it called `a.py` the spec's (B161 review).
        groups: dict[str, list[str]] = {}
        for f in shown:
            groups.setdefault(_origin_of(f, source_text), []).append(f)
        where = "; ".join(
            f"{', '.join(f'`{f}`' for f in files)} — {ORIGIN_WORDS[origin]}"
            for origin, files in groups.items()
        )
        hyps.append(
            Hypothesis(
                claim=f"The fault is in {where}, not localized to a line. "
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
    # Ranked means ranked: the [low] recency note was appended first and printed as hypothesis 1
    # above a [medium] one (B161 review). A stable sort keeps the order within each band.
    hyps.sort(key=lambda h: _CONFIDENCE_RANK.get(h.confidence, len(_CONFIDENCE_RANK)))
    return hyps


_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


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
    hybrid: HybridRetriever | None = None,
    source_text: str | None = None,
) -> RCAReport:
    """Localize + ground a bug into an RCA report. Deterministic unless ``llm`` is given.

    ``source_text`` is the ticket as its reporter wrote it, when ``problem`` is something else — the
    intake spec a plan reads. It decides only whether a named file is called the ticket's or the
    spec's; ``None`` means ``problem`` is the reporter's own text, as in the ``rca`` command, and an
    empty string means no ticket text was kept, so neither can be claimed (``unchecked``).
    """
    loc = localize_trace(problem, store=store)
    fault = loc.fault
    # A resolved frame first; failing that, a file the text itself named. The second is
    # weaker — a module, not a line — but it is what turns "nothing resolved" into a
    # regression surface and a recently-changed check for the majority of bug tickets,
    # which arrive as prose and not as tracebacks.
    fault_file = fault.where.split(":", 1)[0] if fault else (loc.stated_files[0] if loc.stated_files else "")
    ranked_files, ranked_by, ranked_model = (
        ([], [], "") if fault else _ranked_candidates(problem, loc, store, root, hybrid)
    )
    # Nothing traced and nothing named: the top ranked file stands in, labelled as ranked wherever it is
    # shown.
    if fault:
        origin = "traced"
    elif loc.stated_files:
        origin = _origin_of(loc.stated_files[0], source_text)
    else:
        origin = "ranked" if ranked_files else ""
    fault_file = fault_file or (ranked_files[0] if ranked_files else "")
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
            source_text=source_text,
        ),
        regression_surface=_regression_surface(store, fault_file) if fault_file else [],
        recently_changed=recently_changed,
        fix_approach=_deterministic_fix_approach(loc),
        grounded=store.summary().get("grounded_nodes", 0) > 0,
        ranked_files=ranked_files,
        ranked_by=ranked_by,
        ranked_model=ranked_model,
        fault_origin=origin,
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
    elif report.ranked_files:
        model = f" with the embedding model `{report.ranked_model}`" if report.ranked_model else ""
        ranked = [
            f"_No traceback frame resolved. Ranked by hybrid search{model}, **not traced** — places to look "
            "first:_"
        ]
        ranked.extend(
            f"- `{f}` — {why}" for f, why in zip(report.ranked_files, report.ranked_by, strict=True)
        )
        doc.add(brief.FAULT_SITE, "\n".join(ranked))
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
