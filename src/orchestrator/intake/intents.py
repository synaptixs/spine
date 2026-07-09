"""Block B.2: intent extraction.

Reads the ``SourceDocument``s from a Confluence (or other) ingest and
derives discrete, buildable *intents* — the unit a feature spec and then
a Jira issue will be built from. One LLM call over the concatenated docs
(length-capped), parsed into ``Intent``s with graceful degradation on
malformed output.

An ``Intent`` deliberately carries the fields a downstream spec + gap
analysis need: scope, dependencies, NFRs, and open questions. The gap
analyzer (B.3) checks these for completeness; the spec writer (B.4)
expands each into acceptance criteria.

The LLM call is direct (like the planner / code reviewer) rather than
through the runtime — Block B is a standalone CLI/ingest flow. The
``agent.intent_extractor`` template documents the contract for when the
full SDLC pipeline runs it through the runtime instead.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.core.llm import CompletionResult, LLMClient, Message
from orchestrator.intake.source import SourceDocument

logger = logging.getLogger("orchestrator.intake.intents")

_EXTRACTOR_MODEL = "claude-sonnet-4-6"
# Cap how much source text we send in one call. Requirements spaces can be
# large; the tree walk is already capped, this guards the prompt size.
_MAX_PROMPT_CHARS = 60_000

_SYSTEM_PROMPT = (
    "You extract product/engineering INTENTS from requirements documents. "
    "An intent is a single, discrete, buildable capability — not a whole "
    "project, not a vague theme. Split broad docs into multiple intents.\n\n"
    "Output a single JSON object, no prose, no code fences:\n"
    '{"intents": [{'
    '"title": "<short imperative>", '
    '"description": "<what + why, 1-3 sentences>", '
    '"scope": "<what is in / out of scope>", '
    '"acceptance_criteria": ["<criterion the document states, VERBATIM>"], '
    '"dependencies": ["<other intent or system>"], '
    '"nfrs": ["<non-functional requirement>"], '
    '"open_questions": ["<ambiguity a human must resolve>"], '
    '"source_title": "<title of the doc this came from>"}]}\n\n'
    "Rules: be specific; put genuine ambiguities in open_questions (do not "
    "invent answers); empty arrays are fine. Title is a concise imperative "
    '("Add CSV export").\n\n'
    "FIDELITY: one intent per feature the document actually states. Split "
    "only when the document itself describes distinct capabilities — never "
    "manufacture intents by decomposing a single stated feature into its "
    "implementation steps. A feature's tests, documentation, error handling, "
    "and quality gates (lint/type/CI requirements) are PART of that feature's "
    "intent, never separate intents — a ticket asking for one function plus "
    "its tests is exactly ONE intent. Concrete technical identifiers the document names "
    "(file paths, module/class/function names, env vars, endpoints) must "
    "appear VERBATIM in the description or scope — downstream codegen "
    "targets the exact files named, and a paraphrase like 'the statistics "
    "module' loses the path the author specified.\n\n"
    "ACCEPTANCE CRITERIA: when the document states acceptance criteria, "
    "requirements, or an API/behavioral contract (a named function, its "
    "signature, return type, parameters, error behavior), copy each one into "
    "acceptance_criteria VERBATIM — do NOT paraphrase or summarize them. "
    "These are the contract codegen must hit; 'sends a notification' instead "
    "of 'async notify_approval_raised(...) -> bool returns True on 2xx' lets "
    "the implementation invent its own API. Leave the array empty only when "
    "the document states no criteria."
)


class Intent(BaseModel):
    """A discrete, buildable capability derived from requirements."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    scope: str = ""
    # Acceptance criteria / API contract stated by the source, captured
    # VERBATIM so the spec writer (and thus codegen) targets the contract the
    # author wrote rather than a paraphrase. Empty when the source states none.
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    nfrs: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)


@runtime_checkable
class StructuredIntentSource(Protocol):
    """A source that yields fully-formed ``Intent``s **deterministically** (no LLM).

    Most sources hand the extractor unstructured prose (Confluence/Notion/markdown)
    and an LLM guesses intents out of it. A source whose format is already
    intent-shaped — e.g. OpenSpec change proposals, where each change is one
    capability with `### Requirement:`/`#### Scenario:` acceptance criteria — should
    parse straight to ``Intent``s instead. ``BacklogService.analyze`` prefers this
    path when the source implements it, skipping the LLM extraction entirely (cheaper,
    deterministic, and lossless on the stated criteria)."""

    def structured_intents(self, documents: list[SourceDocument]) -> list[Intent]: ...


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(title: str) -> str:
    s = _SLUG_RE.sub("-", title.lower()).strip("-")
    return s[:48] or "intent"


class IntentExtractor:
    """Derives ``Intent``s from source documents via one structured LLM call."""

    def __init__(self, llm: LLMClient, *, model: str = _EXTRACTOR_MODEL) -> None:
        self._llm = llm
        self._model = model

    async def extract(self, documents: list[SourceDocument]) -> list[Intent]:
        usable = [d for d in documents if not d.is_empty]
        if not usable:
            return []
        title_to_id = {d.title: d.id for d in usable}
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=self._build_user_message(usable)),
        ]
        # temperature=0: intent extraction must be deterministic so a pinned
        # --intent id stays addressable across runs (paired with the intake cache).
        result: CompletionResult = await self._llm.complete(
            messages, model=self._model, json_object=True, temperature=0.0
        )
        return self._parse(result.text, title_to_id, fallback_doc_ids=[d.id for d in usable])

    def _build_user_message(self, documents: list[SourceDocument]) -> str:
        parts: list[str] = []
        budget = _MAX_PROMPT_CHARS
        for d in documents:
            chunk = f"# {d.title} (id={d.id})\n{d.body}"
            if len(chunk) > budget:
                chunk = chunk[:budget] + "\n…[truncated]"
            parts.append(chunk)
            budget -= len(chunk)
            if budget <= 0:
                break
        return "Requirements documents:\n\n" + "\n\n---\n\n".join(parts)

    def _parse(self, text: str, title_to_id: dict[str, str], *, fallback_doc_ids: list[str]) -> list[Intent]:
        payload = _loads_json_object(text)
        if payload is None:
            logger.warning("intake.intents.unparseable_output")
            return []
        intents: list[Intent] = []
        seen_ids: set[str] = set()
        for idx, raw in enumerate(payload.get("intents") or []):
            intent = _intent_from_raw(raw, idx, title_to_id, fallback_doc_ids, seen_ids)
            if intent is not None:
                seen_ids.add(intent.id)
                intents.append(intent)
        return intents


def _intent_from_raw(
    raw: Any,
    idx: int,
    title_to_id: dict[str, str],
    fallback_doc_ids: list[str],
    seen_ids: set[str],
) -> Intent | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    base_id = f"intent-{_slug(title)}"
    intent_id = base_id if base_id not in seen_ids else f"{base_id}-{idx}"
    source_title = str(raw.get("source_title") or "").strip()
    source_ids = [title_to_id[source_title]] if source_title in title_to_id else list(fallback_doc_ids)
    return Intent(
        id=intent_id,
        title=title,
        description=str(raw.get("description") or "").strip(),
        scope=str(raw.get("scope") or "").strip(),
        acceptance_criteria=_str_list(raw.get("acceptance_criteria")),
        dependencies=_str_list(raw.get("dependencies")),
        nfrs=_str_list(raw.get("nfrs")),
        open_questions=_str_list(raw.get("open_questions")),
        source_doc_ids=source_ids,
    )


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _loads_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            loaded = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return loaded if isinstance(loaded, dict) else None
