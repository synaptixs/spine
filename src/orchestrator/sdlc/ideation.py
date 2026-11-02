"""Candidate options for a change, from one capped, ledgered model call (SSPN-297, P5 — D6, D11).

``option_measures`` measures options and calls no model; this module is the one place ideation
does. It is opt-in (a surface calls it only on ``--generate``) and makes **exactly one call**,
which returns 2–4 options written into the change's ``design.md`` ``## Options`` with
``Source: generated · <model> · $<cost>``. The comparison that follows is the same as for options
a person wrote: the generator is a convenience, not the product (D1).

**What the call sees** (D11 d): the change's why-fields, its landing sites from the graph (real
symbol ids, so the anchors the model names are ones ``option_measures`` can resolve), the doc
sections the graph binds to those symbols (so it does not propose what a page already ruled out),
and a bounded overview. Each part has its **own share** of a fixed budget — a symbol named in
twenty pages cannot crowd out the landing sites — and each records what it cut (invariant 7). The
model is told the cuts too, so it does not read a clipped list as the whole repository.

**What it may cost** (D6). Before the call, an estimate: the prompt at two characters a token plus
the tool's framing, and the full output cap, at the model's price (``SDLC_MODEL_PRICES``, else
LiteLLM's installed price, else the unpriced rate, said to be an estimate). Meant to be high, and set
from a measured call (D19): at four characters a token a 13,795-character prompt estimated 3,449
tokens and was billed 6,341, so the cap was refusing on half the cost; LiteLLM's tokenizer counted
4,074, older than the model's. A call can still cost more than this — it is an estimate, not a
guarantee — which is why each call's real cost is in the ledger. The call is refused,
never silently skipped, when that estimate is over ``CALL_CAP_USD`` or would take this change's
ideation spend past ``SESSION_TRIPWIRE_USD``; the refusal names the estimate and the limit and asks
approve-or-park. An approval names the number it approves (``approve_usd``), so a yes to one
estimate cannot let a larger call through.

**What it records.** The call is a row in the run's ``TokenLedger`` (stage ``ideation``) and a JSON
line in ``<change>/.ideation-ledger.jsonl``, which the tripwire sums: it travels with the change,
so its spend is reviewable and survives the process. A failed call is charged its estimate; a
reply that does not parse is reported, charged what it cost, and **writes nothing**.

**What it never does.** Overwrite a person's options (generated ones are appended, and a block the
reader reports problems in is refused before any spend), or write ``## Decision`` — only
``answer`` records a choice. No LiteLLM import happens until a price must be looked up or the call
is made: building and estimating a prompt for a priced model loads no model client.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from orchestrator.core.llm import catalog
from orchestrator.core.llm.budget import estimate_cost_usd
from orchestrator.core.llm.client import CompletionResult, LLMClient, LLMError, Message, ToolSpec
from orchestrator.core.llm.pricing import price_overrides_from_env, unpriced_rate_from_env
from orchestrator.core.llm.recording import RecordingLLMClient, TokenLedger
from orchestrator.intake.options import Option, OptionsError, load_options, render_options, write_options

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.intake.intents import Intent
    from orchestrator.intake.requirements import LoadedChange
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.docs import DocPage
    from orchestrator.sdlc.landings import Landing

__all__ = [
    "CALL_CAP_USD",
    "CHARS_PER_TOKEN",
    "LEDGER_NAME",
    "MAX_OUTPUT_TOKENS",
    "PROMPT_BUDGET_CHARS",
    "SESSION_TRIPWIRE_USD",
    "SHARES",
    "STAGE",
    "TOOL_FRAMING_TOKENS",
    "Breach",
    "CostEstimate",
    "DocSection",
    "GenerationResult",
    "IdeationError",
    "IdeationPrompt",
    "PromptPart",
    "Refusal",
    "build_prompt",
    "doc_sections",
    "estimate_call",
    "generate",
    "parse_reply",
    "session_spent",
]

#: The model-routing stage (``core.llm.catalog.STAGE_ENV``).
STAGE = "ideation"
#: Refuse one call whose estimate is over this. Provisional: P8 measures a real call (D6).
CALL_CAP_USD = 0.10
#: Refuse a call that would take one change's ideation spend past this — spec-kit's whole session (D6).
SESSION_TRIPWIRE_USD = 0.59
#: The prompt, in characters (about 6,000 tokens), and each part's fixed share of it.
PROMPT_BUDGET_CHARS = 24_000
SHARES: dict[str, float] = {"why": 0.20, "code": 0.40, "docs": 0.25, "overview": 0.15}
#: The reply's cap — and the output side of the estimate, so that side is never under.
MAX_OUTPUT_TOKENS = 2_000
#: The input side of the estimate (D19). Calibrated on P8 run 1 (claude-opus-5): 2.18 characters a
#: token over the whole prompt, tool schema included, plus Anthropic's tool-use preamble.
CHARS_PER_TOKEN = 2
TOOL_FRAMING_TOKENS = 500
MIN_OPTIONS, MAX_OPTIONS = 2, 4
#: Beside the change's ``proposal.md``: one JSON line per call.
LEDGER_NAME = ".ideation-ledger.jsonl"

_CUT = "…[truncated]"
# An item is cut short only when a useful piece of it fits; a sliver is left out instead.
_MIN_CUT = 80
_MAX_DOC_SECTION_CHARS = 600
_NOUNS = {"why": "fields", "code": "landing sites", "docs": "doc sections", "overview": "overview lines"}
_HEADINGS = {
    "why": "The change",
    "code": "Where the code already is (landing sites from the code graph)",
    "docs": "Documentation the graph binds to those symbols",
    "overview": "Repository overview (bounded)",
}
#: The writer refuses an entry holding `,` `;` or a backtick (it could not read back the same), so
#: the model is told so: one annotated entry once cost a whole paid reply (B132).
_BARE = (
    "Each entry is one bare symbol id or path: no comma, semicolon, backtick or note — "
    "any detail goes in the approach."
)
_TOOL = ToolSpec(
    name="submit_options",
    description="Submit 2 to 4 distinct candidate solutions for the change.",
    parameters={
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "minItems": MIN_OPTIONS,
                "maxItems": MAX_OPTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "A short name, unique among the options."},
                        "approach": {
                            "type": "string",
                            "description": "2 to 5 sentences: what it does and how.",
                        },
                        "anchors": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Existing symbol ids or file paths it changes or reuses. " + _BARE,
                        },
                        "creates": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "New files or modules it adds. " + _BARE,
                        },
                        "risks": {"type": "string"},
                    },
                    "required": ["title", "approach"],
                },
            }
        },
        "required": ["options"],
    },
)
_SYSTEM = (
    "You propose candidate solutions for a software change. Give 2 to 4 options that differ in "
    "approach, not in wording. For each: a short unique title; an approach of 2 to 5 sentences; "
    "anchors — the existing code it changes or reuses, named exactly as a symbol id or file path "
    "listed in the prompt (an anchor not in the code is reported as unresolved); creates — the new "
    "files or modules it adds; and its main risk. "
    + _BARE
    + " Do not propose what the documentation says was ruled out, and do not choose between the "
    "options: the person does. Answer by calling submit_options."
)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class IdeationError(ValueError):
    """A change whose ideation ledger cannot be read: the tripwire would be guessing. Never a defect."""


# --- the prompt ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DocSection:
    """One doc section the graph binds to a landing symbol. ``text`` is empty when the page itself
    was not read: the section is still named, since the graph says it describes the code."""

    title: str
    text: str
    symbol: str


@dataclass(frozen=True)
class PromptPart:
    """One part of the prompt in its own share: what was offered, what went in, what was cut."""

    name: str
    budget: int
    offered: int
    kept: int
    cut: bool
    chars: int
    #: Items left out before the budget was applied (weak landing sites).
    skipped: int = 0

    @property
    def elided(self) -> int:
        return self.offered - self.kept

    @property
    def note(self) -> str:
        noun = _NOUNS[self.name]
        if not self.offered and not self.skipped:
            return f"{self.name}: none offered"
        out = f"{self.name}: {self.kept} of {self.offered} {noun} kept"
        if self.cut:
            out += ", the last cut short"
        if self.elided:
            out += f", {self.elided} elided (budget {self.budget:,} characters)"
        if self.skipped:
            out += f"; {self.skipped} weak left out"
        return out


@dataclass(frozen=True)
class IdeationPrompt:
    messages: tuple[Message, ...]
    parts: tuple[PromptPart, ...]

    def part(self, name: str) -> PromptPart:
        return next(p for p in self.parts if p.name == name)

    @property
    def chars(self) -> int:
        """Everything sent: the messages and the tool's schema."""
        tool = len(json.dumps(_TOOL.parameters)) + len(_TOOL.description)
        return sum(len(m.content) for m in self.messages) + tool


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _fit(name: str, items: Sequence[str], budget: int, *, skipped: int = 0) -> tuple[str, PromptPart]:
    """Lay ``items`` into ``budget`` in order: whole while they fit, the next one cut short when a
    useful piece fits, every one after that left out (the ``intake.intents.fit_documents`` rule)."""
    out: list[str] = []
    used, cut = 0, False
    for item in items:
        need = len(item) + (1 if out else 0)
        if used + need <= budget:
            out.append(item)
            used += need
            continue
        room = budget - used - (1 if out else 0) - len(_CUT)
        if room >= _MIN_CUT:
            out.append(item[:room] + _CUT)
            cut = True
        break
    text = "\n".join(out)
    part = PromptPart(
        name=name, budget=budget, offered=len(items), kept=len(out), cut=cut, chars=len(text), skipped=skipped
    )
    return text, part


