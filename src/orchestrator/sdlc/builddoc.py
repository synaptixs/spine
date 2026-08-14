"""The build document — one ticket's plan, assembled before any code exists.

A run today spends real money before anyone sees anything, and its first output is
either a PR or a traceback. This module produces the thing that should come first: a
reviewable document assembled from the sources of truth, cheap enough to throw away.

**Twelve sections, fixed titles, fixed order** — see `docs/specs/build-document.md` §3.
The shape is the contract: a reviewer must be able to find section 9 without reading
sections 1–8, and a renderer can only be built against a stable shape.

**Every section says where it came from.** The four labels (§1) are the load-bearing
idea, and a document that mixes a quoted requirement with model inference without
saying which is which is worse than no document — it lends the authority of the first
to the second. A section that mixes takes the weaker label.

**Deterministic.** With a supplied spec there is no LLM anywhere in this path:
``produce_design(llm=None)`` is the heuristic design, and everything else reads the
graph, the tree, or git. Same commit and same spec in, byte-identical document out —
which is what makes the persisted history worth keeping.

Sections 3, 9, 11 and 12 are later phases. They render as headings that say what
would establish them rather than vanishing, because a missing section a reader cannot
see reads as a section with nothing to say.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from orchestrator.pkg.accuracy import measured_recall

# The four provenance labels of docs/specs/build-document.md §1.
STATED = "stated"
DETERMINISTIC = "derived · deterministic"
MODEL = "derived · model"
HUMAN = "human"

# Bounds. Every aggregation caps its output and says what it elided (invariant 7):
# a clipped diagram that implies completeness is worse than a small honest one.
_MAX_IMPORTERS = 6
_MAX_HOTSPOTS = 5
_MAX_MODULES = 6

_ID_UNSAFE = re.compile(r"[^0-9A-Za-z_]")
_DERIVED_AT = re.compile(r"\*\*Derived at:\*\* `([^`]+)`")

# Substituted after the body exists, because the status depends on a digest of the body.
_STATUS_PLACEHOLDER = "\x00status\x00"  # noqa: S105 — a render placeholder, not a secret
# Everything after this is what a reviewer read — see :func:`plan_digest`.
_BODY_SEP = "\n---\n"
# …and everything after *this* is what happened afterwards, which nobody approved and which
# must not invalidate the approval. The digest stops here.
_JOURNEY_MARKER = "\n## Journey\n"


# ---- provenance ------------------------------------------------------------


def _label(label: str, source: str) -> str:
    """The italic line that sits directly beneath a section heading.

    Trailing newline on purpose: without the blank line markdown folds the label into
    the first paragraph of the section, and a provenance label that reads as part of
    the content is the opposite of what it is for.
    """
    return f"*{label} — {source}*\n"


def _pending(what: str) -> str:
    """A section that has not been established, saying so rather than vanishing."""
    return _label("not established", what)


def derived_at(root: Path | str = ".") -> str:
    """The commit every deterministic section was computed from.

    Not decoration. A plan approved at X and built at Y is a document that *was* true,
    and without the stamp nothing downstream can tell. A dirty tree is marked, because
    a document derived from uncommitted work cannot be reproduced from the commit alone.
    """
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if rev.returncode != 0:
            return "unknown"
        commit = rev.stdout.strip() or "unknown"
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return f"{commit}-dirty" if dirty.returncode == 0 and dirty.stdout.strip() else commit
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ---- approval --------------------------------------------------------------


@dataclass(frozen=True)
class PlanApproval:
    """One human decision about one plan, durable and beside it.

    ``escalate.Approval`` is the run-scoped equivalent and the wrong shape here: it is
    keyed by run, and it lives under the temp dir because a parked run does not outlive
    the work. An approved plan is evidence about a *ticket* and has to outlive every run
    of it. The vocabulary is deliberately the same so the two read alike.
    """

    intent_id: str
    decision: str  # APPROVED | REJECTED
    decided_by: str
    decided_at: str  # ISO date — a decision without a date is a rumour
    digest: str  # of the document body that was read
    commit: str  # what it was derived at
    note: str = ""


def plan_digest(document: str) -> str:
    """A fingerprint of what a reviewer actually read — the twelve sections, and nothing else.

    Bounded at both ends. The header is excluded because approving rewrites the status, and
    hashing it would invalidate an approval at the instant it was granted. The journey is
    excluded because runs append to it: a digest that covered it would refuse the very next
    run after the one it permitted.
    """
    _, sep, body = document.partition(_BODY_SEP)
    # rstrip because appending a journey adds blank lines *before* the marker, and a digest
    # that changed on trailing whitespace would refuse a document nothing had happened to.
    reviewed = (body if sep else document).partition(_JOURNEY_MARKER)[0].rstrip()
    return hashlib.sha256(reviewed.encode("utf-8")).hexdigest()[:16]


def approval_path(intent_id: str, *, root: Path | str = ".", out: Path | str | None = None) -> Path:
    return (Path(out) if out else plan_dir(root)) / f"{intent_id}-approval.json"


def load_approval(
    intent_id: str, *, root: Path | str = ".", out: Path | str | None = None
) -> PlanApproval | None:
    path = approval_path(intent_id, root=root, out=out)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return PlanApproval(**payload)
    except (OSError, ValueError, TypeError):
        # A corrupt approval is not an approval. Refusing to read it fails closed, which
        # is the only safe direction for a gate.
        return None


def save_approval(approval: PlanApproval, *, root: Path | str = ".", out: Path | str | None = None) -> Path:
    path = approval_path(approval.intent_id, root=root, out=out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(approval), indent=2) + "\n", encoding="utf-8")
    return path


def decided_by_default(root: Path | str = ".") -> str:
    """Who is approving, from git. An approval that is tedious to attribute says "me"."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _status_line(approval: PlanApproval | None, digest: str) -> str:
    if approval is None:
        return "proposed"
    who = approval.decided_by or "someone"
    when = approval.decided_at
    note = f" — {approval.note}" if approval.note else ""
    if approval.decision == "REJECTED":
        return f"**rejected** by {who} on {when}{note} *(human)*"
    if approval.digest != digest:
        return (
            f"**stale** — approved by {who} on {when}, but the plan has changed since. "
            "Re-read it and approve again *(human)*"
        )
    return f"**approved** by {who} on {when}{note} *(human)*"


