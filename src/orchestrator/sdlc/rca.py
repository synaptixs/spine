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
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.sdlc.localize import Localization, localize_trace

logger = logging.getLogger("orchestrator.sdlc.rca")

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
    callers: list[str] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    regression_surface: list[str] = field(default_factory=list)
    recently_changed: bool = False
    fix_approach: str = ""
    grounded: bool = False
    llm: bool = False


def _recently_changed_files(root: Path | str | None, *, commits: int = 40) -> set[str]:
    """Repo-relative files touched in the last ``commits`` commits (best-effort)."""
    if root is None:
        return set()
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--name-only", "--pretty=format:", "-n", str(commits)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


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
    if loc.fault is None:
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
        "over the regression surface below before merging."
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
    fault_file = fault.where.split(":", 1)[0] if fault else ""
    changed = _recently_changed_files(root)
    recently_changed = bool(
        fault_file and any(c == fault_file or c.endswith("/" + fault_file) for c in changed)
    )

    report = RCAReport(
        problem=problem.strip(),
        exception=loc.exception,
        fault_site=f"{fault.func} at {fault.where}" if fault else "",
        fault_module=fault.module if fault else "",
        callers=loc.callers,
        hypotheses=_deterministic_hypotheses(
            loc, recently_changed=recently_changed, fault_module=fault.module if fault else ""
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


def render_rca_md(report: RCAReport) -> str:
    origin = "LLM-enriched" if report.llm else "deterministic (no LLM)"
    out: list[str] = [f"# Root-cause analysis\n\n_{origin}; hypotheses ranked by evidence, not asserted._\n"]
    if report.exception:
        out.append(f"**Exception:** `{report.exception}`\n")

    out.append("## Fault site")
    if report.fault_site:
        line = report.fault_site + (f" (in {report.fault_module})" if report.fault_module else "")
        out.append(line)
        if report.recently_changed:
            out.append("\n⚠ This module changed recently — treat a regression as the leading hypothesis.")
        if report.callers:
            out.append("\n_Called by (potential trigger paths):_")
            out.extend(f"- {c}" for c in report.callers[:10])
    else:
        out.append("_Not localized to a repo symbol — see the low-confidence hypothesis below._")
    out.append("")

    out.append("## Root-cause hypotheses")
    if report.hypotheses:
        for i, h in enumerate(report.hypotheses, 1):
            out.append(f"{i}. **[{h.confidence}]** {h.claim}")
            out.extend(f"   - {e}" for e in h.evidence)
    else:
        out.append("_No hypotheses could be grounded — gather more of the failure output._")
    out.append("")

    out.append("## Regression surface")
    if report.regression_surface:
        out.append("_A fix must not break these (the fault module's dependents + hotspots):_\n")
        out.extend(f"- {s}" for s in report.regression_surface[:15])
    else:
        out.append("_None identified (no in-repo dependents, or the fault didn't localize)._")
    out.append("")

    out.append("## Suggested fix approach")
    out.append(report.fix_approach)
    out.append("")

    out.append("## Next step")
    out.append(
        "Review + approve, then `orchestrator design` the fix and implement it with a regression "
        "test that reproduces the failure first (red → green). This report stops at analysis — "
        "no code is changed."
    )
    return "\n".join(out) + "\n"


__all__ = ["Hypothesis", "RCAReport", "build_rca", "render_rca_md"]
