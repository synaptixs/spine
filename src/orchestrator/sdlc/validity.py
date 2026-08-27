"""Is this ticket worth building — and is what it says about the code even true?

The one piece of judgment in the run agent. Everything else composes tools that already
exist; this decides whether to use them at all.

**Why it exists.** Two tickets in one cycle would have wasted a full run each:

* SSPN-3's acceptance criterion said *"11 ``Entity`` nodes on this repo"*. The source has 7.
  An agent that treats criteria as ground truth either loops forever trying to reach 11 or
  declares success at 7 and calls the criterion met.
* SSPN-18 was a design defect described in prose, with no traceback. The RCA path localizes
  from stack frames, so it resolved to nothing and reported *"the fault may be in a
  dependency"* — pointing away from a bug squarely inside this repo.

So the gate returns **one of a fixed set of verdicts with evidence**, never prose, and
refusing a ticket is a first-class outcome rather than an error path.

**Deterministic, no LLM.** Every check here reads the graph and compares it to what the
ticket claims. A model asked "is this ticket sound?" produces a confident opinion; the graph
produces a count. Where a check cannot be made from facts it is not made at all — the gate
under-claims rather than guessing, because a gate that cries wolf gets switched off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import NodeKind
from orchestrator.sdlc.profile_select import is_bug


class Verdict(str, Enum):
    """The gate's answer. ``PROCEED`` is the only one that continues a run."""

    PROCEED = "PROCEED"
    CRITERIA_WRONG = "CRITERIA_WRONG"  # a criterion contradicts the source
    UNLOCALIZED = "UNLOCALIZED"  # a Bug that resolves to no symbol
    ALREADY_DONE = "ALREADY_DONE"  # the graph says it exists
    DUPLICATE = "DUPLICATE"  # another run already covered it
    TOO_BIG = "TOO_BIG"  # split it before building it


# Countable things a ticket can assert about a repo, and the node kind that answers it.
# Only kinds the graph can count exactly: a claim about "functions" is rarely meant literally,
# where "3 endpoints" or "11 entities" is a checkable statement of fact.
_COUNTABLE: dict[str, NodeKind] = {
    "entity": NodeKind.ENTITY,
    "entities": NodeKind.ENTITY,
    "table": NodeKind.ENTITY,
    "tables": NodeKind.ENTITY,
    "endpoint": NodeKind.ENDPOINT,
    "endpoints": NodeKind.ENDPOINT,
    "route": NodeKind.ENDPOINT,
    "routes": NodeKind.ENDPOINT,
    "module": NodeKind.MODULE,
    "modules": NodeKind.MODULE,
}

# "11 Entity nodes", "7 tables", "≥ 70 route handlers". The noun may be wrapped in backticks
# and followed by a qualifier ("nodes", "handlers"), which is why the kind word is matched
# rather than the whole phrase.
_CLAIM = re.compile(
    r"(?P<count>\d+)\s+`?(?P<kind>[A-Za-z]+)`?\s*(?:nodes?|handlers?|declarations?)?\b",
    re.IGNORECASE,
)

# Phrases that make a count a claim about *this repo as it is*, rather than a target to reach.
# "add 3 endpoints" is work; "3 endpoints on this repo" is an assertion that can be false.
_ASSERTS_CURRENT = re.compile(
    r"\b(on|in)\s+(this|the)\s+(repo|repository|codebase|project)\b|\bcurrently\b|\bthere are\b",
    re.IGNORECASE,
)

DEFAULT_MAX_CRITERIA = 12
DEFAULT_MAX_FILES = 15


@dataclass(frozen=True)
class Finding:
    """One thing the gate noticed, with the evidence that makes it checkable."""

    check: str
    detail: str
    evidence: str = ""


@dataclass
class Assessment:
    verdict: Verdict = Verdict.PROCEED
    findings: list[Finding] = field(default_factory=list)

    @property
    def proceed(self) -> bool:
        return self.verdict is Verdict.PROCEED

    def render(self) -> str:
        lines = [f"**Verdict:** {self.verdict.value}", ""]
        if not self.findings:
            lines.append("Nothing in this ticket contradicts the code.")
            return "\n".join(lines)
        for finding in self.findings:
            lines.append(f"- **{finding.check}** — {finding.detail}")
            if finding.evidence:
                lines.append(f"    - {finding.evidence}")
        return "\n".join(lines)


