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
    #: high | medium | low — or ``stated``: the ticket's own words, quoted, ranked above every band (D12).
    confidence: str = "medium"
    #: The ticket's text, verbatim, for a ``stated`` hypothesis; "" otherwise.
    quote: str = ""


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
    #: not blur them (B161). Two more: ``stated`` (the ticket's own Root cause / Cause / Analysis /
    #: Fix section names it, D12) and ``linked:<page title>`` (only a page `--follow-links` read names
    #: it, B164).
    fault_origin: str = ""


def _exception_class(exception: str) -> str:
    return exception.split(":", 1)[0].split(".")[-1].strip() if exception else ""


#: How many ranked candidate files an RCA lists when no frame resolved.
_MAX_RANKED_FILES = 10
#: How deep RCA reads the fused ranking (sdlc-search-use D11, SSPN-390). At 50 hits, 4 of 9 fix files'
#: first hits were out of reach on two graded tickets, and on SSPN-73 the 50 hits held only 4 files. On
#: the 8-ticket dev set the top 10 kept the same 15 of 22 fix files. Still only
#: :data:`_MAX_RANKED_FILES` files are listed.
RCA_HIT_LIMIT = 200


def _ranked_candidates(
    problem: str, loc: Localization, store: FactStore, root: Path | str | None, hybrid: HybridRetriever | None
) -> tuple[list[str], list[str], str]:
    """Hybrid search's files for a prose bug report (D12), when the ``rca`` stage has it on; else nothing.

    Seeded, beyond the words, by what the report itself names: the exception class (``KeyError`` names
    nothing in
    the repo, ``ParserError`` may) and the files it states. Distinct files in rank order, each with its
    evidence.
    """
    from orchestrator.pkg.hybrid import HybridRetriever, hybrid_enabled

    if hybrid is None and not hybrid_enabled("rca"):
        return [], [], ""
    cls = _exception_class(loc.exception)
    stated = set(loc.stated_files)
    seeds = sorted(
        n.id
        for n in store.nodes
        if n.grounded
        and ((cls and n.name == cls) or (n.provenance is not None and n.provenance.file in stated))
    )
    files: list[str] = []
    evidence: list[str] = []
    if hybrid is None:
        from orchestrator.pkg.embed import default_embedder

        hybrid = HybridRetriever(store, root, embedder=default_embedder())
    for hit in hybrid.search(problem, extra_seeds=seeds, limit=RCA_HIT_LIMIT):
        file = hit.node.provenance.file if hit.node.provenance else ""
        if file and file not in files:
            files.append(file)
            evidence.append(hit.found_by)
        if len(files) >= _MAX_RANKED_FILES:
            break
    return files, evidence, hybrid.model or ""


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
    "stated": "named in the ticket's own root-cause section",
}

#: ``linked:<title>`` — a path only a linked page names (B164). The title varies, so it is not a key.
_LINKED = "linked:"


def origin_words(origin: str) -> str:
    """How ``origin`` is said; an unknown origin reads as the ticket's, as it always did."""
    if origin.startswith(_LINKED):
        return f"named on a linked page ({origin[len(_LINKED) :]})"
    return ORIGIN_WORDS.get(origin, ORIGIN_WORDS["ticket"])


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
    """``ticket``, ``linked:<title>``, ``spec`` or ``unchecked`` for a file the read text names.

    With `--follow-links` the kept text holds the pages the ticket links to after its own words. A
    path only such a page names is the page author's, which is weaker than the reporter stating it,
    and is said so (B164). The ticket's own mention wins.
    """
    from orchestrator.intake.follow_links import split_linked

    if source_text is None:
        return "ticket"  # the text read *is* the reporter's — the `rca` command, MCP `root_cause`
    if not source_text.strip():
        return "unchecked"
    own, pages = split_linked(source_text)
    if _named_in(path, own):
        return "ticket"
    for title, text in pages:
        if _named_in(path, text):
            return f"{_LINKED}{title}"
    return "spec"


# ---- the ticket's own statement of the cause (D12, B174) ---------------------------------------------