def _why_items(intent: Intent) -> list[str]:
    items = [f"Title: {_one_line(intent.title)}"]
    for label, value in (
        ("Description", intent.description),
        ("Problem", intent.problem),
        ("Users", "; ".join(intent.users)),
        ("Outcome", intent.outcome),
        ("Non-goals", "; ".join(intent.non_goals)),
    ):
        if _one_line(value):
            items.append(f"{label}: {_one_line(value)}")
    items.extend(f"Acceptance criterion: {_one_line(c)}" for c in intent.acceptance_criteria if _one_line(c))
    return items


def _code_items(landings: Sequence[Landing]) -> list[str]:
    items = []
    for hit in landings:
        line = f"- `{hit.node_id or hit.name}` ({hit.kind}) at {hit.location} — callers ≥ {hit.callers}"
        if hit.module:
            line += f", module `{hit.module}`"
        if hit.covered is False:
            line += ", no test reaches it"
        items.append(line)
    return items


def _doc_items(docs: Sequence[DocSection]) -> list[str]:
    items = []
    for doc in docs:
        text = _one_line(doc.text)
        if len(text) > _MAX_DOC_SECTION_CHARS:
            text = text[:_MAX_DOC_SECTION_CHARS].rstrip() + " …"
        items.append(f"- {doc.title}: {text}" if text else f"- {doc.title} (section text not read)")
    return items