def _criteria_text(spec: dict[str, Any]) -> list[str]:
    return [str(a) for a in (spec.get("acceptance_criteria") or [])]


def _all_criteria_text(spec: dict[str, Any]) -> list[str]:
    """Stated criteria **and** proposed ones (``proposed_criteria``, #137).

    A proposed criterion is the kind most likely to contradict an invariant — nobody asked
    for it — and it is still handed to codegen, so the gate has to read it too.
    """
    criteria = _criteria_text(spec)
    for proposed in spec.get("proposed_criteria") or []:
        # A proposal may be a plain string or a {"criterion": ..., "why": ...} record.
        if isinstance(proposed, dict):
            text = str(proposed.get("criterion") or proposed.get("text") or "").strip()
        else:
            text = str(proposed).strip()
        if text:
            criteria.append(text)
    return criteria


def _count_of(store: FactStore, kind: NodeKind) -> int:
    """Grounded nodes only: an external placeholder is a table someone else owns, not one
    this repo has."""
    return len([n for n in store.nodes if n.kind is kind and not n.external])


def _check_countable_claims(spec: dict[str, Any], store: FactStore) -> list[Finding]:
    """A criterion that states a number the graph can check, and gets it wrong.

    This is the SSPN-3 case exactly: *"11 Entity nodes on this repo"* against a source with
    seven ``__tablename__`` declarations. The number is not a target to reach — the phrasing
    asserts a present fact — so building to it is building to a false premise.
    """
    findings: list[Finding] = []
    for criterion in _criteria_text(spec):
        if not _ASSERTS_CURRENT.search(criterion):
            continue
        for match in _CLAIM.finditer(criterion):
            kind = _COUNTABLE.get(match.group("kind").lower())
            if kind is None:
                continue
            claimed = int(match.group("count"))
            actual = _count_of(store, kind)
            # A "≥ N" style criterion is satisfied by more, so only a strict mismatch counts.
            at_least = bool(re.search(r"(≥|>=|at least|or more)\s*$", criterion[: match.start()].strip()))
            if (at_least and actual >= claimed) or (not at_least and actual == claimed):
                continue
            findings.append(
                Finding(
                    check="countable-claim",
                    detail=(
                        f"the ticket says {claimed} {match.group('kind')} "
                        f"{'or more ' if at_least else ''}exist here; the graph holds {actual}"
                    ),
                    evidence=criterion.strip(),
                )
            )
    return findings


def _check_unbound_criteria(criteria: Any, issue_type: str = "") -> list[Finding]:
    """A criterion naming code the graph does not hold.

    Only claims can fail here — prose, CamelCase, env vars and tool names are not claims about
    this repository and never refuse a ticket. That rule lives in `criteria_binding`, which
    borrows it from the doc-drift reconciler rather than inventing a second one.

    **What the finding means depends on the ticket**, and `assess` acts on it accordingly. For
    a bug it is a false premise: the subject is code that already exists, so a criterion naming
    a symbol nobody can find describes a repository this is not. For an enhancement the missing
    symbol *is the deliverable* — and the better-written the criterion, the more precisely it
    names something not built yet. So the wording says which case the reader is in, rather than
    leaving one sentence to serve two opposite meanings.
    """
    rows = getattr(criteria, "unbound", ()) if criteria is not None else ()
    expected = not is_bug(issue_type)
    return [
        Finding(
            check="criterion-unbound",
            detail=(
                f"the criterion names {', '.join(row.claims)}, which the graph does not hold"
                + (" — expected for something not built yet" if expected else "")
            ),
            evidence=(
                row.text
                if not expected
                # One fact, reported twice, two stages apart: the design stage lists the same
                # absent name under unverified references (`impact.unverified_references`). A
                # reviewer who cannot tell it is one fact starts discounting both.
                else f"{row.text} — the design stage lists it again under unverified references"
            ),
        )
        for row in rows
    ]


# --- documented invariants -------------------------------------------------------------
# Hand-maintained on purpose. Parsing CLAUDE.md at runtime would make the gate depend on
# prose formatting — a worse contract than a short explicit list that a reviewer can read.
# Keep in sync with CLAUDE.md "Invariants" (invariant 2: `understand` / `state` and the
# episteme knowledge base are deterministic — no LLM call, no clock, no randomness).
_DETERMINISTIC_SURFACES: tuple[tuple[str, ...], ...] = (
    ("understand",),
    ("state",),
    ("episteme",),
    ("memory bank", "memory-bank"),
    ("knowledge base", "knowledge-base"),
    ("comprehension",),
    ("regression",),
)