#: Section headings whose body is the reporter's own account of the cause or the fix. Matched whole,
#: after case-folding and dropping a trailing ``:`` or ``.``: "Fix direction (not decided)" is not one.
_CAUSE_HEADINGS = frozenset({"root cause", "root-cause", "cause", "analysis", "fix", "proposed fix"})
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_WIKI_HEADING_RE = re.compile(r"^\s*h[1-6]\.\s+(.+?)\s*$")
#: ``**Root cause:** text`` / ``*Root cause.* text`` — a bold or italic lead-in, its text on the same line.
_LEAD_IN_RE = re.compile(r"^\s*(\*\*|\*|__|_)([^*_\n]+?)\1\s*[:.]?\s*(.*)$")
#: How much of one stated section is carried, and how many sections. Bounded and said so (invariant 7).
_MAX_STATED_CHARS = 1500
_MAX_STATED_SECTIONS = 3


def _heading_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(":.").strip()).lower()


def stated_causes(text: str) -> list[tuple[str, str]]:
    """``[(heading, body verbatim), …]`` for each Root cause / Cause / Analysis / Fix / Proposed fix section.

    Deterministic: headings only (markdown at any level, Jira's ``h2.``, or a bold/italic lead-in), in
    the order the ticket writes them. A heading section runs to the next heading; a lead-in to the next
    blank line. A body is cut at :data:`_MAX_STATED_CHARS` with the cut stated.
    """
    lines = text.splitlines()
    found: list[tuple[str, str]] = []
    i = 0
    while i < len(lines) and len(found) < _MAX_STATED_SECTIONS:
        line = lines[i]
        heading = _MD_HEADING_RE.match(line) or _WIKI_HEADING_RE.match(line)
        lead = None if heading else _LEAD_IN_RE.match(line)
        title = heading.group(1) if heading else lead.group(2) if lead else ""
        if not title or _heading_key(title) not in _CAUSE_HEADINGS:
            i += 1
            continue
        body: list[str] = [lead.group(3)] if lead and lead.group(3) else []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if _MD_HEADING_RE.match(nxt) or _WIKI_HEADING_RE.match(nxt):
                break
            if lead is not None and (not nxt.strip() or _LEAD_IN_RE.match(nxt)):
                break
            body.append(nxt)
            j += 1
        verbatim = "\n".join(body).strip("\n").rstrip()
        if verbatim.strip():
            if len(verbatim) > _MAX_STATED_CHARS:
                verbatim = (
                    f"{verbatim[:_MAX_STATED_CHARS].rstrip()} … (cut at {_MAX_STATED_CHARS:,} of "
                    f"{len(verbatim):,} characters — the rest is in the ticket)"
                )
            found.append((title.strip().rstrip(":.").strip(), verbatim))
        i = j
    return found


_BACKTICKED_RE = re.compile(r"`([A-Za-z_$][\w$]*)(?:\(\))?`")


def _stated_fault_file(sections: list[tuple[str, str]], store: FactStore) -> str:
    """The first graph file the stated sections name: a path, else an identifier the graph holds once."""
    files = sorted({n.provenance.file for n in store.nodes if n.grounded and n.provenance is not None})
    for _, body in sections:
        for raw in _ANY_PATH_RE.findall(body.replace("\\", "/")):
            named = raw[2:] if raw.startswith("./") else raw
            for f in files:
                if named == f or f.endswith("/" + named) or named.endswith("/" + f):
                    return f
    for _, body in sections:
        for name in _BACKTICKED_RE.findall(body):
            holders = sorted(
                {
                    n.provenance.file
                    for n in store.nodes
                    if n.grounded and n.provenance is not None and n.name == name and n.kind.value != "Module"
                }
            )
            if len(holders) == 1:
                return holders[0]
    return ""


def _stated_hypotheses(sections: list[tuple[str, str]]) -> list[Hypothesis]:
    return [
        Hypothesis(
            claim=f"The ticket's own “{heading}” section, verbatim — the reporter's statement, not checked "
            "against the code:",
            confidence="stated",
            quote=body,
        )
        for heading, body in sections
    ]