def _overview_items(overview: Mapping[str, Any] | None) -> list[str]:
    if not overview:
        return []
    summary = overview.get("summary") or {}
    totals = overview.get("totals") or {}
    items = [f"Graph: {summary.get('nodes', 0)} nodes, {summary.get('edges', 0)} edges"]
    modules = list(overview.get("modules") or [])
    if (overview.get("truncated") or {}).get("modules"):
        items.append(f"Largest {len(modules)} of {totals.get('modules', '?')} modules:")
    items.extend(f"- module {m.get('module')} ({m.get('nodes', 0)} nodes)" for m in modules)
    items.extend(
        f"- symbol `{s.get('id')}` (degree {s.get('degree', 0)})" for s in overview.get("top_symbols") or []
    )
    return items


def build_prompt(
    intent: Intent,
    *,
    landings: Sequence[Landing] = (),
    docs: Sequence[DocSection] = (),
    overview: Mapping[str, Any] | None = None,
    budget_chars: int = PROMPT_BUDGET_CHARS,
) -> IdeationPrompt:
    """The one call's messages, each part in its own share of ``budget_chars``. Deterministic."""
    strong = [hit for hit in landings if not hit.weak]
    offered = {
        "why": _why_items(intent),
        "code": _code_items(strong),
        "docs": _doc_items(docs),
        "overview": _overview_items(overview),
    }
    sections: list[str] = []
    parts: list[PromptPart] = []
    for name, items in offered.items():
        skipped = len(landings) - len(strong) if name == "code" else 0
        text, part = _fit(name, items, int(budget_chars * SHARES[name]), skipped=skipped)
        parts.append(part)
        body = text or "(none)"
        sections.append(f"# {_HEADINGS[name]}\n{body}\n_{part.note}_")
    sections.append(
        f"# What to return\nCall submit_options with {MIN_OPTIONS} to {MAX_OPTIONS} options. "
        "Anchors must be symbol ids or file paths from the landing sites above, or other existing "
        "code you are sure of; new code goes under creates."
    )
    messages = (Message(role="system", content=_SYSTEM), Message(role="user", content="\n\n".join(sections)))
    return IdeationPrompt(messages=messages, parts=tuple(parts))