_INVARIANT_REF = (
    "CLAUDE.md invariant 2: `understand` / `state` and the episteme knowledge base are "
    "deterministic — never add an LLM call, a clock read, or randomness to their output"
)

# Words that mean "read the clock" or "roll a die" in an output. Matched on word boundaries
# so `generated_at` and `uuid4` hit while `timestamped log line` only hits via `timestamp`.
_NONDETERMINISM = re.compile(
    r"\b("
    r"timestamps?|timestamped|generated_at|created_at|updated_at|generation[_ ]time"
    r"|iso[- ]?8601|utcnow|datetime\.now|time\.time|current time|wall[- ]clock|clock read"
    r"|random(?:ly|ness|ised|ized)?|uuid4?|nondeterministic|non-deterministic"
    r")\b",
    re.IGNORECASE,
)

# Places a timestamp is perfectly fine: they are not the deterministic artifacts. A log line,
# an HTTP header, or a tracker comment may carry a clock read without breaking anything.
_EXEMPT_SINKS = re.compile(
    r"\b(log(?:s|ged|ging|ger|-line|\sline|\smessage|\srecord)?|stderr|stdout\slog"
    r"|http\s*(?:response\s*)?header|response\s*header|header|audit\s*(?:log|row|record)"
    r"|jira|tracker|issue\s*comment|pr\s*(?:comment|body|description)|slack|notification"
    r"|database\s*(?:row|column)|db\s*row|metric|telemetry|trace)\b",
    re.IGNORECASE,
)

# Words that mean "this lands in what the surface emits" — the part invariant 2 pins down.
_OUTPUT_WORDS = re.compile(
    r"\b(output|outputs|json|report|artifact|artifacts|payload|meta|manifest|markdown|md"
    r"|file|files|document|snapshot|response|result|emit|emits|emitted|render|rendered"
    r"|write|writes|written|include|includes|including|contains?|carry|carries)\b",
    re.IGNORECASE,
)


def _surface_pattern(alias: str) -> str:
    """A regex for one surface alias, tolerating a plural ``s`` and a possessive.

    SSPN-31's own criterion is phrased entirely as output shape — it names the JSON key
    ``"regressions"`` and never the ``regression`` command — so an exact-word match found no
    surface and the check that was written for it returned PROCEED. The inflection rule is
    deliberately tiny (an optional ``'``/``’`` possessive, an optional trailing ``s``): a
    stemmer would start matching words nobody wrote, and the two-signal design (a
    nondeterminism word **and** a named surface) is what keeps `timestamp` alone harmless.
    """
    return rf"(?<![a-z0-9_]){re.escape(alias)}(?:['\u2019]s|s|es)?(?![a-z0-9_])"


def _named_surface(text: str) -> str | None:
    """The deterministic surface a criterion talks about, if any.

    Matches simple inflections of each alias, so ``regressions`` and ``the regression's
    output`` both resolve to the ``regression`` surface.
    """
    lowered = text.lower()
    for aliases in _DETERMINISTIC_SURFACES:
        for alias in aliases:
            if re.search(_surface_pattern(alias), lowered):
                return aliases[0]
    return None


def _check_invariants(spec: dict[str, Any]) -> list[Finding]:
    """A criterion that contradicts a documented repo invariant (SSPN-31).

    The run that produced this check invented *"`meta.generated_at` as an ISO-8601 UTC
    timestamp in the JSON output of `orchestrator regression --format json`"*. Nobody asked
    for it, and it contradicts invariant 2: those surfaces are byte-stable so their output can
    be diffed. An agent building to it would have shipped non-diffable output and passed its
    own tests. Provenance makes an invented criterion visible; this makes a dangerous one stop.

    Deterministic string matching, like every other check here — no LLM, no network. Asking a
    model whether a model invented a requirement is not an answer.
    """
    findings: list[Finding] = []
    for criterion in _all_criteria_text(spec):
        match = _NONDETERMINISM.search(criterion)
        if match is None:
            continue
        surface = _named_surface(criterion)
        if surface is None:
            continue
        # Determinism is a property of these outputs, not a ban on the word: a log line, an
        # HTTP header, or a tracker comment may carry a clock read.
        if _EXEMPT_SINKS.search(criterion) or not _OUTPUT_WORDS.search(criterion):
            continue
        findings.append(
            Finding(
                check="repo-invariant",
                detail=(
                    f"the criterion puts {match.group(0)!r} in {surface!r} output, which is a "
                    "deterministic surface — its output must be byte-stable so it can be diffed"
                ),
                evidence=f"{_INVARIANT_REF} — criterion: {criterion.strip()}",
            )
        )
    return findings


