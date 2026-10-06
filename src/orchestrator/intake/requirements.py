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
"""

from __future__ import annotations

import contextlib
import datetime
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from orchestrator.intake.gaps import STRICT_GAP_RULES, GapAnalyzer, GapFinding, GapRule, blocks_approval
from orchestrator.intake.intents import Intent, Resolution, _slug, question_states
from orchestrator.intake.openspec_source import _H2, _fence_mask, _parse_resolution, change_to_intent
from orchestrator.intake.openspec_writer import PLACEHOLDER_CRITERION, _resolution_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.intake.pkg_evidence import DraftGrounding

__all__ = [
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

    @property
    def passes(self) -> bool:
        """The gate: no blocker and no needs-input finding. Warnings never fail it."""
        return not blocks_approval(list(self.findings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "change": self.change_id,
            "passes": self.passes,
            "open_items": [
                {"rule": f.rule_id, "severity": f.severity.value, "message": f.message} for f in self.findings
            ],
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
) -> CheckReport:
    """The strict clarity gate over one intent, with open items ordered: blockers first, then
    what needs a human, then warnings — rule order within each, so the same input reads the same."""
    findings = sorted(
        GapAnalyzer(list(rules)).analyze([intent]),
        key=lambda f: _SEVERITY_ORDER[f.severity.value],
    )
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
    return CheckReport(
        change_id=change_id or intent.id,
        findings=tuple(findings),
        answered=tuple(answered),
        deferred=tuple(deferred),
        unresolved=tuple(unresolved),
        orphaned=orphaned,
        code=code,
        notes=tuple(notes),
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


def _owner(raw: str) -> str:
    """A deferral's owner: one name, as ``@name`` reads back. Whitespace would end the token the
    reader looks for, and a line break would write structure into the file."""
    owner = raw.strip().lstrip("@").strip()
    if not owner or len(owner.split()) != 1:
        raise RequirementsError(f"{raw!r}: a deferral names exactly one owner, e.g. --defer-to @finance-lead")
    return owner


def _heading_safe(value: str | list[str], question: str) -> None:
    """A why-field answer is written as lines of ``proposal.md``; one that begins with ``#`` would
    start a heading of its own (and could open a second ``## Open Questions``)."""
    lines = value if isinstance(value, list) else value.split("\n")
    if any(line.lstrip().startswith("#") for line in lines):
        raise RequirementsError(
            f"{question!r}: an answer cannot start a line with '#' — it would become a heading"
        )


def record_answers(
    change: LoadedChange,
    requests: list[AnswerRequest],
    *,
    channel: Channel = "cli",
    at: str = "",
) -> dict[str, Any]:
    """Write each answer or deferral into ``proposal.md``. All-or-nothing: every question is
    matched, validated and placed in memory before the file is touched, and the file is replaced
    atomically — a typo in the fifth leaves it untouched, and a failure never leaves half of it."""
    if not requests:
        raise RequirementsError("nothing to record")
    when = at or datetime.date.today().isoformat()
    open_questions = change.intent.open_questions
    resolved: list[tuple[str, Resolution, str]] = []
    for req in requests:
        if bool(req.answer.strip()) == bool(req.defer_to.strip()):
            raise RequirementsError(f"{req.question!r}: give exactly one of an answer or --defer-to")
        question = _match(req.question, open_questions)
        if req.answer.strip():
            res = Resolution(
                status="answered",
                answer=" ".join(req.answer.split()),
                origin=_ORIGIN[channel],
                channel=channel,
                at=when,
            )
        else:
            res = Resolution(status="deferred", owner=_owner(req.defer_to), at=when)
        resolved.append((question, res, req.answer))
    crlf = "\r\n" in change.proposal_md
    md = change.proposal_md.replace("\r\n", "\n")
    for question, res, raw in resolved:
        md = _record_resolution(md, question, res)
        field_name = FIELD_QUESTIONS.get(question)
        if field_name and res.status == "answered":
            value: str | list[str] = _split_list(raw) if field_name in _LIST_FIELDS else res.answer
            _heading_safe(value, question)
            md = _set_field(md, field_name, value)
    _write_atomic(change.proposal_path, md.replace("\n", "\r\n") if crlf else md)
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


def _write_atomic(path: Path, text: str) -> None:
    """Replace ``path`` in one step, in the same directory, never through a symlink: a link at
    ``proposal.md`` is replaced by the new file rather than written through to its target."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".proposal-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        # mkstemp makes the file 0600; the change keeps the mode it had, or a shared checkout
        # would find its proposal unreadable after one answer.
        with contextlib.suppress(OSError):
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


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
    idx = next((n for n, b in enumerate(blocks) if b[0] and b[0].lower() == section.lower()), None)
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
