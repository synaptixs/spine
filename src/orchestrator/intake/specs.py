"""Block B.4: spec writing.

Expands each approved ``Intent`` into a ``FeatureSpec`` an engineer (or the
code-gen pipeline in Block D) can act on: a summary, a user story,
acceptance criteria, technical notes, NFRs, dependencies, and a rough
estimate. One spec maps to one Jira issue downstream (B.5).

One LLM call per intent keeps each spec focused and gives clean 1:1
traceability (intent → spec → issue). Direct call, structured-JSON parse
with graceful degradation — a malformed response yields a minimal spec
carried by the intent's own fields rather than crashing the ingest.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.core.llm import CompletionResult, LLMClient, Message
from orchestrator.intake.intents import Intent

logger = logging.getLogger("orchestrator.intake.specs")

_SPEC_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = (
    "You expand an approved product INTENT into a FEATURE SPEC ready for an "
    "engineer to implement. Be concrete and testable.\n\n"
    "Output a single JSON object, no prose, no code fences:\n"
    "{"
    '"summary": "<2-4 sentence what + why>", '
    '"user_story": "As a <role>, I want <capability>, so that <benefit>", '
    '"acceptance_criteria": ["<testable, Given/When/Then or checklist>"], '
    '"technical_notes": "<approach, affected components, risks>", '
    '"nfrs": ["<non-functional requirement>"], '
    '"dependencies": ["<other work or system>"], '
    '"estimate": "S|M|L|XL"}\n\n'
    "Rules: acceptance_criteria must be specific and verifiable; carry over "
    "the intent's NFRs/dependencies and add any you infer; estimate is a "
    "rough t-shirt size. Do not invent scope beyond the intent.\n\n"
    "FIDELITY: when the intent carries STATED ACCEPTANCE CRITERIA, copy every "
    "one into acceptance_criteria VERBATIM — never reword, split, merge, drop, "
    "or add. They are the contract: a named function, its signature, return "
    "type, error behavior. You may add inferred criteria only AFTER the "
    "verbatim ones, and only when the stated set leaves a real gap. An "
    "invented or paraphrased criterion sends codegen to build the wrong API "
    "(it then writes tests for its own API and they pass). Concrete technical "
    "identifiers the intent names (file paths, module/class/function names, "
    "env vars, endpoints) must be carried into the spec VERBATIM — codegen "
    "edits the exact files named; 'the statistics module' instead of "
    "'src/orchestrator/pkg/stats.py' makes it guess."
)


class FeatureSpec(BaseModel):
    """An implementation-ready spec derived from one intent → one Jira issue."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str
    title: str
    summary: str = ""
    user_story: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    technical_notes: str = ""
    nfrs: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    estimate: str = ""


class SpecWriter:
    """Turns intents into feature specs via one LLM call each."""

    def __init__(self, llm: LLMClient, *, model: str = _SPEC_MODEL) -> None:
        self._llm = llm
        self._model = model

    async def write(self, intent: Intent) -> FeatureSpec:
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=self._build_user_message(intent)),
        ]
        # temperature=0: a spec must be stable for a given intent so the same
        # --intent yields the same acceptance criteria (and cached) run to run.
        result: CompletionResult = await self._llm.complete(
            messages, model=self._model, json_object=True, temperature=0.0
        )
        return self._parse(result.text, intent)

    async def write_all(self, intents: list[Intent]) -> list[FeatureSpec]:
        return [await self.write(i) for i in intents]

    def _build_user_message(self, intent: Intent) -> str:
        lines = [
            f"Intent: {intent.title}",
            f"Description: {intent.description}",
        ]
        if intent.scope:
            lines.append(f"Scope: {intent.scope}")
        if intent.acceptance_criteria:
            lines.append("STATED ACCEPTANCE CRITERIA (copy these into acceptance_criteria VERBATIM):")
            lines.extend(f"  - {c}" for c in intent.acceptance_criteria)
        if intent.dependencies:
            lines.append(f"Dependencies: {', '.join(intent.dependencies)}")
        if intent.nfrs:
            lines.append(f"NFRs: {', '.join(intent.nfrs)}")
        if intent.open_questions:
            lines.append(f"Open questions (resolve in technical_notes): {'; '.join(intent.open_questions)}")
        return "\n".join(lines)

    def _parse(self, text: str, intent: Intent) -> FeatureSpec:
        payload = _loads_json_object(text)
        if payload is None:
            logger.warning("intake.specs.unparseable_output", extra={"intent": intent.id})
            # Minimal spec carried by the intent so the ingest still produces
            # something traceable for the human to fix.
            return FeatureSpec(
                intent_id=intent.id,
                title=intent.title,
                summary=intent.description,
                acceptance_criteria=list(intent.acceptance_criteria),
                nfrs=list(intent.nfrs),
                dependencies=list(intent.dependencies),
            )
        # Stated criteria are the contract: keep them even if the model dropped
        # them, and ensure they lead (verbatim) before any the model inferred.
        criteria = _merge_criteria(intent.acceptance_criteria, _str_list(payload.get("acceptance_criteria")))
        return FeatureSpec(
            intent_id=intent.id,
            title=intent.title,
            summary=str(payload.get("summary") or intent.description).strip(),
            user_story=str(payload.get("user_story") or "").strip(),
            acceptance_criteria=criteria,
            technical_notes=str(payload.get("technical_notes") or "").strip(),
            nfrs=_str_list(payload.get("nfrs")) or list(intent.nfrs),
            dependencies=_str_list(payload.get("dependencies")) or list(intent.dependencies),
            estimate=str(payload.get("estimate") or "").strip().upper(),
        )


def _merge_criteria(stated: list[str], produced: list[str]) -> list[str]:
    """Stated criteria lead (verbatim); the model's extras follow, de-duped.

    Guarantees a contract the source stated survives even if the spec writer
    paraphrased or dropped it — the failure that let run #23 ship the wrong
    API. Comparison is whitespace-insensitive so a re-emitted criterion isn't
    double-listed.
    """
    out = list(stated)
    seen = {" ".join(c.split()) for c in stated}
    for c in produced:
        if " ".join(c.split()) not in seen:
            out.append(c)
            seen.add(" ".join(c.split()))
    return out


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