def _check_localization(spec: dict[str, Any], landing: list[str], issue_type: str) -> list[Finding]:
    """A Bug describes something that already exists. If nothing in the graph matches it, the
    run has nowhere to start, and guessing a fault site is the failure RCA exists to avoid."""
    if not is_bug(issue_type):
        return []
    if landing:
        return []
    return [
        Finding(
            check="localization",
            detail="nothing in the graph matches this bug's words, so there is no fault site to work from",
            evidence="add a traceback, a failing test, or the symbol involved",
        )
    ]


def _check_size(spec: dict[str, Any], files: list[str], max_criteria: int, max_files: int) -> list[Finding]:
    findings: list[Finding] = []
    criteria = _criteria_text(spec)
    if len(criteria) > max_criteria:
        findings.append(
            Finding(
                check="size",
                detail=f"{len(criteria)} acceptance criteria — more than one change's worth",
                evidence=f"cap is {max_criteria}; split the ticket before building it",
            )
        )
    if len(files) > max_files:
        findings.append(
            Finding(
                check="size",
                detail=f"the change lands in {len(files)} modules",
                evidence=f"cap is {max_files}",
            )
        )
    return findings


# Codegen shows the model the files a change names, capped by
# ``codegen._MAX_CONTEXT_BYTES``. Past that cap files are excerpted, and a model editing a
# file it has only partly seen quotes an anchor that does not exist — "edit 0 'find' text
# not found". A spec can be perfectly well-scoped by every other measure and still be
# unbuildable: `_check_size` counts criteria and modules, and neither correlates with bytes.
# One 56 KB file passes the module count and exceeds the whole budget alone.
#
# Two thresholds, because excerpting is not useless. Anchor-located windows handle a modest
# overflow, so the margin is a warning that rides along with PROCEED. Well past it, the
# excerpting is doing more guessing than showing, and that is worth refusing before a paid
# codegen cycle rather than after three failed runs.
_CONTEXT_REFUSE_RATIO = 1.5


def _named_file_bytes(root: Path, files: list[str]) -> tuple[int, list[str]]:
    """Total size of the files a change names, and the ones that could be measured."""
    total, seen = 0, []
    for ref in files:
        candidate = (root / ref).resolve()
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
                seen.append(ref)
        except OSError:  # unreadable is not a reason to block a run
            continue
    return total, seen


def _check_context_budget(files: list[str], root: Path | None, budget: int) -> tuple[list[Finding], bool]:
    """Whether the files this change names can actually be shown to codegen.

    Skipped entirely without a ``root``: measuring needs the tree, and a caller that has
    not got one should get exactly the verdicts it got before.
    """
    if root is None or not files or budget <= 0:
        return [], False
    total, measured = _named_file_bytes(root, files)
    if not measured or total <= budget:
        return [], False
    over = total / budget
    detail = (
        f"the {len(measured)} file(s) this change names total {total:,} bytes against "
        f"codegen's {budget:,}-byte context budget"
    )
    if over >= _CONTEXT_REFUSE_RATIO:
        return [
            Finding(
                check="context-budget",
                detail=f"{detail} — {over:.1f}x over",
                evidence=(
                    "codegen would see only part of each file and quote edit anchors from "
                    "text it was never shown; split the change so its files fit"
                ),
            )
        ], True
    return [
        Finding(
            check="context-budget",
            detail=f"{detail} — {over:.1f}x over, so files will be excerpted",
            evidence=(
                "anchor-located windows usually cope at this margin; a failed edit anchor "
                "is the symptom if they do not"
            ),
        )
    ], False


