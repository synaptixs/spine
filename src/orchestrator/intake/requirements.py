"""The requirements check: a deterministic skeleton, a clarity gate, recorded answers.

Spine does not author requirements. Whoever drafts them — a person, Claude in a chat, a
Confluence page — this module runs the strict clarity gate over the result, reports what the
code already says about it, and records the answers people give, with the channel Spine
actually observed. **No model is called anywhere in here** (D31).

Three operations, all over an OpenSpec change on disk:

``skeleton``
    One sentence in, a change out. Every why-field is an open question in fixed wording (D22),
    so the same sentence always yields the same questions.
``check_intent`` / ``code_check``
    The strict gate (``gaps.STRICT_GAP_RULES``), and what a repository says about the change's
    criteria. The second half takes a ``DraftGrounding`` the *caller* computed — the CLI is the
    only layer that may read a repository and an intake plan, which keeps ``intake`` free of an
    import of ``sdlc`` (see ``cli/build._grounding_for``).
``record_answers``
    An answer or a deferral written into ``proposal.md``. The edit is **surgical**: a change a
    human drafted keeps its prose, its Impact and its Grounding exactly as they were; only the
    answered question's nested bullet, and the why-field a fixed question maps to, move.

Answers are recorded under the question's exact text (D24). A question that was reworded after
it was answered shows up as an *orphaned* resolution in ``check`` — never silently dropped.

**The four fixed why-questions are always accepted** (SSPN-126). A change ``draft --source`` wrote,
or one a person wrote, asks none of them, so before this its why could only be supplied by
hand-editing ``proposal.md``. Answering one that is not open first appends it — to the questions
section the reader reads, else to a new ``## Open Questions`` before ``## Grounding`` — and then
records it like any other, with its provenance; ``check`` points each failing why-rule at that
command. Any other unknown question is still an error: a typo must not become a question.
"""

from __future__ import annotations

import datetime
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from orchestrator.intake import options as _options
from orchestrator.intake.atomic import write_atomic as _write_atomic
from orchestrator.intake.gaps import (
    STRICT_GAP_RULES,
    GapAnalyzer,
    GapFinding,
    GapRule,
    GapSeverity,
    blocks_approval,
)
from orchestrator.intake.intents import Intent, Resolution, _slug, question_states
from orchestrator.intake.openspec_source import _H2, _fence_mask, _parse_resolution, change_to_intent
from orchestrator.intake.openspec_writer import PLACEHOLDER_CRITERION, _resolution_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.intake.pkg_evidence import DraftGrounding

__all__ = [
    "CHOICE_QUESTION",
    "FIELD_QUESTIONS",
    "STRICT_GAP_RULES",
    "Channel",
    "AnswerRequest",
    "CheckReport",
    "CodeCheck",
    "LoadedChange",
    "RequirementsError",
    "check_intent",
    "code_check",
    "load_answers_file",
    "load_change",
    "record_answers",
    "skeleton",
]

Channel = Literal["cli", "answers-file", "mcp"]

#: The fixed questions a skeleton asks, and the why-field each one's answer fills. Fixed wording
#: is the point (D22): the same idea gives the same questions, and an answer to one of these is
#: recognised by its text alone.
FIELD_QUESTIONS: dict[str, str] = {
    "What problem does this solve?": "problem",
    "Who has this problem?": "users",
    "How will we know it worked? Describe an outcome someone can observe.": "outcome",
    "What is explicitly out of scope?": "non_goals",
}
_LIST_FIELDS = {"users", "non_goals"}
_FIELD_HEADING = {"problem": "Problem", "users": "Users", "outcome": "Outcome"}
_CANONICAL = ("problem", "users", "outcome")
# What an answer's origin is, given where it arrived (§12): over MCP Spine cannot see who typed
# it, so it says "relayed" and never claims a person it did not observe.
_ORIGIN: dict[str, Literal["user", "relayed"]] = {"cli": "user", "answers-file": "user", "mcp": "relayed"}
# The strict why-rules a fixed question answers, by rule id: `check` points each failing one at the
# answer that fixes it. Keyed by id, so a custom rule set that names its rules otherwise gets none.
_RULE_FIELD = {
    "problem_stated": "problem",
    "users_named": "users",
    "outcome_stated": "outcome",
    "non_goal_named": "non_goals",
}
_FIELD_QUESTION = {f: q for q, f in FIELD_QUESTIONS.items()}
#: The fixed question a change with `## Options` must have answered (D9): which option is taken. Its
#: answer is recorded like any other — with provenance, in `proposal.md` — and shown in `## Decision`.
CHOICE_QUESTION = "Which option do we take?"
_DEFAULT_ROOT = "openspec"