# ---- the journey -----------------------------------------------------------


@dataclass(frozen=True)
class JourneyEntry:
    """One thing that happened to this ticket, stamped with the stage and the time.

    **Append-only by construction** — this module offers no way to update or delete one.
    That is the point of the phase: when implement disagrees with the design, the
    disagreement is the most valuable thing on the page, and a later stage that could
    tidy an earlier one away would remove exactly the evidence worth keeping.
    """

    run_id: str
    stage: str
    status: str  # ok | skipped | failed
    detail: str
    at: str  # ISO seconds, UTC
    # Structured actuals on the run-outcome entry. Section 11 reads these rather than
    # parsing them back out of ``detail`` — a cost history recovered by regex is one that
    # breaks the first time somebody rewords a log line. Defaulted so entries written
    # before this existed still load.
    tokens: int = 0
    usd: float = 0.0


def journey_path(intent_id: str, *, root: Path | str = ".", out: Path | str | None = None) -> Path:
    return (Path(out) if out else plan_dir(root)) / f"{intent_id}-journey.jsonl"


def append_journey(
    entry: JourneyEntry,
    *,
    intent_id: str,
    root: Path | str = ".",
    out: Path | str | None = None,
) -> Path:
    """Add one line. JSONL because appending must never rewrite what is already there."""
    path = journey_path(intent_id, root=root, out=out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(entry)) + "\n")
    return path