def doc_sections(
    store: FactStore, landings: Sequence[Landing], pages: Mapping[str, DocPage]
) -> tuple[DocSection, ...]:
    """The doc sections the graph binds (``MENTIONS``) to the strong landing sites, each once, in
    landing order. ``pages`` holds the section text by title (``pkg.doc_source.read_doc_pages``)."""
    seen: set[str] = set()
    out: list[DocSection] = []
    for hit in landings:
        if hit.weak or not hit.node_id:
            continue
        for doc in store.docs_for(hit.node_id):
            if doc.name in seen:
                continue
            seen.add(doc.name)
            page = pages.get(doc.name)
            out.append(DocSection(title=doc.name, text=page.text if page else "", symbol=hit.node_id))
    return tuple(out)


# --- the estimate ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CostEstimate:
    """What the call may cost at most: the prompt plus the full output cap at the model's price.
    ``priced`` is False when the price is the unpriced rate, an estimate of an estimate."""

    model: str
    usd: float
    prompt_tokens: int
    output_tokens: int
    priced: bool


def _price(model: str, env: Mapping[str, str] | None) -> tuple[float, float, bool]:
    """Dollars per million tokens in and out, and whether that is the model's own price."""
    override = price_overrides_from_env(env).get(model)
    if override is not None:
        # Rounded back from litellm's per-token form, so a configured $50 stays exactly 50.
        return (
            round(override["input_cost_per_token"] * 1e6, 9),
            round(override["output_cost_per_token"] * 1e6, 9),
            True,
        )
    info = catalog.describe(model)
    if info is not None and info.input_usd_per_mtok is not None and info.output_usd_per_mtok is not None:
        return info.input_usd_per_mtok, info.output_usd_per_mtok, True
    rate = unpriced_rate_from_env(env)
    return rate.input_per_mtok, rate.output_per_mtok, False


