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

from orchestrator.core.llm import CompletionResult, LLMClient, Message, ToolSpec, catalog
from orchestrator.intake.intents import Intent

logger = logging.getLogger("orchestrator.intake.specs")

_SPEC_MODEL = catalog.DEFAULT_MODEL

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

# The same contract as the prompt, as a tool the provider makes the model call.
#
# `json_object=True` only constrains providers that have a JSON mode; Anthropic
# drops `response_format`, and the default intake model is an Anthropic one — so the
# spec arrived as whatever the model felt like emitting and `_loads_json_object`
# salvaged it. Codegen and the acceptance judge already force a tool call for this;
# spec writing now does the same. The prompt still states the fidelity rules, which
# no schema can express.
_SUBMIT_TOOL = ToolSpec(
    name="submit_feature_spec",
    description=("Submit the implementation-ready feature spec for this intent. Call this exactly once."),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2-4 sentence what + why."},
            "user_story": {
                "type": "string",
                "description": "As a <role>, I want <capability>, so that <benefit>.",
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Testable criteria; stated ones copied VERBATIM and first.",
            },
            "technical_notes": {"type": "string"},
            "nfrs": {"type": "array", "items": {"type": "string"}},
            "dependencies": {"type": "array", "items": {"type": "string"}},
            "estimate": {"type": "string", "enum": ["S", "M", "L", "XL"]},
        },
        "required": ["summary", "acceptance_criteria"],
    },
)


class FeatureSpec(BaseModel):
    """An implementation-ready spec derived from one intent → one Jira issue."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str
    title: str
    summary: str = ""
    user_story: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    # Criteria the model produced that the source never stated. Kept apart from
    # acceptance_criteria so a reader (and, later, the judge) can tell a contract
    # the ticket signed from a suggestion the spec writer inferred.
    proposed_criteria: list[str] = Field(default_factory=list)
    # Stated criteria the code *already* satisfies, mapped to the evidence that says so
    # ("src/orchestrator/cli.py:134 — _check() already prints Error {status} and exits 1").
    # The third state, and the one that costs a run: SSPN-49 filed six criteria of which
    # two described behaviour that already existed, so a run would have reported them met
    # having changed nothing. Keyed by the criterion's exact text — a key that matches
    # nothing is surfaced as a mismatch rather than silently dropped. Human-supplied; no
    # deterministic pass can make this judgement.
    met_criteria: dict[str, str] = Field(default_factory=dict)
    technical_notes: str = ""
    nfrs: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    estimate: str = ""


class SpecWriter:
    """Turns intents into feature specs via one LLM call each."""

    def __init__(self, llm: LLMClient, *, model: str = "") -> None:
        self._llm = llm
        self._model = model or catalog.resolve("intake")

    async def write(self, intent: Intent) -> FeatureSpec:
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=self._build_user_message(intent)),
        ]
        # temperature=0: a spec must be stable for a given intent so the same
        # --intent yields the same acceptance criteria (and cached) run to run.
        result: CompletionResult = await self._llm.complete(
            messages,
            model=self._model,
            temperature=0.0,
            tools=[_SUBMIT_TOOL],
            tool_choice=_SUBMIT_TOOL.name,
        )
        for call in result.tool_calls:
            if call.name == _SUBMIT_TOOL.name:
                # Re-serialized so the forced call and a text answer share one parser,
                # keeping the minimal-spec degradation path the only fallback.
                return self._parse(json.dumps(call.arguments), intent)
        # A provider that ignored the tool still answers in text — the old path, unchanged.
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
        # Stated criteria are the contract: keep them verbatim and alone in
        # acceptance_criteria; whatever the model added lands in proposed_criteria.
        stated, proposed = _merge_criteria(
            intent.acceptance_criteria, _str_list(payload.get("acceptance_criteria"))
        )
        return FeatureSpec(
            intent_id=intent.id,
            title=intent.title,
            summary=str(payload.get("summary") or intent.description).strip(),
            user_story=str(payload.get("user_story") or "").strip(),
            acceptance_criteria=stated,
            proposed_criteria=proposed,
            technical_notes=str(payload.get("technical_notes") or "").strip(),
            nfrs=_str_list(payload.get("nfrs")) or list(intent.nfrs),
            dependencies=_str_list(payload.get("dependencies")) or list(intent.dependencies),
            estimate=str(payload.get("estimate") or "").strip().upper(),
        )


def _merge_criteria(stated: list[str], produced: list[str]) -> tuple[list[str], list[str]]:
    """Partition criteria by provenance: ``(stated, proposed)``.

    The stated list is returned verbatim and in its original order — a contract
    the source stated survives even if the spec writer paraphrased or dropped
    it (the failure that let run #23 ship the wrong API). Everything the model
    produced that isn't one of them is *proposed*, not accepted: concatenating
    the two is how a three-criterion ticket became a nine-criterion spec with
    nobody able to tell which three were real.

    Comparison stays whitespace-insensitive, so a criterion the model re-emits
    in a different whitespace form is recognised as the stated one rather than
    listed a second time as proposed.
    """
    stated_out = list(stated)
    seen = {" ".join(c.split()) for c in stated}
    proposed: list[str] = []
    for c in produced:
        key = " ".join(c.split())
        if key not in seen:
            proposed.append(c)
            seen.add(key)
    return stated_out, proposed


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
