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

from orchestrator.intake.intents import Intent, _slug
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


# --- markdown parsing (pure, unit-testable) --------------------------------


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


def change_to_intent(
    change_id: str, *, proposal_md: str = "", spec_texts: tuple[str, ...] = (), tasks_md: str = ""
) -> Intent:
    """Map one OpenSpec change to an ``Intent`` (deterministic; scenarios → criteria)."""
    sections = _h2_sections(proposal_md)
    description = _first_section(sections, "why", "intent", "purpose", "what changes")
    scope = _first_section(sections, "scope", "what changes", "impact")
    approach = _first_section(sections, "approach")
    if approach and approach not in scope:
        scope = f"{scope}\n\nApproach: {approach}".strip()

    criteria: list[str] = []
    for spec_md in spec_texts:
        for req_text, scenarios in _requirements(spec_md):
            criteria.append(req_text)
            criteria.extend(scenarios)

    # Open questions: a proposal may carry them explicitly; keep them verbatim.
    open_q = [
        ln.strip().lstrip("-*").strip()
        for ln in _first_section(sections, "open questions", "questions").splitlines()
        if ln.strip().lstrip("-*").strip()
    ]

    return Intent(
        id=f"intent-{_slug(change_id)}",
        title=_title(proposal_md, change_id),
        description=description or _humanize(change_id),
        scope=scope,
        acceptance_criteria=criteria,
        open_questions=open_q,
        source_doc_ids=[f"openspec:{change_id}"],
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