def _check_prior_runs(issue_key: str, runs: list[Any]) -> list[Finding]:
    """Another run already carried this ticket **to a PR**.

    The PR is the whole test. A run that finished without opening one changed nothing anyone
    else can see — a safe-mode rehearsal, or a live run that stopped short — and refusing the
    next attempt on that basis makes a ticket unworkable after its first dry run. This check
    was written to catch two branches and two PRs for one piece of work; without the PR
    condition it caught iteration instead, and did it to the first person who tried.
    """
    for record in runs:
        if not (record.issue_key and record.issue_key == issue_key and record.status == "done"):
            continue
        pr_url = getattr(record, "pr_url", "")
        if not pr_url:
            continue
        return [
            Finding(
                check="prior-run",
                detail=f"run {record.run_id} already carried this ticket to a PR",
                evidence=f"PR {pr_url}",
            )
        ]
    return []


def assess(
    spec: dict[str, Any],
    *,
    store: FactStore,
    landing: list[str] | None = None,
    issue_type: str = "",
    issue_key: str = "",
    prior_runs: list[Any] | None = None,
    max_criteria: int = DEFAULT_MAX_CRITERIA,
    max_files: int = DEFAULT_MAX_FILES,
    root: Path | str | None = None,
    context_budget: int = 0,
    criteria: Any = None,
) -> Assessment:
    """Judge one ticket against the code. Deterministic; the graph answers, not a model.

    ``landing`` is where the investigation says this ticket lands — passed in rather than
    recomputed so the gate and the brief cannot disagree.

    ``criteria`` is a ``CriteriaBinding`` (Phase 2a). It arrives here rather than parking the
    run from `autorun` so there is **one gate with one verdict**: an unbound criterion is a
    false premise in exactly the way a false count is, and a second refusal path would give the
    parity gate two answers to compare.
    """
    landing = landing or []
    findings: list[Finding] = []

    duplicate = _check_prior_runs(issue_key, prior_runs or [])
    if duplicate:
        return Assessment(verdict=Verdict.DUPLICATE, findings=duplicate)

    breaks_invariant = _check_invariants(spec)
    if breaks_invariant:
        # Same reason as a false countable claim: code built to a criterion that contradicts
        # the repo's own rules passes its own tests and is still wrong.
        return Assessment(verdict=Verdict.CRITERIA_WRONG, findings=breaks_invariant)

    wrong = _check_countable_claims(spec, store)
    if wrong:
        # Ordered first because it is the one failure that makes every later stage pointless:
        # code built to a false premise passes its own tests and is still wrong.
        return Assessment(verdict=Verdict.CRITERIA_WRONG, findings=wrong)

    unbound = _check_unbound_criteria(criteria, issue_type)
    if unbound and is_bug(issue_type):
        # For a bug, the same family as a false count and ordered beside it: a criterion nobody
        # can locate is a test nobody can write, so anything built to it is graded by a test
        # that had to invent its own subject.
        return Assessment(verdict=Verdict.CRITERIA_WRONG, findings=unbound)
    # For anything else it is reported and the run continues. An enhancement's criterion names
    # what the run is about to build, so refusing it means the *better-written* criterion is
    # the one that parks the ticket:
    #
    #     the compiler returns >= 70 rules        → no claim → PROCEED
    #     `rule_compiler` returns >= 70 rules     → unbound  → parked
    #
    # The second is more precise and more testable. `_check_localization` two functions above
    # already takes this position — an enhancement lands nowhere in the graph because it has
    # not been built — and one gate holding two stances on one question is the defect here.
    # Riding along with PROCEED is what `_check_context_budget` already does below.
    findings.extend(unbound)

    unlocalized = _check_localization(spec, landing, issue_type)
    if unlocalized:
        return Assessment(verdict=Verdict.UNLOCALIZED, findings=unlocalized)

    oversized = _check_size(spec, landing, max_criteria, max_files)
    if oversized:
        return Assessment(verdict=Verdict.TOO_BIG, findings=oversized)

    # Last, and only sometimes fatal: a change can be right-sized by every other measure and
    # still not fit in front of the model. Under the refuse ratio this rides along with
    # PROCEED so a run that would have worked still runs.
    over_budget, unbuildable = _check_context_budget(landing, Path(root) if root else None, context_budget)
    if unbuildable:
        return Assessment(verdict=Verdict.TOO_BIG, findings=over_budget)

    return Assessment(verdict=Verdict.PROCEED, findings=findings + over_budget)


__all__ = [
    "DEFAULT_MAX_CRITERIA",
    "DEFAULT_MAX_FILES",
    "Assessment",
    "Finding",
    "Verdict",
    "assess",
]
