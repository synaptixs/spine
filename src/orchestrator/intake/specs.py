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
import re
from collections.abc import Callable
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
    '"summary": "<2-4 sentence what + why, naming the files, identifiers, env vars and '
    'endpoints the intent names, VERBATIM>", '
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
    "'src/orchestrator/pkg/stats.py' makes it guess.\n\n"
    "GROUNDING: name no file, component, module, technology or unit the intent does not "
    "name. When a REPOSITORY CONTEXT block is given it lists the repository's language(s) and "
    "the real symbols matching the intent's words: prefer those names, and never propose a file "
    "in a language the repository is not written in. When a term is ambiguous — an acronym the "
    "code uses as an identifier versus a physical quantity, say — do not pick a meaning: write "
    "the question into technical_notes and keep the criteria to what the intent states."
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
            "summary": {
                "type": "string",
                "description": (
                    "2-4 sentence what + why. Name the files, identifiers, env vars and endpoints "
                    "the intent names, verbatim — 'the API client' instead of "
                    "'EBSOrderApiClient.cs' loses the file the author specified."
                ),
            },
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
    # The intent's own description and scope, carried through unchanged. The extractor is
    # bound to keep the source's identifiers verbatim in exactly these two fields — and until
    # they were carried, the only prose that survived into design and retrieval was
    # ``summary``, a second paraphrase under no such rule. NSS-1231: the file the ticket named
    # in its first sentence was gone by the time anything searched for it.
    description: str = ""
    scope: str = ""
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
        # Intent text → a REPOSITORY CONTEXT block, or "". Set by a caller that has a checkout
        # (`sdlc plan`, `sdlc feature`); intake itself knows no repository. NSS-1243: with only
        # the ticket to go on, the writer read "PSI" as pounds per square inch — the code uses it
        # as an identifier (`PsiLocalId`) — and put a threshold "in oil_status.js" in a C# repo.
        self.context_for: Callable[[str], str] | None = None

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
        if self.context_for is not None:
            try:
                context = self.context_for(f"{intent.title}\n{intent.description}\n{intent.scope}")
            except Exception:  # noqa: BLE001 — context is an aid; a spec without it is still a spec
                logger.warning("intake.specs.context_failed", exc_info=True)
                context = ""
            if context:
                lines.append("")
                lines.append(context)
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
                description=intent.description,
                scope=intent.scope,
                acceptance_criteria=list(intent.acceptance_criteria),
                nfrs=list(intent.nfrs),
                dependencies=list(intent.dependencies),
            )
        # Stated criteria are the contract: keep them verbatim and alone in
        # acceptance_criteria; whatever the model added lands in proposed_criteria.
        stated, proposed = _merge_criteria(
            intent.acceptance_criteria, _str_list(payload.get("acceptance_criteria"))
        )
        summary = str(payload.get("summary") or intent.description).strip()
        # The prompt asks for identifiers verbatim; NSS-1231 is the measured case of a model
        # not doing it — 40 identifiers gone, the named file among them, and retrieval then
        # searching the paraphrase. A rule a model can ignore is not a rule: whatever the
        # source named and the spec dropped is carried here, deterministically.
        notes = _carry_identifiers(
            str(payload.get("technical_notes") or "").strip(),
            present_in=summary,
            source=f"{intent.description}\n{intent.scope}",
        )
        return FeatureSpec(
            intent_id=intent.id,
            title=intent.title,
            summary=summary,
            description=intent.description,
            scope=intent.scope,
            user_story=str(payload.get("user_story") or "").strip(),
            acceptance_criteria=stated,
            proposed_criteria=proposed,
            technical_notes=notes,
            nfrs=_str_list(payload.get("nfrs")) or list(intent.nfrs),
            dependencies=_str_list(payload.get("dependencies")) or list(intent.dependencies),
            estimate=str(payload.get("estimate") or "").strip().upper(),
        )