def estimate_call(
    prompt: IdeationPrompt, model: str, *, env: Mapping[str, str] | None = None
) -> CostEstimate:
    """The most the one call can cost, before it is made."""
    tokens_in = -(-prompt.chars // CHARS_PER_TOKEN) + TOOL_FRAMING_TOKENS
    rate_in, rate_out, priced = _price(model, env)
    usd = (tokens_in * rate_in + MAX_OUTPUT_TOKENS * rate_out) / 1e6
    return CostEstimate(
        model=model, usd=usd, prompt_tokens=tokens_in, output_tokens=MAX_OUTPUT_TOKENS, priced=priced
    )


# --- the ledger ------------------------------------------------------------------------------------


def _ledger_path(change: LoadedChange) -> Path:
    return change.directory / LEDGER_NAME


def session_spent(change: LoadedChange) -> float:
    """What ideation has spent on this change, from its ledger. A row that cannot be read stops the
    tripwire rather than letting it count low."""
    path = _ledger_path(change)
    if not path.is_file():
        return 0.0
    total = 0.0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            cost = row["cost_usd"]
        except (ValueError, TypeError, KeyError) as exc:
            raise IdeationError(f"{path}:{n}: ledger row cannot be read ({exc})") from exc
        if isinstance(cost, bool) or not isinstance(cost, int | float) or not math.isfinite(cost) or cost < 0:
            raise IdeationError(f"{path}:{n}: ledger row cost {cost!r} is not a number of dollars")
        total += float(cost)
    return total


def _append_row(change: LoadedChange, row: dict[str, Any]) -> None:
    with _ledger_path(change).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# --- the refusal -----------------------------------------------------------------------------------

Limit = Literal["call_cap", "session_tripwire"]


@dataclass(frozen=True)
class Breach:
    limit: Limit
    limit_usd: float
    would_be_usd: float


@dataclass(frozen=True)
class Refusal:
    """The call was not made: the estimate, the limits it passes, and the choice it leaves."""

    estimate_usd: float
    breaches: tuple[Breach, ...]
    message: str
    choices: tuple[str, ...] = ("approve", "park")


def _refusal(estimate: CostEstimate, spent: float, approve_usd: float) -> Refusal | None:
    shown = round(estimate.usd, 4)
    if approve_usd >= shown:
        return None
    breaches: list[Breach] = []
    if round(estimate.usd, 9) > CALL_CAP_USD:
        breaches.append(Breach("call_cap", CALL_CAP_USD, estimate.usd))
    if round(spent + estimate.usd, 9) > SESSION_TRIPWIRE_USD:
        breaches.append(Breach("session_tripwire", SESSION_TRIPWIRE_USD, spent + estimate.usd))
    if not breaches:
        return None
    reasons = []
    for b in breaches:
        if b.limit == "call_cap":
            reasons.append(f"over the ${CALL_CAP_USD:.2f} cap per call")
        else:
            reasons.append(
                f"would take this change's ideation spend from ${spent:.4f} to ${b.would_be_usd:.4f}, "
                f"past the ${SESSION_TRIPWIRE_USD:.2f} session tripwire"
            )
    priced = (
        "" if estimate.priced else " (no known price: estimated at the unpriced rate; set SDLC_MODEL_PRICES)"
    )
    message = (
        f"Not called: the estimate ${shown:.4f} for {estimate.model}{priced} is "
        + " and ".join(reasons)
        + f". Approve that estimate (approve_usd={shown:.4f}) or park the generation."
    )
    return Refusal(estimate_usd=estimate.usd, breaches=tuple(breaches), message=message)


# --- the reply -------------------------------------------------------------------------------------


def _from_text(text: str) -> Any:
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start : end + 1])
    except ValueError:
        return None
    return data.get("options") if isinstance(data, dict) else None