class RequirementsError(ValueError):
    """Something the caller got wrong — a missing change, an unknown question. Never a defect."""


# --- the skeleton -----------------------------------------------------------------------------


def skeleton(idea: str) -> Intent:
    """A change-shaped intent from one sentence. Deterministic: the same sentence, the same intent."""
    text = " ".join(idea.split())
    if not text:
        raise RequirementsError("an idea needs at least one word")
    slug = "-".join(_slug(text).split("-")[:8]).strip("-") or "idea"
    return Intent(
        id=f"intent-{slug}",
        title=text if len(text) <= 80 else text[:77].rstrip() + "...",
        description=text,
        open_questions=list(FIELD_QUESTIONS),
        idea_id=f"idea-{slug}",
    )


# --- loading a change -------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedChange:
    change_id: str
    directory: Path
    proposal_path: Path
    proposal_md: str
    intent: Intent

    @property
    def design_path(self) -> Path:
        """Where the change's ``design.md`` is, whether or not it exists. Read only by the code that
        needs it (``options``), so a change whose design cannot be read still checks as before."""
        return self.directory / "design.md"


def load_change(change: str, *, root: str | Path = "openspec") -> LoadedChange:
    """A change by id (under ``<root>/changes/``) or by the path of its directory.

    Reads exactly what ``openspec://`` reads, so a change is checked the way it would be built —
    however it was drafted."""
    candidate = Path(change)
    directory = candidate if (candidate / "proposal.md").is_file() else Path(root) / "changes" / change
    proposal = directory / "proposal.md"
    if not proposal.is_file():
        raise RequirementsError(f"no change {change!r}: {proposal} does not exist")
    md = proposal.read_bytes().decode("utf-8")
    specs = tuple(p.read_text(encoding="utf-8") for p in sorted((directory / "specs").rglob("spec.md")))
    change_id = directory.name
    return LoadedChange(
        change_id,
        directory,
        proposal,
        md,
        change_to_intent(change_id, proposal_md=md.replace("\r\n", "\n"), spec_texts=specs),
    )


# --- the check --------------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeCheck:
    """What the repository says about a change's criteria (D10). Facts the graph states; it never
    judges whether a criterion is *met* — only that it names code that exists, or does not."""

    state: str
    where: str
    already_exist: tuple[dict[str, Any], ...] = ()
    names_not_found: tuple[dict[str, Any], ...] = ()
    no_claim: int = 0
    tree_checked: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "grounding": self.state,
            "where": self.where,
            "criteria_naming_existing_code": list(self.already_exist),
            "criteria_naming_unfound_code": list(self.names_not_found),
            "criteria_with_no_claim": self.no_claim,
            "tree_checked": self.tree_checked,
        }


def code_check(grounding: DraftGrounding) -> CodeCheck:
    """A change's criteria against the code, from a grounding the caller read."""
    binding = grounding.binding
    if not grounding.cites or binding is None:
        # Said out loud, like the drafted banner: no repository, an empty graph, or no binding —
        # in none of which may "no findings" be read as "checked and clean".
        return CodeCheck(state=grounding.state, where=grounding.where)
    return CodeCheck(
        state=grounding.state,
        where=grounding.where,
        already_exist=tuple(
            {"criterion": r.text, "anchors": [{"symbol": a.symbol, "where": a.where} for a in r.anchors]}
            for r in binding.bound
        ),
        names_not_found=tuple({"criterion": r.text, "unresolved": list(r.claims)} for r in binding.unbound),
        no_claim=len(binding.no_claim),
        tree_checked=grounding.tree_checked,
    )