def _source_path_re() -> re.Pattern[str]:
    """``sdlc.source_paths.PATH_RE``, imported lazily: `sdlc` imports `intake`, not the reverse."""
    from orchestrator.sdlc.source_paths import PATH_RE

    return PATH_RE


_SOURCE_PATH_RE = _source_path_re()


#: What a ticket author means as an identifier, in the order a reader would notice them.
#: Backticks first: whatever the author fenced is an identifier by declaration. Then the shapes
#: prose cannot produce by accident — a URL, a filename with a source extension, SCREAMING_SNAKE
#: with at least one underscore, CamelCase with a lowercase-or-digit→uppercase hump somewhere
#: after the first letter (``EBSOrderApiClient`` has ``rA``, ``OAuth2Client`` has ``2C``; ``Hot``
#: and ``OAuth2`` have none), a dotted
#: name with at least two dots. All-caps acronyms (``HTTP``, ``OIC``) are deliberately not
#: matched: too many are English.
_IDENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"`([^`\n]{2,120})`"),
    re.compile(r"\bhttps?://[^\s)\]>\"'`]+"),
    # Files: the one regex `sdlc.source_paths` keeps for every front-end suffix and either
    # separator — a second copy here had already drifted (no C/C++, no Windows paths).
    _SOURCE_PATH_RE,
    re.compile(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b"),
    re.compile(r"\b[A-Z](?=[A-Za-z0-9]*[a-z0-9][A-Z])[A-Za-z0-9]{2,}\b"),
    re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){2,}\b"),
)
#: Bounded honestly (invariant 7): a ticket that pastes a stack trace names hundreds.
_MAX_CARRIED = 24


def _identifiers(text: str) -> list[str]:
    """The identifiers ``text`` names, first-appearance order, deduplicated, longest-wins.

    A candidate that is a substring of one already kept is dropped: ``EBSOrderApiClient``
    inside ``EBSOrderApiClient.cs`` is the same fact, and carrying both would read as two.
    Pure and deterministic — same text in, same list out.
    """
    found: list[tuple[int, str]] = []
    for pattern in _IDENT_PATTERNS:
        for m in pattern.finditer(text):
            token = (m.group(1) if m.groups() else m.group(0)).strip().rstrip(".,;:")
            if token:
                found.append((m.start(), token))
    found.sort(key=lambda pair: (pair[0], -len(pair[1])))
    kept: list[str] = []
    for _, token in found:
        if any(token == k or _same_fact(token, k) for k in kept):
            continue
        kept.append(token)
    return kept


def _same_fact(short: str, longer: str) -> bool:
    """``short`` names the same thing as ``longer``: its stem, or the file at the end of its path.

    ``EBSOrderApiClient`` inside ``EBSOrderApiClient.cs`` is one fact; ``Cart`` inside
    ``ShoppingCartService`` is not, and a bare substring test dropped it — or kept it — depending
    on which sentence came first.
    """
    return longer.startswith(short + ".") or longer.endswith("/" + short) or longer.endswith("\\" + short)


def _carry_identifiers(technical_notes: str, *, present_in: str, source: str) -> str:
    """``technical_notes``, extended with every identifier ``source`` names that neither it nor
    ``present_in`` still mentions. Labelled as carried, so a reader knows the model did not
    write that line — and so ``design._stated_paths`` and retrieval can read it regardless of
    what the model chose to keep.
    """
    haystack = f"{present_in}\n{technical_notes}"
    # Whole-identifier presence: `Client` is not present because `EBSOrderApiClient` is.
    missing = [
        i
        for i in _identifiers(source)
        if not re.search(r"(?<![\w./\\])" + re.escape(i) + r"(?![\w])", haystack)
    ]
    if not missing:
        return technical_notes
    shown = missing[:_MAX_CARRIED]
    more = f" (+{len(missing) - len(shown)} more)" if len(missing) > len(shown) else ""
    line = "Identifiers the source names, carried verbatim: " + ", ".join(shown) + more
    return f"{technical_notes}\n\n{line}" if technical_notes else line


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