def _deterministic_hypotheses(
    loc: Localization,
    *,
    recently_changed: bool,
    fault_module: str,
    source_text: str | None = None,
    stated_file: str = "",
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
            f"{', '.join(f'`{f}`' for f in files)} — {origin_words(origin)}"
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
    elif loc.fault is None and not stated_file:
        # Not when the ticket's own root-cause section names a graph file: "isn't in this repo's graph"
        # would contradict the fault line above it (D12).
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


def _deterministic_fix_approach(
    loc: Localization, ranked_files: list[str] | None = None, stated: str = ""
) -> str:
    if loc.fault is None:
        # No traceback frame is not "outside the graph": a ticket in prose names files, or search
        # ranks them, and that is where to start (B168). Outside the graph only when nothing in the
        # repository matched at all.
        named = [stated] if stated else list(loc.stated_files[:3])
        if named:
            return (
                "Reproduce the failure with a focused test, then localize the fault: no traceback frame "
                f"resolved, so start in {', '.join(f'`{f}`' for f in named)}, which the text names, and "
                "find the line before changing anything."
            )
        if ranked_files:
            top = ", ".join(f"`{f}`" for f in ranked_files[:3])
            return (
                "Reproduce the failure with a focused test, then localize the fault: no traceback frame "
                f"resolved, so start with the files search ranked — {top} (ranked, not traced)."
            )
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

    traced = report.fault_origin == "traced"
    evidence: dict[str, Any] = {
        "problem": report.problem[:2000],
        "exception": report.exception,
        "fault_site": report.fault_site,
        # Where the fault module came from (B163). "Changed recently" is a regression signal only for a
        # traced fault; handed over without the origin, a model ranked it the leading hypothesis again.
        "fault_origin": report.fault_origin,
        "callers": report.callers[:10],
        **({"recently_changed": report.recently_changed} if traced else {}),
        "regression_surface": report.regression_surface[:10],
        "deterministic_hypotheses": [h.claim for h in report.hypotheses if h.confidence != "stated"],
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
            confidence=_capped(str(h.get("confidence", "medium")), str(h.get("claim", "")), traced=traced),
        )
        for h in (data.get("hypotheses") or [])
        if h.get("claim")
    ]
    if hyps:
        # The ticket's own statement stays first: a model's ranking does not replace the reporter (D12).
        report.hypotheses = [h for h in report.hypotheses if h.confidence == "stated"] + hyps
    if data.get("fix_approach"):
        report.fix_approach = str(data["fix_approach"])
    report.llm = True
    return report


#: A claim about recent change. Matched only to *cap* it, never to raise anything.
_RECENCY_RE = re.compile(
    r"\b(regress\w*|recent(ly)?|latest commits?|newly introduced|introduced by)\b", re.IGNORECASE
)


def _capped(confidence: str, claim: str, *, traced: bool) -> str:
    """A model's band, except that a recency claim on an untraced fault is at most ``low`` (B163, B161)."""
    if confidence == "stated":
        return "medium"  # only the ticket's own words are "stated"; a model cannot claim it
    if not traced and confidence == "high" and _RECENCY_RE.search(claim):
        return "low"
    return confidence


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
    # D12: the ticket's own Root cause / Cause / Analysis / Fix sections, read from its own words only —
    # a linked page's headings are its author's, not the reporter's.
    from orchestrator.intake.follow_links import split_linked

    sections = stated_causes(split_linked(problem if source_text is None else source_text)[0])
    stated_file = "" if fault else (_stated_fault_file(sections, store) if sections else "")
    # A resolved frame first; failing that, a file the text itself named. The second is
    # weaker — a module, not a line — but it is what turns "nothing resolved" into a
    # regression surface and a recently-changed check for the majority of bug tickets,
    # which arrive as prose and not as tracebacks.
    fault_file = fault.where.split(":", 1)[0] if fault else (loc.stated_files[0] if loc.stated_files else "")
    fault_file = stated_file or fault_file
    ranked_files, ranked_by, ranked_model = (
        ([], [], "") if fault else _ranked_candidates(problem, loc, store, root, hybrid)
    )
    # Nothing traced and nothing named: the top ranked file stands in, labelled as ranked wherever it is
    # shown.
    if fault:
        origin = "traced"
    elif stated_file:
        origin = "stated"
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
        hypotheses=_stated_hypotheses(sections)
        + _deterministic_hypotheses(
            # The resolved module, not the frame's: with only a stated file the report still
            # says "changed recently", and a banner whose hypothesis is missing from the
            # ranked list below it reads as a bug in the report.
            loc,
            recently_changed=recently_changed,
            fault_module=fault.module if fault else fault_file,
            source_text=source_text,
            stated_file=stated_file,
        ),
        regression_surface=_regression_surface(store, fault_file) if fault_file else [],
        recently_changed=recently_changed,
        fix_approach=_deterministic_fix_approach(loc, ranked_files, stated_file),
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
            rows.extend(f"   > {q}" if q else "   >" for q in h.quote.splitlines())
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


__all__ = ["Hypothesis", "RCAReport", "build_rca", "origin_words", "render_rca_md", "stated_causes"]