def _names(value: Any, k: int, field: str, problems: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(f"option {k}: {field} must be a list of names, got {type(value).__name__}")
        return ()
    return tuple(_one_line(v).strip("`").strip() for v in value)


def parse_reply(result: CompletionResult, *, source: str) -> tuple[tuple[Option, ...], tuple[str, ...]]:
    """The options a reply holds — from the ``submit_options`` call, else JSON in its text — and
    every reason they cannot be written. Options come back only when there is no problem."""
    raw: Any = None
    for call in result.tool_calls:
        if call.name == _TOOL.name:
            raw = call.arguments.get("options")
            break
    if raw is None:
        raw = _from_text(result.text)
    if not isinstance(raw, list):
        return (), ('the reply held no options (expected a submit_options call or JSON {"options": [...]})',)
    if not MIN_OPTIONS <= len(raw) <= MAX_OPTIONS:
        return (), (f"the reply held {len(raw)} options; expected {MIN_OPTIONS} to {MAX_OPTIONS}",)
    problems: list[str] = []
    options: list[Option] = []
    for k, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            problems.append(f"option {k} is not an object")
            continue
        title, approach, risks = item.get("title"), item.get("approach"), item.get("risks", "")
        if not isinstance(title, str) or not _one_line(title):
            problems.append(f"option {k}: no title")
        if not isinstance(approach, str) or not _one_line(approach):
            problems.append(f"option {k}: no approach")
        anchors = _names(item.get("anchors"), k, "anchors", problems)
        creates = _names(item.get("creates"), k, "creates", problems)
        # One line each, as the reader gives them back: a line break from the model is collapsed,
        # so it can neither open a section nor make the written block read back differently.
        options.append(
            Option(
                title=_one_line(title) if isinstance(title, str) else "",
                approach=_one_line(approach) if isinstance(approach, str) else "",
                anchors=anchors,
                creates=creates,
                risks=_one_line(risks) if isinstance(risks, str) else "",
                source=_one_line(source),
            )
        )
    if not problems:
        try:
            render_options(options)
        except OptionsError as exc:
            problems.append(str(exc))
    return (tuple(options), ()) if not problems else ((), tuple(problems))


# --- the call --------------------------------------------------------------------------------------

Status = Literal["written", "refused", "failed"]


@dataclass(frozen=True)
class GenerationResult:
    status: Status
    options: tuple[Option, ...] = ()
    problems: tuple[str, ...] = ()
    refusal: Refusal | None = None
    model: str = ""
    cost_usd: float = 0.0
    cost_estimated: bool = False
    estimate: CostEstimate | None = None
    #: This change's ideation spend before this call.
    session_spent_usd: float = 0.0


async def generate(
    change: LoadedChange,
    prompt: IdeationPrompt,
    client: LLMClient,
    *,
    model: str | None = None,
    approve_usd: float = 0.0,
    ledger: TokenLedger | None = None,
    env: Mapping[str, str] | None = None,
) -> GenerationResult:
    """At most one model call: refused before it when over a limit, its options appended to the
    change's ``## Options`` only when every one of them can be written and read back."""
    model = catalog.resolve(STAGE, model)
    try:
        existing = load_options(change)
        spent = session_spent(change)
    except (OptionsError, IdeationError) as exc:
        return GenerationResult(status="failed", problems=(str(exc),), model=model)
    blockers = [f"`## Options` already has a problem — fix it first: {p}" for p in existing.problems]
    if existing.options and not blockers:
        # The block is rewritten with the new options appended, so the writer must take what is
        # there now (a hand-written option with no Source line, say) — found out before any spend.
        try:
            render_options(existing.options)
        except OptionsError as exc:
            blockers.append(f"`## Options` cannot be rewritten as it stands — fix it first: {exc}")
    if blockers:
        return GenerationResult(
            status="failed", problems=tuple(blockers), model=model, session_spent_usd=spent
        )
    estimate = estimate_call(prompt, model, env=env)
    refusal = _refusal(estimate, spent, approve_usd)
    if refusal is not None:
        return GenerationResult(
            status="refused", refusal=refusal, model=model, estimate=estimate, session_spent_usd=spent
        )

    messages = list(prompt.messages)
    recording = RecordingLLMClient(client, ledger=ledger if ledger is not None else TokenLedger())
    row: dict[str, Any] = {"stage": STAGE, "model": model, "estimate_usd": round(estimate.usd, 6)}
    try:
        with recording.stage(STAGE):
            result = await recording.complete(
                messages,
                model=model,
                tools=[_TOOL],
                tool_choice=_TOOL.name,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
    except LLMError as exc:
        # Nothing says what a failed call cost, so the most it could have is charged.
        row.update(prompt_tokens=0, completion_tokens=0, cost_usd=estimate.usd, estimated=True)
        row["outcome"] = "call failed"
        _append_row(change, row)
        return GenerationResult(
            status="failed",
            problems=(f"the model call failed: {exc}",),
            model=model,
            cost_usd=estimate.usd,
            cost_estimated=True,
            estimate=estimate,
            session_spent_usd=spent,
        )

    used = result.model or model
    if result.cost_known:
        cost, estimated = result.cost_usd, False
    else:
        cost, estimated = estimate_cost_usd(result, messages, unpriced_rate_from_env(env)), True
    source = f"generated · {used} · ${cost:.4f}" + (" (estimated)" if estimated else "")
    options, problems = parse_reply(result, source=source)
    combined = existing.options + options
    if options:
        try:
            render_options(combined)
        except OptionsError as exc:
            options, problems = (), (str(exc),)
    row.update(
        model=used,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=cost,
        estimated=estimated,
        outcome="written" if options else "not written",
    )
    done = GenerationResult(
        status="failed",
        problems=problems,
        model=used,
        cost_usd=cost,
        cost_estimated=estimated,
        estimate=estimate,
        session_spent_usd=spent,
    )
    if not options:
        # Paid for, so kept: the reply (bounded by the output cap) can be copied in by hand (B132).
        row["reply"] = {
            "text": result.text,
            "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in result.tool_calls],
        }
        _append_row(change, row)
        kept = f"the model's reply is kept in {_ledger_path(change)} (its last row)"
        return replace(done, problems=(*done.problems, kept))
    write_options(change, combined)
    _append_row(change, row)
    # The writer and the reader must agree: what was written is read back before it is reported.
    if load_options(change).options != combined:
        return replace(done, problems=("the written block did not read back as written",))
    return replace(done, status="written", options=options)