@dataclass(frozen=True)
class CheckReport:
    change_id: str
    findings: tuple[GapFinding, ...]
    answered: tuple[str, ...]
    deferred: tuple[str, ...]
    unresolved: tuple[str, ...]
    orphaned: tuple[str, ...]
    code: CodeCheck | None = None
    notes: tuple[str, ...] = field(default=())
    #: ``orchestrator openspec answer <change> [--root R]`` — the start of each ``fix`` line.
    answer_cli: tuple[str, ...] = ()

    @property
    def passes(self) -> bool:
        """The gate: no blocker and no needs-input finding. Warnings never fail it."""
        return not blocks_approval(list(self.findings))

    def _item(self, f: GapFinding) -> dict[str, Any]:
        item: dict[str, Any] = {"rule": f.rule_id, "severity": f.severity.value, "message": f.message}
        field_name = _RULE_FIELD.get(f.rule_id)
        question = _FIELD_QUESTION[field_name] if field_name else ""
        placeholder = "<your answer>"
        if f.rule_id == "option_chosen":
            question, placeholder = CHOICE_QUESTION, "<option number or title>"
        if question:
            # A user who cannot see the way out stalls as surely as one with none (D5): name the
            # exact command, runnable as printed, and the tool an agent would call (D10).
            argv = self.answer_cli or ("orchestrator", "openspec", "answer", self.change_id)
            item["fix"] = {
                "question": question,
                "cli": shlex.join([*argv, "--question", question, "--answer", placeholder]),
                "mcp": {"tool": "requirements_answer", "question": question},
            }
        return item

    def to_dict(self) -> dict[str, Any]:
        return {
            "change": self.change_id,
            "passes": self.passes,
            "open_items": [self._item(f) for f in self.findings],
            "questions": {
                "answered": list(self.answered),
                "deferred": list(self.deferred),
                "unresolved": list(self.unresolved),
            },
            "orphaned_resolutions": list(self.orphaned),
            "code_check": self.code.to_dict() if self.code else None,
            "notes": list(self.notes),
        }


_SEVERITY_ORDER = {"blocker": 0, "needs_input": 1, "warning": 2}


def check_intent(
    intent: Intent,
    *,
    change_id: str = "",
    rules: tuple[GapRule, ...] | list[GapRule] = STRICT_GAP_RULES,
    code: CodeCheck | None = None,
    change_ref: str = "",
    root: str = _DEFAULT_ROOT,
    options: _options.OptionsBlock | None = None,
) -> CheckReport:
    """The strict clarity gate over one intent, with open items ordered: blockers first, then
    what needs a human, then warnings — rule order within each, so the same input reads the same.

    ``change_ref`` and ``root`` are what the caller was given to find the change — an id under a
    root, or a directory path — so each why-rule's ``fix`` line runs as printed.

    ``options`` is the change's ``## Options`` block (``options.load_options``). When it holds
    options, the gate also needs one chosen (``option_chosen``, D9); when it is absent or ``None``,
    the check is exactly what it was."""
    found = GapAnalyzer(list(rules)).analyze([intent])
    chosen = options is not None and options.present and bool(options.options)
    if chosen and options is not None and (problem := _choice_problem(intent, options.options)):
        found.append(GapFinding("option_chosen", intent.id, GapSeverity.NEEDS_INPUT, problem))
    findings = sorted(found, key=lambda f: _SEVERITY_ORDER[f.severity.value])
    answered, deferred, unresolved = question_states(intent)
    orphaned = tuple(q for q in intent.resolutions if q not in intent.open_questions)
    notes: list[str] = []
    if deferred:
        notes.append(
            "A deferral with an owner satisfies the question gate; it does not supply the missing why."
        )
    if any(PLACEHOLDER_CRITERION in c for c in intent.acceptance_criteria):
        notes.append(
            "The delta spec still carries the placeholder scenario a skeleton is written with — "
            "no real acceptance criteria have been written, and the gate has no rule that requires any."
        )
    elif not intent.acceptance_criteria:
        notes.append("This change states no acceptance criteria; the gate has no rule that requires any.")
    # D2: a relayed why counts, and is said out loud — Spine saw an agent's channel, not a person.
    # Only a field that is still filled is named: one deleted by hand since no longer counts.
    by_field = {
        FIELD_QUESTIONS[fixed]: res
        for q in intent.open_questions
        if (fixed := _fixed_question(q)) and (res := intent.resolutions.get(q)) is not None
    }
    relayed = [
        name.replace("_", "-")
        for name in FIELD_QUESTIONS.values()
        if (res := by_field.get(name)) is not None
        and res.status == "answered"
        and res.origin == "relayed"
        and getattr(intent, name)
    ]
    if relayed:
        notes.append(
            f"The {_and(relayed)} came by relay (MCP): it counts for the gate, but Spine did not "
            "see who wrote it."
        )
    if options is not None and options.problems:
        shown = "; ".join(options.problems[:3])
        more = f" (and {len(options.problems) - 3} more)" if len(options.problems) > 3 else ""
        notes.append(f"`## Options` in design.md has {len(options.problems)} problem(s): {shown}{more}")
    ref = change_ref or change_id or intent.id
    return CheckReport(
        change_id=change_id or intent.id,
        findings=tuple(findings),
        answered=tuple(answered),
        deferred=tuple(deferred),
        unresolved=tuple(unresolved),
        orphaned=orphaned,
        code=code,
        notes=tuple(notes),
        answer_cli=(
            "orchestrator",
            "openspec",
            "answer",
            ref,
            *(("--root", root) if root and root != _DEFAULT_ROOT else ()),
        ),
    )


