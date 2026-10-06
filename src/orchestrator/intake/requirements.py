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

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from orchestrator.intake.gaps import STRICT_GAP_RULES, GapAnalyzer, GapFinding, GapRule, blocks_approval
from orchestrator.intake.intents import Intent, Resolution, _slug, question_states
from orchestrator.intake.openspec_source import _H2, _parse_resolution, change_to_intent
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
    md = proposal.read_text(encoding="utf-8")
    specs = tuple(p.read_text(encoding="utf-8") for p in sorted((directory / "specs").rglob("spec.md")))
    change_id = directory.name
    return LoadedChange(
        change_id, directory, proposal, md, change_to_intent(change_id, proposal_md=md, spec_texts=specs)
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
                question=str(row["question"]),
                answer=str(row.get("answer") or ""),
                defer_to=str(row.get("defer_to") or ""),
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


def record_answers(
    change: LoadedChange,
    requests: list[AnswerRequest],
    *,
    channel: Channel = "cli",
    at: str = "",
) -> dict[str, Any]:
    """Write each answer or deferral into ``proposal.md``. All-or-nothing: every question is
    matched before anything is written, so a typo in the fifth leaves the file untouched."""
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
            res = Resolution(status="deferred", owner=req.defer_to.strip().lstrip("@"), at=when)
        resolved.append((question, res, req.answer))
    md = change.proposal_md
    for question, res, raw in resolved:
        md = _record_resolution(md, question, res)
        field_name = FIELD_QUESTIONS.get(question)
        if field_name and res.status == "answered":
            value: str | list[str] = res.answer
            if field_name in _LIST_FIELDS:
                value = _split_list(raw)  # the answer as typed: its line breaks are the list
            md = _set_field(md, field_name, value)
    change.proposal_path.write_text(md, encoding="utf-8")
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
    """``[[heading | None, [lines…]], …]`` — the preamble first, then one block per ``## ``."""
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
    return blocks


def _join(blocks: list[list[Any]]) -> str:
    return "\n".join("\n".join(b[1]) for b in blocks).rstrip("\n") + "\n"


def _bullet_text(line: str) -> str:
    return line.strip().lstrip("-*").strip()


def _record_resolution(md: str, question: str, res: Resolution) -> str:
    """Put ``res`` directly under ``question`` in ``## Open Questions``, replacing any resolution
    already nested there. Everything else in the file is left byte-for-byte alone."""
    blocks = _h2_blocks(md)
    for block in blocks:
        if not (block[0] and block[0].lower() in ("open questions", "questions")):
            continue
        lines: list[str] = block[1]
        out: list[str] = [lines[0]]
        i, done = 1, False
        while i < len(lines):
            line = lines[i]
            out.append(line)
            i += 1
            if not done and line.strip() and line[:1] not in " \t" and _bullet_text(line) == question:
                done = True
                while (
                    i < len(lines)
                    and lines[i].strip()
                    and lines[i][:1] in " \t"
                    and _parse_resolution(_bullet_text(lines[i])) is not None
                ):
                    i += 1
                out.append(_resolution_line(res))
        block[1] = out
        return _join(blocks)
    raise RequirementsError("this change has no `## Open Questions` section")


def _h3_blocks(body: str) -> tuple[str, list[tuple[str, str]]]:
    """A section body as ``(lead text, [(### name as written, text)…])``."""
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", body, re.MULTILINE))
    if not matches:
        return body.strip(), []
    subs = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        subs.append((m.group(1).strip(), body[m.end() : end].strip()))
    return body[: matches[0].start()].strip(), subs


def _set_subsection(md: str, section: str, name: str, text: str, *, canonical: tuple[str, ...] = ()) -> str:
    """Set ``### name`` under ``## section``, creating the section if it is missing. The lead text
    and every other subsection stay as they were; ``canonical`` names go first, in that order."""
    blocks = _h2_blocks(md)
    block = next((b for b in blocks if b[0] and b[0].lower() == section.lower()), None)
    if block is None:
        block = [section, [f"## {section}", ""]]
        blocks.insert(1, block)
    lead, subs = _h3_blocks("\n".join(block[1][1:]))
    subs = [(n, t) for n, t in subs if n.lower() != name.lower()] + [(name, text)]
    subs.sort(key=lambda s: canonical.index(s[0].lower()) if s[0].lower() in canonical else len(canonical))
    body = [lead] if lead else []
    body.append("\n".join(f"### {n}\n{t}" for n, t in subs))
    block[1] = [block[1][0], *"\n\n".join(body).split("\n"), ""]
    return _join(blocks)


def _set_field(md: str, field_name: str, value: str | list[str]) -> str:
    """Fill one why-field in the layout the OpenSpec writer uses (§3.3)."""
    if field_name == "non_goals":
        return _set_subsection(md, "What Changes", "Non-goals", "\n".join(f"- {v}" for v in value))
    text = "\n".join(f"- {v}" for v in value) if isinstance(value, list) else value
    return _set_subsection(md, "Why", _FIELD_HEADING[field_name], text, canonical=_CANONICAL)