def load_journey(
    intent_id: str, *, root: Path | str = ".", out: Path | str | None = None
) -> list[JourneyEntry]:
    """Every entry, in the order it happened. A malformed line is skipped, not fatal."""
    path = journey_path(intent_id, root=root, out=out)
    if not path.is_file():
        return []
    entries: list[JourneyEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(JourneyEntry(**json.loads(line)))
        except (ValueError, TypeError):
            continue
    return entries


def design_disagreement(planned: list[str], touched: list[str]) -> str:
    """What implement did that the design did not say, and vice versa.

    Deterministic, and the reason this section earns its place: a run that quietly edited
    three files nobody planned is the single most useful thing a reader can be told, and
    today it is visible only by reading the diff.
    """
    want, got = {p.strip() for p in planned if p.strip()}, {t.strip() for t in touched if t.strip()}
    unplanned, untouched = sorted(got - want), sorted(want - got)
    if not unplanned and not untouched:
        return ""
    parts = []
    if unplanned:
        parts.append("changed but not planned: " + ", ".join(f"`{f}`" for f in unplanned))
    if untouched:
        parts.append("planned but not changed: " + ", ".join(f"`{f}`" for f in untouched))
    return "implement disagreed with the design — " + "; ".join(parts)


def _journey_block(entries: list[JourneyEntry]) -> str:
    """The journey, grouped by run, oldest first. Rendered below the twelve sections."""
    if not entries:
        return ""
    lines = [_JOURNEY_MARKER.strip(), ""]
    lines.append(_label(HUMAN + " + machine", "what happened after the plan was written"))
    seen: list[str] = []
    for entry in entries:
        if entry.run_id not in seen:
            seen.append(entry.run_id)
            lines.append(f"\n**Run `{entry.run_id}`**\n")
        mark = {"ok": "✓", "failed": "✗", "skipped": "·"}.get(entry.status, "·")
        lines.append(f"- {mark} **{entry.stage}** — {entry.detail} _({entry.at})_")
    return "\n".join(lines) + "\n"


# ---- sections 11 and 12: cost and confidence -------------------------------

# Roughly four characters to a token for source. Used only to turn bytes we already
# measured into an order of magnitude, and said out loud wherever it is shown.
_BYTES_PER_TOKEN = 4
# Codegen's own ceiling: output covers thinking plus reply. The gap between "no output"
# and "all of it" is the honest width of an estimate nobody has measured yet.
_OUTPUT_CAP_TOKENS = 32_000
# Providers worth pricing a swap against. Deliberately short: the catalog holds ~1,700
# priced models across 40-odd providers, and a table nobody reads to the end bounds
# nothing. Add a provider here when someone actually runs the pipeline on it.
_COMPARE_PROVIDERS = ("anthropic", "openai", "gemini")
_MAX_MODELS_PER_PROVIDER = 3


def _measured_runs(journey: list[JourneyEntry]) -> list[JourneyEntry]:
    return [e for e in journey if e.stage == "run" and e.tokens]


def _cost_block(*, carried_bytes: int, journey: list[JourneyEntry]) -> str:
    """Section 11. Measured where there is history, estimated where there is not — labelled."""
    from orchestrator.core.llm import catalog

    model_id = catalog.resolve("codegen")
    info = catalog.describe(model_id)
    measured = _measured_runs(journey)

    lines: list[str] = []
    if measured:
        totals = sorted(e.tokens for e in measured)
        spend = [e.usd for e in measured if e.usd]
        completed = sum(1 for e in measured if e.status == "ok")
        lines.append(
            f"**Measured** over {len(measured)} run(s) of this ticket: "
            f"{sum(totals) // len(totals):,} tokens mean, {totals[0]:,} lightest, {totals[-1]:,} heaviest."
        )
        if spend:
            lines.append(
                f"Actual spend: ${sum(spend):.2f} across all runs, ${sum(spend) / len(spend):.2f} mean.\n"
            )
        else:
            lines.append("")
        if completed < len(measured):
            lines.append(
                f"**{len(measured) - completed} of {len(measured)} run(s) produced nothing, and cost the "
                "same as the ones that did.** A failed run is not a cheap run.\n"
            )
    else:
        lines.append(
            "**No measured history for this ticket** — nothing has been run yet, so the "
            "below is an estimate.\n"
        )

    est_in = carried_bytes // _BYTES_PER_TOKEN
    lines.append(
        f"**Estimated** from the {carried_bytes:,} b this prompt carries: ~{est_in:,} input tokens "
        f"(at ~{_BYTES_PER_TOKEN} chars/token), and between 0 and {_OUTPUT_CAP_TOKENS:,} output — "
        "codegen's cap covers thinking plus reply, and the width of that band *is* the uncertainty.\n"
    )

    rows = ["| model | provider | $/Mtok in | $/Mtok out | this prompt, low–high |", "|---|---|---|---|---|"]
    priced = [m for m in catalog.catalog() if m.input_usd_per_mtok or m.output_usd_per_mtok]
    resolved = next((m for m in priced if m.id == model_id), None)

    # Comparison spans providers, because the question a reader has is "what would
    # switching cost", and the answer is useless if it only lists neighbours of what they
    # already run. Selection is by *price tier*, not by recency: the catalog carries no
    # release date, so "the latest model" is not a fact available here — and a hardcoded
    # list of latest ids goes stale silently, which is worse than not claiming it.
    anchor = float(resolved.input_usd_per_mtok or 0.0) if resolved else 0.0
    shown: list[Any] = [resolved] if resolved else []
    seen_ids: set[str] = {m.id for m in shown}
    for provider in _COMPARE_PROVIDERS:
        family = [m for m in priced if m.provider == provider and m.id not in seen_ids and m.supports_tools]
        # Nearest the resolved model's input price first — a like-for-like swap — then by
        # id so the same commit always renders the same table.
        family.sort(key=lambda m: (abs(float(m.input_usd_per_mtok or 0.0) - anchor), m.id))
        for candidate in family[:_MAX_MODELS_PER_PROVIDER]:
            seen_ids.add(candidate.id)
            shown.append(candidate)

    for m in shown:
        rate_in = float(m.input_usd_per_mtok or 0.0)
        rate_out = float(m.output_usd_per_mtok or 0.0)
        low = est_in * rate_in / 1_000_000
        high = low + _OUTPUT_CAP_TOKENS * rate_out / 1_000_000
        mark = " *(resolved)*" if m.id == model_id else ""
        rows.append(
            f"| `{m.id}`{mark} | {m.provider} | {rate_in:.2f} | {rate_out:.2f} | ${low:.2f}–{high:.2f} |"
        )
    rows.append(
        f"\n_Nearest {_MAX_MODELS_PER_PROVIDER} by input price per provider, of {len(priced):,} "
        "priced models — a like-for-like swap, not a ranking. `orchestrator models --provider "
        "<name>` lists them all._"
    )
    if info is None:
        rows.append(
            f"\n_`{model_id}` is not in the installed catalog — its own price is unknown, and the "
            "estimate above cannot be made for it._"
        )
    lines.append("\n".join(rows) + "\n")

    lines.append(
        "**A failed run costs what a successful one costs.** The prompt is resent on every "
        "corrective attempt, so the bill scales with attempts, not with outcomes.\n"
    )
    return "\n".join(lines)


def _confidence_block(
    *,
    signals: dict[str, Any],
    journey: list[JourneyEntry],
) -> str:
    """Section 12. Two numbers, never one — each a band with the basis under it.

    **Deterministic on purpose, and not a preference.** Phase 4 binds an approval to a
    digest of this document. A model-written confidence score would move on every render,
    the digest with it, and every approval would be stale the moment it was granted. So the
    score is computed from what the plan could and could not establish, and shown as a band
    with its basis — never a bare number that invites more trust than a band would.
    """
    rows = [
        "| what the plan established | reading | weight |",
        "|---|---|---|",
    ]
    # A check that cannot apply to this ticket is not a check this ticket failed. Root
    # cause is the case: a feature has none to establish, and scoring its absence capped
    # every enhancement a point below every bug while telling the reader nothing.
    # ``applies`` drops such a row out of the denominator instead.
    score = 0
    possible = 0
    for label, applies, ok, good, bad, na in (
        (
            "Validity gate",
            True,
            signals.get("verdict") == "PROCEED",
            "PROCEED — nothing contradicts the code",
            f"{signals.get('verdict') or 'unknown'} — the ticket disagrees with the code",
            "",
        ),
        (
            "Where it lands",
            True,
            bool(signals.get("brief_agrees")),
            "the brief and the design name the same files",
            "the brief names none of the files being changed",
            "",
        ),
        (
            "Root cause",
            bool(signals.get("root_cause")),
            bool(signals.get("fault_site")),
            "localized to a symbol",
            "a file at best — no line established",
            "nothing to localize — not a bug, so nothing is owed",
        ),
        (
            "Named paths",
            True,
            not signals.get("unverified"),
            "every path the design names is in the graph",
            "the design names paths the graph has never seen",
            "",
        ),
        (
            "Context budget",
            True,
            not signals.get("over_budget"),
            "the named files fit the window whole",
            "over budget — codegen will excerpt",
            "",
        ),
    ):
        if not applies:
            rows.append(f"| {label} | {na} | n/a |")
            continue
        possible += 1
        score += 1 if ok else 0
        rows.append(f"| {label} | {good if ok else bad} | {'+' if ok else '−'} |")

    band = "high" if score == possible else "medium" if score * 2 >= possible else "low"
    out = [
        f"**Is the analysis right? — {band}** ({score} of {possible} applicable checks "
        "positive). A band, not a percentage: nothing here measures correctness, only how "
        "much the plan managed to establish.\n",
        "\n".join(rows) + "\n",
    ]

    runs = _measured_runs(journey)
    if runs:
        done = sum(1 for e in runs if e.status == "ok")
        rate = "high" if done == len(runs) else "low" if done == 0 else "medium"
        out.append(
            f"**Will an unattended run complete? — {rate}.** Measured, not guessed: "
            f"**{done} of {len(runs)} run(s)** of this ticket completed. That is the base rate, "
            "and it is the only honest input anyone has.\n"
        )
    else:
        out.append(
            "**Will an unattended run complete? — unestablished.** No run of this ticket has "
            "happened, so there is no base rate. Nothing here should be read as optimism.\n"
        )

    untested = signals.get("untested") or 0
    if untested:
        out.append(
            f"**And the plan raises its own bar:** {untested} symbol(s) in the files being "
            "changed have no test, so the delivery is larger than the spec implies.\n"
        )
    return "\n".join(out)


# ---- section 3: root cause -------------------------------------------------


def _root_cause_block(report: Any) -> str:
    """Section 3, or "" when there is nothing grounded to say.

    The record's rule is that a feature's root-cause section is *omitted rather than
    padded*. The same applies to a bug reported with no failure text: a section whose
    only content is "nothing resolved" is padding that a reader has to read to discover
    is empty. The test is evidence, not ticket type — an exception or a fault module.
    """
    if report is None:
        return ""
    exception = str(getattr(report, "exception", "") or "")
    site = str(getattr(report, "fault_site", "") or "")
    module = str(getattr(report, "fault_module", "") or "")
    if not exception and not module:
        return ""

    lines: list[str] = []
    if exception:
        lines.append(f"**Exception:** `{exception}`\n")
    if site:
        lines.append(f"**Fault site:** {site}" + (f" (in `{module}`)" if module else "") + "\n")
    elif module:
        lines.append(f"**Fault site:** `{module}` — named by the ticket, not localized to a line.\n")
    if getattr(report, "recently_changed", False):
        lines.append("⚠ This module changed recently — a regression is the leading hypothesis.\n")

    hypotheses = list(getattr(report, "hypotheses", []) or [])
    if hypotheses:
        lines.append("**Hypotheses, ranked — evidence, not a verdict:**\n")
        for i, hyp in enumerate(hypotheses, 1):
            lines.append(f"{i}. **[{getattr(hyp, 'confidence', 'medium')}]** {getattr(hyp, 'claim', '')}")
            lines.extend(f"    - {e}" for e in (getattr(hyp, "evidence", ()) or ()))
        lines.append("")

    # The template requires this line: what the root cause puts *out* of scope. It is only
    # honest when something was actually localized — a file named by a ticket rules nothing
    # out, and saying otherwise would narrow the work on the strength of a guess.
    if site:
        lines.append(
            f"**Consequence:** the failure is at {site}. A change elsewhere is out of scope "
            "until this is disproved.\n"
        )
    else:
        lines.append(
            f"**Consequence:** the ticket establishes the file, not the line — nothing inside "
            f"`{module}` is ruled out yet.\n"
        )
    return "\n".join(lines)


# ---- section 5: the diagram ------------------------------------------------


def _mermaid_blast(bd: dict[str, Any]) -> str:
    """A `flowchart TD` of what imports what changes, and the fan-in inside it.

    Held to the subset `md.js` renders — nodes declared first with quoted labels, then
    bare-id edges, no chaining. Anything outside it falls back to `<pre>` in our own UI
    while still looking fine on GitHub, so a broken diagram is invisible until someone
    opens Spine. Returns "" rather than a guess when there is nothing to draw: no
    picture beats a wrong picture.
    """
    modules = (bd.get("modules") or [])[:_MAX_MODULES]
    if not modules:
        return ""

    ids: dict[str, str] = {}

    def _id(key: str) -> str:
        if key not in ids:
            ids[key] = f"n{len(ids)}"
        return ids[key]

    def _safe(text: str) -> str:
        # Only what would break the label: a quote closes it and a bracket closes the
        # node. Dots and slashes are fine inside quotes, and stripping them turned
        # `src/orchestrator/cli.py` into "src orchestrator cli py" — a path no reader
        # recognises. Ids are a separate, sanitised namespace (`_id`).
        return str(text).replace('"', "'").replace("[", "(").replace("]", ")").strip() or "?"

    importers: list[str] = []
    changed: list[str] = []
    hotspots: list[str] = []
    edges: list[str] = []

    for mod in modules:
        ref = str(mod.get("ref") or "")
        if not ref:
            continue
        mid = _id(f"m:{ref}")
        changed.append(f'    {mid}["{_safe(ref)}"]')
        for name in (mod.get("importer_names") or [])[:_MAX_IMPORTERS]:
            iid = _id(f"i:{name}")
            line = f'    {iid}["{_safe(name)}"]'
            if line not in importers:
                importers.append(line)
            edges.append(f"  {iid} --> {mid}")
        for spot in (mod.get("hotspots") or [])[:_MAX_HOTSPOTS]:
            name = str(spot.get("name") or "")
            if not name:
                continue
            hid = _id(f"h:{ref}:{name}")
            callers = int(spot.get("callers") or 0)
            hotspots.append(f'    {hid}["{_safe(name)}<br/>{callers} caller(s)"]')
            edges.append(f"  {mid} --> {hid}")

    lines = ["```mermaid", "flowchart TD"]
    if importers:
        lines.append('  subgraph inbound["what imports it"]')
        lines.extend(importers)
        lines.append("  end")
    lines.append('  subgraph target["what this ticket changes"]')
    lines.extend(changed)
    lines.append("  end")
    if hotspots:
        lines.append('  subgraph fanin["fan-in inside it"]')
        lines.extend(hotspots)
        lines.append("  end")
    lines.extend(dict.fromkeys(edges))  # dedupe, keep order
    lines.append("```")
    return "\n".join(lines)


def _is_test_module(name: str) -> bool:
    n = str(name)
    return n.startswith("test") or n.startswith("tests.") or ".test" in n or "_test" in n


def _blast_prose(bd: dict[str, Any], language: str = "python") -> str:
    """The three blocks the template requires, in order: reading, containment, caveat."""
    modules = bd.get("modules") or []
    shown = modules[:_MAX_MODULES]
    total_importers = sum(int(m.get("importers") or 0) for m in modules)
    total_hotspots = sum(len(m.get("hotspots") or []) for m in modules)

    elided = ""
    if len(modules) > len(shown):
        elided = f" Showing {len(shown)} of {len(modules)} module(s)."

    reading = (
        f"**Reading it:** {len(modules)} module(s) change; {total_importers} module(s) import "
        f"them, and {total_hotspots} symbol(s) inside them carry the fan-in.{elided}"
    )

    all_importers = [n for m in modules for n in (m.get("importer_names") or [])]
    if not all_importers:
        containment = (
            "**Containment:** nothing in the graph imports what changes. A change here "
            "cannot propagate outward."
        )
    elif all(_is_test_module(n) for n in all_importers):
        containment = (
            f"**Containment:** the only importers are tests ({', '.join(sorted(set(all_importers))[:8])}). "
            "Nothing in the product depends on what changes."
        )
    else:
        product = sorted({n for n in all_importers if not _is_test_module(n)})
        containment = (
            f"**Containment:** the neighbourhood reaches {len(product)} non-test module(s): "
            f"{', '.join(product[:8])}. A change here is visible to them."
        )

    if not bd.get("call_graph_available"):
        caveat = (
            "**Caveat:** no call graph for this language — this is module-level impact only, "
            "and symbol-level fan-in is omitted rather than zero."
        )
    else:
        caveat = (
            "**Caveat:** method calls through an instance emit no `CALLS` edge (SSPN-48), so "
            "per-method counts under-report. Module-function counts are exact."
        )
        # The measured version of the same caveat. "Counts under-report" tells a reader to be
        # vaguely careful; "recall is 0.73" tells them roughly one call in four is missing and
        # lets them decide whether that matters for this ticket.
        #
        # The parenthetical is load-bearing: this is measured against the extractor's own
        # fixtures, NOT against the repository being described. A reader who takes it as a
        # statement about their own code has been misled by us. None means the language was
        # never measured — six of eight front-ends have no corpus — and an unmeasured language
        # has not scored zero, so it gets no clause at all.
        recall = measured_recall(language)
        if recall is not None:
            caveat += (
                f" Measured `CALLS` recall for {language} is **{recall:.2f}** "
                "(against the extractor's own test corpus, not this repository) — "
                "treat this list as a lower bound."
            )

    unverified = bd.get("unverified_references") or []
    if unverified:
        caveat += (
            f"\n\n**Unverified references:** {', '.join(str(u) for u in unverified[:8])} — named by "
            "the design and absent from the graph."
        )
    return f"{reading}\n\n{containment}\n\n{caveat}\n"


# ---- section 5, fourth block: the evidence ---------------------------------


def collect_evidence(store: Any, *, files: list[str], root: Path) -> dict[str, Any]:
    """The deterministic facts about the neighbourhood that the diagram cannot draw.

    Computed here rather than in the renderer because it needs the graph, and a renderer
    that re-derives facts from paths is the thing invariant 1 forbids. Everything is
    bounded and records what it elided.
    """
    from orchestrator.pkg.facts import NodeKind
    from orchestrator.sdlc.coverage import CoverageIndex, is_test_node

    wanted = {f.strip() for f in files if f.strip()}
    in_scope = [n for n in store.nodes if getattr(n.provenance, "file", "") in wanted]
    modules = [n for n in in_scope if n.kind is NodeKind.MODULE]
    symbols = [n for n in in_scope if n.kind in (NodeKind.FUNCTION, NodeKind.TYPE)]

    index = CoverageIndex(store)
    covered: list[Any] = []
    uncovered: list[Any] = []
    for node in sorted(symbols, key=lambda n: n.name):
        (covered if index.is_covered(node.id) else uncovered).append(node)

    endpoints: list[tuple[str, str]] = []
    for node in in_scope:
        for ep in store.endpoints_called_by(node.id):
            endpoints.append((node.name, f"calls {ep.name}"))
        for ep in store.exposers_of(node.id):
            endpoints.append((node.name, f"serves {ep.name}"))

    regression = sorted(
        {imp.name for mod in modules for imp in store.importers_of(mod.id) if is_test_node(imp)}
    )
    docs = sorted({doc.name for node in in_scope for doc in store.docs_for(node.id)})

    return {
        "call_graph_available": index.call_graph_available,
        "symbols": len(symbols),
        "covered": [n.name for n in covered],
        "uncovered": [n.name for n in uncovered],
        "endpoints": endpoints,
        "regression": regression,
        "docs": docs,
        "history": _recent_history(sorted(wanted), root=root),
    }


def _recent_history(files: list[str], *, root: Path, commits: int = 5) -> list[str]:
    """What has recently happened to the files being changed.

    A file nobody has touched in a year and a file touched three times last week carry
    different risk, and neither is visible in the graph.
    """
    if not files:
        return []
    try:
        result = subprocess.run(
            ["git", "log", f"-{commits}", "--format=%h %ad %s", "--date=short", "--", *files],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in result.stdout.splitlines() if line.strip()] if result.returncode == 0 else []


def _evidence_block(ev: dict[str, Any]) -> str:
    """The fourth block of section 5 — five findings, each saying what it does not know."""
    if not ev:
        return ""
    lines = ["**Evidence:**\n"]

    covered, uncovered = ev.get("covered") or [], ev.get("uncovered") or []
    total = len(covered) + len(uncovered)
    if not ev.get("call_graph_available"):
        lines.append("- *Coverage today:* no call graph for this language — unknown, not zero.\n")
    elif total:
        # Distinct names for the display list — two nested functions can share a name, and
        # "`_go`, `_go`" reads as a rendering bug. The counts stay per-symbol, which is what
        # is actually true.
        names = list(dict.fromkeys(uncovered))
        listed = ", ".join(f"`{n}`" for n in names[:8])
        elided = f" (+{len(uncovered) - 8} more)" if len(uncovered) > 8 else ""
        detail = f" — untested: {listed}{elided}" if uncovered else ""
        lines.append(
            f"- *Coverage today:* {len(uncovered)} of {total} symbol(s) in the files this ticket "
            f"changes are reached by no test{detail}. Reached by a test means exercised, not "
            "asserted correct.\n"
        )

    endpoints = ev.get("endpoints") or []
    if endpoints:
        shown = "; ".join(f"`{sym}` {what}" for sym, what in endpoints[:6])
        more = f" (+{len(endpoints) - 6} more)" if len(endpoints) > 6 else ""
        lines.append(f"- *Endpoints crossed:* {shown}{more}.\n")
    else:
        lines.append(
            "- *Endpoints crossed:* none joined. A path built from an f-string or a variable "
            "yields no edge, so this is silence rather than absence.\n"
        )

    regression = ev.get("regression") or []
    if regression:
        shown = ", ".join(f"`{t}`" for t in regression[:8])
        more = f" (+{len(regression) - 8} more)" if len(regression) > 8 else ""
        lines.append(f"- *Regression surface:* {shown}{more} import what changes — run these.\n")
    else:
        lines.append("- *Regression surface:* no test module imports what changes.\n")

    history = ev.get("history") or []
    if history:
        lines.append("- *Recent history:*\n")
        # A commit subject in this repo routinely contains backticks; wrapping the whole
        # line in one more closes the span early and the rest renders as prose.
        lines.extend(f"    - {line.replace('`', '')}\n" for line in history)

    docs = ev.get("docs") or []
    if docs:
        shown = ", ".join(f"`{d}`" for d in docs[:6])
        more = f" (+{len(docs) - 6} more)" if len(docs) > 6 else ""
        lines.append(f"- *Docs affected:* {shown}{more} mention what changes.\n")

    return "".join(lines)


# ---- section 8: criteria, in three states ----------------------------------


def _criteria_block(spec: dict[str, Any]) -> str:
    """Stated, stated-but-already-met, and proposed — never silently narrowed.

    An already-met criterion stays on the page with the evidence that satisfies it.
    Deleting it is how six criteria became four with no reader able to tell: a run
    would report it met having changed nothing, which is the failure this document
    exists to catch.
    """
    stated = [str(c) for c in (spec.get("acceptance_criteria") or [])]
    proposed = [str(c) for c in (spec.get("proposed_criteria") or [])]
    met = {str(k): str(v) for k, v in (spec.get("met_criteria") or {}).items()}

    rows: list[str] = ["| # | Criterion | State | Satisfied by |", "|---|---|---|---|"]
    n = 0
    for text in stated:
        n += 1
        if text in met:
            rows.append(f"| {n} | {text} | **stated · already met** | {met[text]} |")
        else:
            rows.append(f"| {n} | {text} | stated | — |")
    for text in proposed:
        n += 1
        rows.append(f"| {n} | {text} | proposed *(model)* | — |")

    out = "\n".join(rows) + "\n"

    already = sum(1 for t in stated if t in met)
    if already:
        out += (
            f"\n**{already} of {len(stated)} stated criteria already satisfied by code that "
            "exists.** A run would report them met having changed nothing. The delivery is the "
            f"remaining {len(stated) - already}.\n"
        )
    unmatched = [k for k in met if k not in stated]
    if unmatched:
        out += (
            "\n**Warning:** `met_criteria` names "
            f"{len(unmatched)} criterion/criteria that are not in `acceptance_criteria` — "
            "they are ignored above, and the mismatch is probably a typo: "
            + "; ".join(f"“{u[:60]}…”" for u in unmatched[:3])
            + "\n"
        )
    if not stated:
        out += "\n**No stated criteria.** There is nothing for the acceptance judge to verify.\n"
    return out


# ---- sections 7 and 10: files and the prompt -------------------------------


def _file_rows(paths: list[str], root: Path) -> tuple[list[str], list[str], int]:
    """Split named paths into those that exist and those that do not, with sizes."""
    changed: list[str] = []
    created: list[str] = []
    total = 0
    for raw in paths:
        rel = str(raw).strip()
        if not rel:
            continue
        p = root / rel
        try:
            size = p.stat().st_size if p.is_file() else 0
        except OSError:
            size = 0
        if size or p.is_file():
            total += size
            changed.append(f"| `{rel}` | {size:,} b |")
        else:
            created.append(f"| `{rel}` | named by the design, absent from the tree |")
    return changed, created, total


# ---- the document ----------------------------------------------------------


def render_build_md(
    spec: dict[str, Any],
    *,
    investigation: Any,
    design: dict[str, Any],
    validity: Any,
    root: Path,
    commit: str,
    context_budget: int,
    language: str = "python",
    evidence: dict[str, Any] | None = None,
    rca: Any = None,
    approval: PlanApproval | None = None,
    journey: list[JourneyEntry] | None = None,
) -> str:
    """Assemble the twelve sections.

    Near-pure: no I/O beyond stat-ing the named files and reading the packaged accuracy
    baseline (a committed constant, so the output stays deterministic).
    """
    title = str(spec.get("title") or "untitled")
    intent = str(spec.get("intent_id") or "unknown")
    files = [str(f) for f in (design.get("files_to_touch") or [])]
    blast = design.get("blast_radius") or {}
    changed, created, carried = _file_rows(files, root)

    landing = list(getattr(investigation, "landing", []) or [])
    landing_files = {str(getattr(land, "where", "")).split(":", 1)[0] for land in landing}
    agreed = sorted(landing_files & set(files))

    out: list[str] = []
    add = out.append

    add(f"# {intent} — build document\n")
    add(f"**Spec:** `{intent}` · **Derived at:** `{commit}` · **Status:** {_STATUS_PLACEHOLDER}\n")
    # .value first: str-Enum stringifies as "Verdict.PROCEED", which is a Python repr
    # leaking onto a page a human is meant to read.
    raw_verdict = getattr(validity, "verdict", "")
    add(f"**Validity:** {getattr(raw_verdict, 'value', raw_verdict) or 'unknown'}\n")
    findings = list(getattr(validity, "findings", []) or [])
    if findings:
        add("> " + "\n> ".join(str(getattr(f, "detail", f)) for f in findings) + "\n")
    add(
        "Assembled by `orchestrator sdlc plan`. No code was written and nothing was "
        "spent. Every section carries where it came from.\n"
    )
    add("---\n")

    add("## 1. Requirement")
    add(_label(STATED, "the ticket body, quoted"))
    add(f"**{title}**\n")
    add(str(spec.get("summary") or "_The ticket says nothing beyond its title._") + "\n")

    add("## 2. Intent")
    add(_label(MODEL, "`intake/specs.py` — the spec writer"))
    add(str(spec.get("user_story") or "_No user story on the spec._") + "\n")

    add("## 3. Root cause")
    root_cause = _root_cause_block(rca)
    if root_cause:
        add(_label(DETERMINISTIC, f"`sdlc/rca.py` @ `{commit}` — hypotheses, not a verdict"))
        add(root_cause)
    else:
        add(_pending("no exception and no file named — nothing to localize, so nothing is claimed"))

    add("## 4. PKG — what the graph knows")
    add(_label(DETERMINISTIC, f"`FactStore` @ `{commit}`"))
    if landing:
        add("| symbol | kind | where | callers | module |")
        add("|---|---|---|---|---|")
        for land in landing[:10]:
            add(
                f"| `{getattr(land, 'name', '')}` | {getattr(land, 'kind', '')} | "
                f"`{getattr(land, 'where', '')}` | {getattr(land, 'callers', 0)} | "
                f"`{getattr(land, 'module', '')}` |"
            )
        add("")
    else:
        add("_Nothing in the graph matched this ticket's words._\n")
    areas = list(getattr(investigation, "areas", []) or [])
    if areas:
        add(f"**Areas:** {', '.join(f'`{a}`' for a in areas[:8])}\n")

    # The SSPN-49 finding, made deterministic: lexical retrieval matched the ticket's
    # words to modules literally named that, and never reached the file the ticket
    # states. A brief that names none of the files being changed is noise, and saying
    # so here costs nothing while carrying it silently costs a run.
    if not landing:
        add("**The brief is empty.** Locate the change by hand before building.\n")
    elif agreed:
        add(
            f"**The brief agrees with the design** on {len(agreed)} file(s): "
            + ", ".join(f"`{a}`" for a in agreed)
            + ".\n"
        )
    else:
        add(
            "**The brief names none of the files this ticket will change.** Retrieval is "
            "lexical — it matched the ticket's words, not its work. Treat it as noise here.\n"
        )

    add("## 5. Blast radius")
    add(_label(DETERMINISTIC, f"`sdlc/impact.py` @ `{commit}`"))
    diagram = _mermaid_blast(blast)
    if diagram:
        add(diagram + "\n")
    else:
        add("_Nothing to draw — no module in the graph matched the files being changed._\n")
    add(_blast_prose(blast, language))
    add(_evidence_block(evidence or {}))

    add("## 6. Design")
    origin = "an LLM" if design.get("llm") else "the deterministic heuristic (no LLM)"
    add(_label(DETERMINISTIC if not design.get("llm") else MODEL, f"`sdlc/design.py` — {origin}"))
    add(str(design.get("approach") or "_No approach._") + "\n")
    risks = [str(r) for r in (design.get("risks") or [])]
    if risks:
        add("*Risks, as the design states them:*\n")
        add("\n".join(f"- {r}" for r in risks) + "\n")
    add(f"**Test strategy:** {design.get('test_strategy') or '—'}\n")

    add("## 7. Files")
    add(_label(DETERMINISTIC, "the paths the spec states, plus the design"))
    if changed:
        add("**Changed**\n")
        add("| file | size |")
        add("|---|---|")
        out.extend(changed)
        add("")
    if created:
        add("**Created**\n")
        add("| file | note |")
        add("|---|---|")
        out.extend(created)
        add("")
    if not changed and not created:
        add("_The design proposes no files. Locate the change before building._\n")

    add("## 8. Acceptance criteria")
    add(_label(f"{STATED} + {MODEL}", "the spec, reconciled against the code"))
    add(_criteria_block(spec))

    add("## 9. Facts the generator needs")
    add(_pending("reading the named source for what must not be duplicated — no phase owns this yet"))

    add("## 10. Codegen prompt")
    add(_label(DETERMINISTIC, "`sdlc/codegen.py` — prompt assembly"))
    add(f"**System:** `_IMPLEMENT_SYSTEM` ({language})\n")
    # Only the sections that exist. A manifest promising section 9 while section 9 says it
    # was never established describes a prompt nobody could assemble.
    carried_sections = [
        n for n, present in ((1, True), (3, bool(root_cause)), (6, True), (8, True)) if present
    ]
    listed = ", ".join(str(n) for n in carried_sections[:-1]) + f" and {carried_sections[-1]}"
    add(f"**User payload:** sections {listed} of this document, plus the files below whole.\n")
    pct = (carried / context_budget * 100) if context_budget else 0.0
    add(f"**Context:** {carried:,} b of {context_budget:,} — {pct:.0f}%.\n")
    if carried > context_budget:
        add("**Over budget.** Codegen will excerpt; the model will not see these files whole.\n")

    entries = list(journey or [])

    add("## 11. Token usage & cost")
    measured = bool(_measured_runs(entries))
    add(
        _label(
            DETERMINISTIC if measured else f"{DETERMINISTIC} (estimate)",
            "the installed model catalog" + (" and this ticket's measured runs" if measured else ""),
        )
    )
    add(_cost_block(carried_bytes=carried, journey=entries))

    add("## 12. Confidence")
    add(_label(DETERMINISTIC, "what the plan could and could not establish — a band, never a score"))
    add(
        _confidence_block(
            signals={
                "verdict": getattr(raw_verdict, "value", raw_verdict),
                "brief_agrees": bool(agreed),
                # Whether section 3 rendered at all — not whether it localized well.
                "root_cause": bool(root_cause),
                "fault_site": bool(getattr(rca, "fault_site", "")),
                "unverified": bool(blast.get("unverified_references")),
                "over_budget": carried > context_budget,
                "untested": len((evidence or {}).get("uncovered") or []),
            },
            journey=entries,
        )
    )

    # The journey goes below the twelve sections, never inside them: it is what happened
    # after the plan was read, and the digest deliberately stops before it.
    block = _journey_block(entries)
    if block:
        out.extend(["", block])

    # The status is written last because it depends on a digest of everything above it.
    document = "\n".join(out).rstrip() + "\n"
    return document.replace(_STATUS_PLACEHOLDER, _status_line(approval, plan_digest(document)))


async def build_plan(
    spec: dict[str, Any],
    *,
    root: Path | str = ".",
    language: str = "python",
    approval: PlanApproval | None = None,
    journey: list[JourneyEntry] | None = None,
) -> str:
    """Run the four cheap stages and render the document. No worktree, no codegen.

    The builders are called directly rather than through ``autorun``'s ``_stage_*``
    wrappers: those carry run records, checkpoints, approval parking and Jira worklogs,
    and a plan must touch none of that — the ticket moves when work begins, not when
    someone thinks about it.
    """
    from orchestrator.pkg import FactStore, load_or_extract
    from orchestrator.pkg.overview import build_overview
    from orchestrator.sdlc.codegen import _MAX_CONTEXT_BYTES
    from orchestrator.sdlc.design import produce_design
    from orchestrator.sdlc.investigate import build_investigation
    from orchestrator.sdlc.rca import build_rca
    from orchestrator.sdlc.validity import assess

    root_path = Path(root)
    batch = load_or_extract(root_path)
    store = FactStore(batch)
    overview = build_overview(batch)

    investigation = build_investigation(
        str(spec.get("title") or ""),
        str(spec.get("summary") or ""),
        store=store,
        root=root_path,
    )
    landing = []
    for land in getattr(investigation, "landing", []) or []:
        where = str(getattr(land, "where", "")).split(":", 1)[0]
        if where and where not in landing:
            landing.append(where)

    # The gate runs, and its verdict is reported — but it does not stop a plan. Refusing
    # to *show* someone the evidence for a refusal is the opposite of the point.
    assessment = assess(
        spec,
        store=store,
        landing=landing,
        issue_type=str(spec.get("issue_type") or ""),
        root=root_path,
        context_budget=_MAX_CONTEXT_BYTES,
    )
    design = await produce_design(spec, overview=overview, store=store, llm=None, root=root_path)
    # llm=None keeps this whole path free of a model call. `build_rca` enriches with one
    # when given it; the deterministic core is what section 3 renders, and the section is
    # labelled accordingly rather than borrowing the authority of a report it did not run.
    report = await build_rca(
        f"{spec.get('title', '')}\n{spec.get('summary', '')}", store=store, root=root_path, llm=None
    )

    return render_build_md(
        spec,
        investigation=investigation,
        design=design,
        validity=assessment,
        root=root_path,
        commit=derived_at(root_path),
        context_budget=_MAX_CONTEXT_BYTES,
        language=language,
        evidence=collect_evidence(
            store, files=[str(f) for f in (design.get("files_to_touch") or [])], root=root_path
        ),
        rca=report,
        approval=approval,
        journey=journey,
    )


# ---- the gate --------------------------------------------------------------


class PlanNotApprovedError(Exception):
    """No current, approved plan for this spec — nothing should be built."""


async def require_approved_plan(spec: dict[str, Any], *, root: Path | str = ".") -> PlanApproval:
    """Refuse unless a human approved *this* plan, and it is still this plan.

    The check is a re-derivation, not a lookup: the plan is regenerated and its body
    re-digested. An approval that only proved a file once existed would keep approving a
    document nobody has read since the code moved underneath it — and determinism is
    exactly what makes the comparison meaningful.
    """
    intent = str(spec.get("intent_id") or "")
    approval = load_approval(intent, root=root)
    if approval is None:
        raise PlanNotApprovedError(
            f"no approved plan for {intent}. Produce one with "
            f"`orchestrator sdlc plan --spec <file>`, read it, then "
            f"`orchestrator sdlc approve {intent}`."
        )
    if approval.decision == "REJECTED":
        note = f": {approval.note}" if approval.note else ""
        raise PlanNotApprovedError(
            f"the plan for {intent} was rejected by {approval.decided_by or 'a human'}{note}."
        )

    current = plan_digest(await build_plan(spec, root=root))
    if current != approval.digest:
        raise PlanNotApprovedError(
            f"the plan for {intent} has changed since {approval.decided_by or 'it'} approved it "
            f"at `{approval.commit}` — re-read it and approve again "
            f"(`orchestrator sdlc plan --spec <file>` then `sdlc approve {intent}`)."
        )
    return approval


# ---- persistence -----------------------------------------------------------


def plan_dir(root: Path | str = ".") -> Path:
    """Where plans live: `.spine/plans/` beside the code they describe.

    Three constraints meet here. It must be **permanent** — the hand-written SSPN-49
    template sat in a `/tmp` scratchpad for two days, which is not storage. It must be
    **invisible to `understand`**, which ingests markdown from disk whether or not git
    tracks it; a live plan in the working tree would become a `Doc` node and change the
    graph the next stage reads. And it must be **stable per ticket**, so re-running is a
    diff rather than a new file.

    A dot-directory satisfies all three: `doc_source` skips dirnames starting with ".",
    so nothing here reaches the graph. Approved documents are promoted to
    `docs/specs/build-documents/` deliberately, by a human.
    """
    return Path(root) / ".spine" / "plans"


def persist(
    document: str,
    *,
    intent_id: str,
    root: Path | str = ".",
    out: Path | str | None = None,
) -> tuple[Path, Path | None]:
    """Write the plan to its stable path, keeping what it replaced.

    Returns ``(path, superseded)``. The previous document is snapshotted under
    ``history/`` keyed by the commit it was derived at — not a timestamp, because the
    document is deterministic and the commit is the axis that actually changed. Writing
    an identical document is a no-op with no snapshot, so re-running at the same commit
    does not churn the history.
    """
    target_dir = Path(out) if out else plan_dir(root)
    path = target_dir / f"{intent_id}-build.md"
    superseded: Path | None = None

    if path.is_file():
        previous = path.read_text(encoding="utf-8")
        if previous == document:
            return path, None
        match = _DERIVED_AT.search(previous)
        key = (
            match.group(1)
            if match
            else hashlib.sha1(previous.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        )
        history = target_dir / "history"
        history.mkdir(parents=True, exist_ok=True)
        superseded = history / f"{intent_id}-{key}.md"
        superseded.write_text(previous, encoding="utf-8")

    target_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path, superseded


__all__ = [
    "DETERMINISTIC",
    "HUMAN",
    "MODEL",
    "STATED",
    "JourneyEntry",
    "PlanApproval",
    "PlanNotApprovedError",
    "append_journey",
    "approval_path",
    "design_disagreement",
    "journey_path",
    "load_journey",
    "build_plan",
    "collect_evidence",
    "decided_by_default",
    "derived_at",
    "load_approval",
    "plan_digest",
    "require_approved_plan",
    "save_approval",
    "persist",
    "plan_dir",
    "render_build_md",
]