# --- recording answers ------------------------------------------------------------------------


@dataclass(frozen=True)
class AnswerRequest:
    """One answer, or one deferral, to one open question."""

    question: str
    answer: str = ""
    defer_to: str = ""


def _text(path: str | Path, i: int, row: dict[str, Any], key: str) -> str:
    """A string value from an answers row. YAML turns `yes` / `no` / `1` / a list into a bool, a
    number or a list — recording `True` as somebody's answer would be worse than refusing."""
    value = row.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RequirementsError(
            f"{path}: answers[{i}].{key} must be text, not {type(value).__name__} — quote it "
            "(YAML reads an unquoted yes/no/1 as a boolean or a number)"
        )
    return value


def load_answers_file(path: str | Path) -> list[AnswerRequest]:
    """``answers:`` as a list of ``{question, answer | defer_to}`` — nothing else."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RequirementsError(f"cannot read answers file {path}: {exc}") from exc
    rows = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RequirementsError(
            f"{path}: expected a non-empty `answers:` list of {{question, answer|defer_to}}"
        )
    out: list[AnswerRequest] = []
    for i, row in enumerate(rows, 1):
        if (
            not isinstance(row, dict)
            or set(row) - {"question", "answer", "defer_to"}
            or not row.get("question")
        ):
            raise RequirementsError(
                f"{path}: answers[{i}] must be {{question, answer}} or {{question, defer_to}}"
            )
        out.append(
            AnswerRequest(
                question=_text(path, i, row, "question"),
                answer=_text(path, i, row, "answer"),
                defer_to=_text(path, i, row, "defer_to"),
            )
        )
    return out


def _match(question: str, open_questions: list[str]) -> str:
    """The open question this text names: exact, or the one that matches ignoring case and
    surrounding whitespace. Anything else is an error that lists what *is* open."""
    if question in open_questions:
        return question
    folded = [q for q in open_questions if " ".join(q.split()).lower() == " ".join(question.split()).lower()]
    if len(folded) == 1:
        return folded[0]
    shown = "\n".join(f"  - {q}" for q in open_questions) or "  (none)"
    raise RequirementsError(f"no open question matches {question!r}. Open questions:\n{shown}")


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _fold(text: str) -> str:
    return " ".join(text.split()).lower()


def _choice_problem(intent: Intent, options: tuple[_options.Option, ...]) -> str:
    """Why the change's options are not yet chosen between — or ``""`` when one is."""
    question = next((q for q in intent.open_questions if _fold(q) == _fold(CHOICE_QUESTION)), "")
    res = intent.resolutions.get(question) if question else None
    if res is None or res.status != "answered":
        return (
            f"This change has {len(options)} option(s) in design.md and none is chosen: answer "
            f"{CHOICE_QUESTION!r} with an option's number or title."
        )
    if _options.resolve_choice(options, res.answer) is None:
        return (
            f"The recorded choice {res.answer!r} names an option `## Options` no longer has — choose again."
        )
    return ""


def _fixed_question(question: str) -> str:
    """The fixed why-question this text names, ignoring case and spacing — or ``""``."""
    return next((q for q in FIELD_QUESTIONS if _fold(q) == _fold(question)), "")


def _owner(raw: str) -> str:
    """A deferral's owner: one name, as ``@name`` reads back. Whitespace would end the token the
    reader looks for, and a line break would write structure into the file."""
    owner = raw.strip().lstrip("@").strip()
    if not owner or len(owner.split()) != 1:
        raise RequirementsError(f"{raw!r}: a deferral names exactly one owner, e.g. --defer-to @finance-lead")
    return owner


