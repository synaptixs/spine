"""OpenSpec source adapter — spec-driven intake (https://openspec.dev).

Reads OpenSpec **change proposals** (``openspec/changes/<id>/``) as fully-formed
``Intent``s, **deterministically — no LLM**. Because an OpenSpec change is already
intent-shaped (one capability, with ``### Requirement:`` SHALL statements and
``#### Scenario:`` Given/When/Then acceptance tests), we parse it straight to an
``Intent`` and populate ``acceptance_criteria`` **verbatim** from the requirements +
scenarios — the exact contract codegen must hit. This bypasses the LLM intent
extractor (via the ``StructuredIntentSource`` seam), which removes the "guess intents
out of prose" step that makes wiki-sourced intents crude.

Directory layout (openspec.dev)::

    openspec/
      changes/<change-id>/
        proposal.md          ## Why | ## What Changes | ## Impact
                             (older variant: ## Intent | ## Scope | ## Approach)
        design.md            (optional)
        tasks.md             ## N. Group  +  - [ ] N.M task
        specs/<cap>/spec.md   ## ADDED/MODIFIED/REMOVED Requirements
                                → ### Requirement: <text> (SHALL/MUST)
                                  → #### Scenario: <text>
                                    - GIVEN/WHEN/THEN/AND …
      specs/<cap>/spec.md     current truth (not a change)

``openspec://<change-id>`` → that one change; ``openspec://`` → every change (excluding
``changes/archive/``). The root dir comes from ``ORCHESTRATOR_OPENSPEC_ROOT`` (default
``./openspec``); ``openspec:///abs/path/openspec`` can point at an absolute dir.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.intake.intents import Intent, Resolution, _slug
from orchestrator.intake.source import FetchTreeResult, SourceDocument, SourceRef

_ARCHIVE = "archive"
_DEFAULT_ROOT = "openspec"

# `## Heading` section splitter (level-2). Everything up to the next `## ` (or EOF).
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
# `### Requirement: <text>` and `#### Scenario: <text>` markers. (Delta section
# headers — `## ADDED/MODIFIED/REMOVED Requirements` — are just `## ` sections; we
# read every requirement regardless of which delta bucket it sits under.)
_REQ = re.compile(r"^###\s+Requirement:\s*(.+?)\s*$", re.MULTILINE)
_SCENARIO = re.compile(r"^####\s+Scenario:\s*(.+?)\s*$", re.MULTILINE)


# `### Name` subsection splitter (level-3), and the `idea: <id>` header line the requirements
# check writes directly under the H1.
_H3 = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_IDEA = re.compile(r"^idea:[ \t]*(\S.*?)\s*$", re.MULTILINE)
# A recorded answer, nested under its question (see `openspec_writer._resolution_line`):
#   **Answer** (user · elicitation · 2026-10-02): EUR and USD only.
#   **Deferred** to @finance-lead (2026-10-02)
_ANSWER = re.compile(r"^\*\*(Answer|Proposed)\*\*\s*\(([^)]*)\)\s*:\s*(.*)$")
_DEFERRED = re.compile(r"^\*\*Deferred\*\*(?:\s+to\s+@?(\S+))?(?:\s*\(([^)]*)\))?\s*$")
_ORIGINS = {"stated", "user", "relayed", "proposed"}
_CHANNELS = {"source", "elicitation", "mcp", "answers-file", "cli"}
_WHY_SUBSECTIONS = ("problem", "users", "outcome")


# --- markdown parsing (pure, unit-testable) --------------------------------


def _h3_sections(body: str) -> tuple[str, dict[str, str]]:
    """Split a section body into its lead text (before the first ``###``) and its
    ``### Name`` subsections, keyed by lowercased name, in document order."""
    matches = list(_H3.finditer(body))
    if not matches:
        return body.strip(), {}
    subs: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        subs[m.group(1).strip().lower()] = body[m.end() : end].strip()
    return body[: matches[0].start()].strip(), subs


def _items(body: str) -> list[str]:
    """One item per non-empty line, list markers stripped."""
    return [t for t in (ln.strip().lstrip("-*").strip() for ln in body.splitlines()) if t]


def _parse_resolution(text: str) -> Resolution | None:
    """A nested ``**Answer**`` / ``**Proposed**`` / ``**Deferred**`` bullet, or ``None`` for any
    other text — which the caller then treats exactly as it always did."""
    m = _ANSWER.match(text)
    if m:
        parts = [p.strip() for p in m.group(2).split("·")]
        origin = parts[0] if parts and parts[0] in _ORIGINS else ""
        channel = parts[1] if len(parts) > 1 and parts[1] in _CHANNELS else ""
        if not origin or not channel:
            return None
        return Resolution(
            status="proposed" if m.group(1) == "Proposed" else "answered",
            answer=m.group(3).strip(),
            origin=origin,  # type: ignore[arg-type]
            channel=channel,  # type: ignore[arg-type]
            at=parts[2] if len(parts) > 2 else "",
        )
    m = _DEFERRED.match(text)
    if m:
        return Resolution(status="deferred", owner=m.group(1) or "", at=(m.group(2) or "").strip())
    return None


def _questions(body: str) -> tuple[list[str], dict[str, Resolution]]:
    """Open questions, and the answers recorded under them.

    Every line is a question, as it always was — except an *indented* line directly under one
    that is a recognised resolution bullet, which is that question's answer and never a
    question of its own."""
    questions: list[str] = []
    resolutions: dict[str, Resolution] = {}
    current = ""
    for raw in body.splitlines():
        if not raw.strip():
            continue
        text = raw.strip().lstrip("-*").strip()
        if raw[:1] in " \t" and current:
            res = _parse_resolution(text)
            if res is not None:
                resolutions[current] = res
                continue
        questions.append(text)
        current = text
    return questions, resolutions


def _h2_sections(md: str) -> dict[str, str]:
    """Map each ``## Heading`` (lowercased) → its body text, in document order."""
    out: dict[str, str] = {}
    matches = list(_H2.finditer(md))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        out[m.group(1).strip().lower()] = md[m.end() : end].strip()
    return out


def _first_section(sections: dict[str, str], *names: str) -> str:
    """First non-empty body among the given (lowercased) heading names."""
    for n in names:
        if sections.get(n):
            return sections[n]
    return ""


def _title(proposal_md: str, change_id: str) -> str:
    """The change title: the H1 (minus a ``Proposal:``/``Change:`` prefix), else the id humanized."""
    for line in proposal_md.splitlines():
        s = line.strip()
        if s.startswith("# "):
            t = s[2:].strip()
            t = re.sub(r"^(proposal|change)\s*:\s*", "", t, flags=re.IGNORECASE).strip()
            return t or _humanize(change_id)
    return _humanize(change_id)


def _humanize(change_id: str) -> str:
    return change_id.replace("-", " ").replace("_", " ").strip().capitalize()


def _scenario_lines(body: str) -> list[str]:
    """The GIVEN/WHEN/THEN/AND bullet lines of a scenario, normalized to one string."""
    steps: list[str] = []
    for line in body.splitlines():
        s = line.strip().lstrip("-*").strip()
        if re.match(r"^(GIVEN|WHEN|THEN|AND|BUT)\b", s, re.IGNORECASE):
            steps.append(" ".join(s.split()))
    return steps


def _requirements(spec_md: str) -> list[tuple[str, list[str]]]:
    """Parse a (delta) spec file into ``(requirement_statement, [scenario_lines...])``.

    The requirement statement is the ``### Requirement: <name>`` heading plus its
    normative body up to the first scenario (the SHALL/MUST sentence). Each
    ``#### Scenario:`` becomes a rendered ``"Scenario: <name> — GIVEN … WHEN … THEN …"``.
    """
    out: list[tuple[str, list[str]]] = []
    reqs = list(_REQ.finditer(spec_md))
    for i, rm in enumerate(reqs):
        end = reqs[i + 1].start() if i + 1 < len(reqs) else len(spec_md)
        block = spec_md[rm.end() : end]
        name = rm.group(1).strip()
        scen = list(_SCENARIO.finditer(block))
        # requirement body = text before the first scenario (the SHALL statement)
        body_end = scen[0].start() if scen else len(block)
        statement = " ".join(block[:body_end].split()).strip()
        req_text = f"{name}: {statement}" if statement else name
        rendered: list[str] = []
        for j, sm in enumerate(scen):
            s_end = scen[j + 1].start() if j + 1 < len(scen) else len(block)
            steps = _scenario_lines(block[sm.end() : s_end])
            label = sm.group(1).strip()
            rendered.append(f"Scenario: {label} — {' '.join(steps)}".strip(" —"))
        out.append((req_text, rendered))
    return out


def _split_non_goals(what_changes: str) -> tuple[str, list[str]]:
    """``## What Changes`` without its ``### Non-goals`` subsection, and that subsection's items.
    A section with no such subsection comes back untouched."""
    lead, subs = _h3_sections(what_changes)
    if "non-goals" not in subs:
        return what_changes, []
    rest = [f"### {name}\n{body}".rstrip() for name, body in subs.items() if name != "non-goals"]
    return "\n\n".join(p for p in (lead, *rest) if p), _items(subs["non-goals"])


def change_to_intent(
    change_id: str, *, proposal_md: str = "", spec_texts: tuple[str, ...] = (), tasks_md: str = ""
) -> Intent:
    """Map one OpenSpec change to an ``Intent`` (deterministic; scenarios → criteria)."""
    sections = _h2_sections(proposal_md)
    description = _first_section(sections, "why", "intent", "purpose", "what changes")
    what_changes, non_goals = _split_non_goals(sections.get("what changes", ""))
    if description == sections.get("what changes"):
        description = what_changes
    scope = _first_section({**sections, "what changes": what_changes}, "scope", "what changes", "impact")
    approach = _first_section(sections, "approach")
    if approach and approach not in scope:
        scope = f"{scope}\n\nApproach: {approach}".strip()

    # A bare `## Why` is the description, whatever it contains. Only when it carries a
    # `### Problem` / `### Users` / `### Outcome` subsection is it read as the why-fields — and
    # then anything else in it (lead text, any other subsection) still lands in `description`.
    problem = outcome = ""
    users: list[str] = []
    if sections.get("why") and description == sections["why"]:
        lead, subs = _h3_sections(sections["why"])
        if any(name in subs for name in _WHY_SUBSECTIONS):
            problem, outcome = subs.get("problem", ""), subs.get("outcome", "")
            users = _items(subs.get("users", ""))
            rest = [
                f"### {name}\n{body}".rstrip() for name, body in subs.items() if name not in _WHY_SUBSECTIONS
            ]
            description = "\n\n".join(p for p in (lead, *rest) if p) or problem

    criteria: list[str] = []
    for spec_md in spec_texts:
        for req_text, scenarios in _requirements(spec_md):
            criteria.append(req_text)
            criteria.extend(scenarios)

    # Open questions: a proposal may carry them explicitly; keep them verbatim. An answer
    # recorded under a question is that question's resolution, not another question.
    open_q, resolutions = _questions(_first_section(sections, "open questions", "questions"))
    idea = _IDEA.search(proposal_md.split("\n## ", 1)[0])

    return Intent(
        id=f"intent-{_slug(change_id)}",
        title=_title(proposal_md, change_id),
        description=description or _humanize(change_id),
        scope=scope,
        acceptance_criteria=criteria,
        open_questions=open_q,
        source_doc_ids=[f"openspec:{change_id}"],
        problem=problem,
        users=users,
        outcome=outcome,
        non_goals=non_goals,
        idea_id=idea.group(1) if idea else "",
        resolutions=resolutions,
    )


# --- the adapter -----------------------------------------------------------


@dataclass
class OpenSpecSourceConfig:
    """Where the ``openspec/`` tree lives. Default: ``$ORCHESTRATOR_OPENSPEC_ROOT`` or ``./openspec``."""

    root: Path = field(default_factory=lambda: Path(os.getenv("ORCHESTRATOR_OPENSPEC_ROOT", _DEFAULT_ROOT)))


class OpenSpecSourceAdapter:
    """Reads ``openspec/changes/`` as deterministic, intent-shaped documents.

    Implements ``SourceAdapter`` (so it drops into the intake pipeline) **and**
    ``StructuredIntentSource`` (so ``analyze`` skips the LLM extractor and uses the
    parsed intents directly)."""

    source_kind = "openspec"

    def __init__(self, config: OpenSpecSourceConfig | None = None) -> None:
        self._config = config or OpenSpecSourceConfig()
        self._intents: dict[str, Intent] = {}  # doc_id → parsed Intent, filled by fetch_tree

    # location helpers
    @property
    def _changes_dir(self) -> Path:
        return self._config.root / "changes"

    def _change_dir(self, change_id: str) -> Path:
        return self._changes_dir / change_id

    def _list_change_ids(self) -> list[str]:
        if not self._changes_dir.is_dir():
            return []
        return sorted(
            p.name
            for p in self._changes_dir.iterdir()
            if p.is_dir() and p.name != _ARCHIVE and (p / "proposal.md").is_file()
        )

    def _read(self, change_id: str) -> tuple[SourceDocument, Intent]:
        d = self._change_dir(change_id)
        proposal = _read_text(d / "proposal.md")
        tasks = _read_text(d / "tasks.md")
        specs_dir = d / "specs"
        spec_texts = tuple(_read_text(p) for p in sorted(specs_dir.rglob("spec.md")) if p.is_file())
        intent = change_to_intent(change_id, proposal_md=proposal, spec_texts=spec_texts, tasks_md=tasks)
        body = "\n\n".join(t for t in (proposal, *spec_texts) if t.strip())
        doc = SourceDocument(
            id=change_id,
            title=intent.title,
            body=body,
            url=str(d),
            space="openspec",
            labels=("openspec", "change"),
        )
        return doc, intent

    async def fetch_document(self, doc_id: str) -> SourceDocument:
        doc, intent = self._read(doc_id)
        self._intents[doc.id] = intent
        return doc

    async def list_children(self, doc_id: str) -> list[SourceRef]:
        # The root ("") lists every change; a change has no children.
        if doc_id and doc_id != _DEFAULT_ROOT:
            return []
        return [SourceRef(id=cid, title=_humanize(cid), kind="change") for cid in self._list_change_ids()]

    async def fetch_tree(self, root_id: str, *, max_depth: int = 3, max_docs: int = 100) -> FetchTreeResult:
        ids = [root_id] if root_id and root_id != _DEFAULT_ROOT else self._list_change_ids()
        docs: list[SourceDocument] = []
        truncated = False
        for cid in ids:
            if len(docs) >= max_docs:
                truncated = True
                break
            if not self._change_dir(cid).is_dir():
                continue
            doc, intent = self._read(cid)
            self._intents[doc.id] = intent
            docs.append(doc)
        return FetchTreeResult(documents=docs, truncated=truncated)

    def structured_intents(self, documents: list[SourceDocument]) -> list[Intent]:
        """The parsed intents for these documents (populated during ``fetch_tree``)."""
        return [self._intents[d.id] for d in documents if d.id in self._intents]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


__all__ = ["OpenSpecSourceAdapter", "OpenSpecSourceConfig", "change_to_intent"]