def _heading_safe(value: str | list[str], question: str) -> None:
    """A why-field answer is written as lines of ``proposal.md``; one that begins with ``#`` would
    start a heading of its own (and could open a second ``## Open Questions``), and one that begins
    with a code fence would hide every heading after it from the reader."""
    lines = value if isinstance(value, list) else value.split("\n")
    stripped = [line.lstrip() for line in lines]
    if any(line.startswith("#") for line in stripped):
        raise RequirementsError(
            f"{question!r}: an answer cannot start a line with '#' — it would become a heading"
        )
    if any(line.startswith(("```", "~~~")) for line in stripped):
        raise RequirementsError(
            f"{question!r}: an answer cannot start a line with a code fence (``` or ~~~) — it would "
            "hide the rest of the proposal from the reader"
        )


#: The placeholder ``check``'s ``fix`` line carries. Refused as an answer, so running the printed
#: line without filling it in can never pass the gate on text nobody wrote.
_PLACEHOLDER = re.compile(r"<[^<>\n]*>")


def record_answers(
    change: LoadedChange,
    requests: list[AnswerRequest],
    *,
    channel: Channel = "cli",
    at: str = "",
) -> dict[str, Any]:
    """Write each answer or deferral into ``proposal.md``. All-or-nothing: every question is
    matched, validated and placed in memory before the file is touched, and the file is replaced
    atomically — a typo in the fifth leaves it untouched, and a failure never leaves half of it.

    One of the four fixed why-questions is accepted even when it is not open: it is appended
    first (``_ensure_question``), in its fixed wording, then answered like any other (D1, D8).
    So is ``CHOICE_QUESTION`` when ``design.md`` has options: its answer must name one, is recorded
    as that option's ``Option <n> — <title>``, and ``## Decision`` shows it (D9)."""
    if not requests:
        raise RequirementsError("nothing to record")
    when = at or datetime.date.today().isoformat()
    open_questions = list(change.intent.open_questions)
    appended: list[str] = []
    resolved: list[tuple[str, Resolution, str]] = []
    decision: tuple[str, Resolution] | None = None
    block: _options.OptionsBlock | None = None
    for req in requests:
        choosing = _fold(req.question) == _fold(CHOICE_QUESTION)
        if choosing and block is None:
            try:
                block = _options.load_options(change)
            except _options.OptionsError as exc:
                raise RequirementsError(str(exc)) from exc
            if not (block.present and block.options):
                raise RequirementsError(
                    f"{req.question!r}: this change has no `## Options` in design.md to choose from"
                )
        if bool(req.answer.strip()) == bool(req.defer_to.strip()):
            raise RequirementsError(f"{req.question!r}: give exactly one of an answer or --defer-to")
        if _PLACEHOLDER.fullmatch(req.answer.strip()):
            raise RequirementsError(
                f"{req.question!r}: {req.answer.strip()!r} is the placeholder from `openspec check` — "
                "replace it with the answer"
            )
        try:
            question = _match(req.question, open_questions)
        except RequirementsError as exc:
            # Appended only when nothing open folds to it: two open spellings of one fixed question
            # are ambiguous, and a third copy would hide that.
            ambiguous = any(_fold(q) == _fold(req.question) for q in open_questions)
            question = "" if ambiguous else (CHOICE_QUESTION if choosing else _fixed_question(req.question))
            if not question:
                always = "\n".join(f"  - {q}" for q in FIELD_QUESTIONS)
                raise RequirementsError(
                    f"{exc}\nThe four why-questions are always accepted, open or not:\n{always}"
                ) from None
            open_questions.append(question)
            appended.append(question)
        answer = " ".join(req.answer.split())
        if choosing and answer and block is not None:
            chosen = _options.resolve_choice(block.options, answer)
            if chosen is None:
                shown = "\n".join(f"  - {_options.label(block.options, o)}" for o in block.options)
                raise RequirementsError(f"{answer!r} names none of this change's options:\n{shown}")
            answer = _options.label(block.options, chosen)
        if answer:
            res = Resolution(
                status="answered", answer=answer, origin=_ORIGIN[channel], channel=channel, at=when
            )
        else:
            res = Resolution(status="deferred", owner=_owner(req.defer_to), at=when)
        if choosing:
            decision = (answer, res) if answer else None
        resolved.append((question, res, req.answer))
    crlf = "\r\n" in change.proposal_md
    md = change.proposal_md.replace("\r\n", "\n")
    for question in appended:
        md = _ensure_question(md, question)
    for question, res, raw in resolved:
        md = _record_resolution(md, question, res)
        # By the fixed wording, not the file's: a question the file spells in another case is still
        # that question (D8), and its answer still fills the field.
        field_name = FIELD_QUESTIONS.get(_fixed_question(question))
        if field_name and res.status == "answered":
            value: str | list[str] = _split_list(raw) if field_name in _LIST_FIELDS else res.answer
            _heading_safe(value, question)
            md = _set_field(md, field_name, value)
    design = ""
    if decision is not None:
        chosen_label, res = decision
        current = change.design_path.read_bytes().decode("utf-8")
        recorded = (
            f"Recorded by `openspec answer` ({res.origin} · {res.channel} · {res.at}); the record is the "
            f"answer under {CHOICE_QUESTION!r} in proposal.md."
        )
        design = _options.set_decision(current, chosen_label, recorded)
    _write_atomic(change.proposal_path, md.replace("\n", "\r\n") if crlf else md)
    if design:
        # proposal.md holds the record the gate reads; design.md shows it beside the options.
        _write_atomic(change.design_path, design)
    return {
        "change": change.change_id,
        "recorded": [
            {
                "question": q,
                "status": r.status,
                "origin": r.origin if r.status == "answered" else "",
                "channel": channel,
            }
            for q, r, _ in resolved
        ],
    }


def _split_list(text: str) -> list[str]:
    parts = [p.strip() for chunk in text.replace("\r", "").split("\n") for p in chunk.split(";")]
    return [p for p in (p.lstrip("-* ").strip() for p in parts) if p]


# --- surgical markdown edits ------------------------------------------------------------------


def _h2_blocks(md: str) -> list[list[Any]]:
    """``[[heading | None, [lines…]], …]`` — the preamble first (when there is one), then one block
    per ``## ``. Split exactly where the reader splits, so the editor and the reader agree."""
    blocks: list[list[Any]] = []
    current: list[Any] = [None, []]
    for line in md.split("\n"):
        m = _H2.match(line)
        if m:
            blocks.append(current)
            current = [m.group(1).strip(), [line]]
        else:
            current[1].append(line)
    blocks.append(current)
    return [b for b in blocks if b[1]]


def _join(blocks: list[list[Any]], *, trailing_newline: bool) -> str:
    text = "\n".join("\n".join(b[1]) for b in blocks)
    return text if not trailing_newline else text.rstrip("\n") + "\n"


def _bullet_text(line: str) -> str:
    return line.strip().lstrip("-*").strip()


def _questions_block(blocks: list[list[Any]]) -> list[Any] | None:
    """The block the reader reads questions from: ``_h2_sections`` keeps the **last** section of a
    given name, and ``_first_section`` takes ``open questions`` before ``questions``, but only if
    it is non-empty."""
    for name in ("open questions", "questions"):
        found = [b for b in blocks if b[0] and b[0].lower() == name]
        if found and "\n".join(found[-1][1][1:]).strip():
            return found[-1]
    return None


def _ensure_question(md: str, question: str) -> str:
    """Append ``question`` where the reader will read it (D9): after the last line of the section
    ``_questions_block`` picks; else into the last empty ``## Open Questions`` (then ``##
    Questions``), the one the reader keeps once it has a line; else into a new ``## Open
    Questions`` before ``## Grounding`` — fenced off and last by design — or at the end."""
    blocks = _h2_blocks(md)
    block = _questions_block(blocks) or next(
        (
            b
            for name in ("open questions", "questions")
            for b in reversed(blocks)
            if b[0] and b[0].lower() == name
        ),
        None,
    )
    if block is not None:
        lines: list[str] = block[1]
        last = max(n for n, line in enumerate(lines) if line.strip())
        lines.insert(last + 1, f"- {question}")
        return _join(blocks, trailing_newline=md.endswith("\n"))
    at = next((n for n, b in enumerate(blocks) if b[0] and b[0].lower() == "grounding"), len(blocks))
    new: list[Any] = ["Open Questions", ["## Open Questions", f"- {question}"]]
    if at < len(blocks) or md.endswith("\n"):
        new[1].append("")
    if at and blocks[at - 1][1][-1].strip():
        blocks[at - 1][1].append("")
    blocks.insert(at, new)
    return _join(blocks, trailing_newline=md.endswith("\n"))


def _record_resolution(md: str, question: str, res: Resolution) -> str:
    """Put ``res`` directly under ``question`` in the section the reader reads, replacing any
    resolution already nested there (blank lines between are looked through, as the reader does).
    Everything else in the file is left byte-for-byte alone. Raises if the question was not
    found where the reader would find it — never a silent success that wrote nothing."""
    blocks = _h2_blocks(md)
    block = _questions_block(blocks)
    if block is None:
        raise RequirementsError("this change has no non-empty `## Open Questions` section")
    lines: list[str] = block[1]
    out: list[str] = [lines[0]]
    i, current, placed = 1, "", False
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip():
            out.append(line)
            continue
        text = _bullet_text(line)
        # The reader's own rule: an indented line under a question that parses as a resolution
        # belongs to that question; every other non-empty line is a question.
        if line[:1] in " \t" and current and _parse_resolution(text) is not None:
            if placed and current == question:
                continue  # an older answer to this question: replaced
            out.append(line)
            continue
        current = text
        out.append(line)
        if not placed and text == question:
            placed = True
            out.append(_resolution_line(res))
    if not placed:
        raise RequirementsError(
            f"could not place the answer: {question!r} is not in the Open Questions section"
        )
    block[1] = out
    return _join(blocks, trailing_newline=md.endswith("\n"))


def _lines_in_fence(lines: list[str]) -> list[bool]:
    return _fence_mask("\n".join(lines))


def _set_subsection(
    md: str, section: str, name: str, text: str, *, before: tuple[str, ...] = (), after: tuple[str, ...] = ()
) -> str:
    """Set ``### name`` under ``## section`` in place. An existing subsection has only its body
    replaced; every other line — lead text, other subsections, blank lines — stays where it was.
    A new one goes before the first of ``before`` that exists, else after the last of ``after`` that
    exists, else at the end of the section. A
    missing section is created: ``Why`` first, any other directly after ``Why``."""
    blocks = _h2_blocks(md)
    # The last section of the name, as the reader keeps (``_h2_sections`` overwrites by name).
    idx = next(
        (n for n, b in reversed(list(enumerate(blocks))) if b[0] and b[0].lower() == section.lower()), None
    )
    if idx is None:
        new = [section, [f"## {section}", "", f"### {name}", *text.split("\n"), ""]]
        why = next((n for n, b in enumerate(blocks) if b[0] and b[0].lower() == "why"), None)
        at = 1 if section.lower() == "why" or why is None else why + 1
        blocks.insert(min(at, len(blocks)), new)
        return _join(blocks, trailing_newline=True)
    lines: list[str] = blocks[idx][1]
    mask = _lines_in_fence(lines)
    starts = [
        (n, m.group(1).strip())
        for n, ln in enumerate(lines)
        if not mask[n] and (m := re.match(r"^###\s+(.+?)\s*$", ln))
    ]

    def span(k: int) -> tuple[int, int]:
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        return starts[k][0], end

    body = text.split("\n")
    for k, (_, sub) in enumerate(starts):
        if sub.lower() == name.lower():
            lo, hi = span(k)
            tail = [""] if hi > lo + 1 and not lines[hi - 1].strip() else []
            lines[lo:hi] = [lines[lo], *body, *tail]
            return _join(blocks, trailing_newline=md.endswith("\n"))
    insert_at = len(lines)
    while insert_at > 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    later = [k for k, (_, sub) in enumerate(starts) if sub.lower() in before]
    earlier = [k for k, (_, sub) in enumerate(starts) if sub.lower() in after]
    if later:
        insert_at = starts[later[0]][0]
    elif earlier:
        insert_at = span(earlier[-1])[1]
        while insert_at > 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
    lines[insert_at:insert_at] = (
        [""] if insert_at > 1 and lines[insert_at - 1].strip() and not starts else []
    ) + [
        f"### {name}",
        *body,
    ]
    return _join(blocks, trailing_newline=md.endswith("\n"))


def _set_field(md: str, field_name: str, value: str | list[str]) -> str:
    """Fill one why-field in the layout the OpenSpec writer uses (§3.3)."""
    if field_name == "non_goals":
        return _set_subsection(md, "What Changes", "Non-goals", "\n".join(f"- {v}" for v in value))
    text = "\n".join(f"- {v}" for v in value) if isinstance(value, list) else value
    order = ("problem", "users", "outcome")
    here = order.index(field_name)
    return _set_subsection(
        md, "Why", _FIELD_HEADING[field_name], text, before=order[here + 1 :], after=order[:here]
    )
