"""Which models this pipeline can be pointed at, and what each one costs you.

Every stage was pinned to one hardcoded string — `claude-sonnet-4-6` in four
separate modules, three of them with no way to override it. Thirteen end-to-end runs
used that model and only that model, so nothing here was ever a choice; it was a
default nobody had revisited.

**The catalog is read from LiteLLM's installed data, not written from memory.**
`litellm.models_by_provider` and `litellm.model_cost` ship with the client this repo
already depends on, so the ids, context windows, prices and — the one that matters
here — tool-calling support are facts about the installed version rather than a list
that silently rots. Upgrading `litellm` brings new models with no edit here.

**Installed means installed.** Left to its default, LiteLLM does not read the price map
it ships: every import fetches the current one from GitHub and falls back to the bundled
copy only if that fails. So the build document's cost table depended on the network — the
same commit planned twice in CI, one fetch succeeded and one did not, and `sdlc_shapes.py`
caught two documents that differed only in §11 (4,175 models fetched, 2,982 bundled).
`sdlc plan` is trusted because the same commit gives the same document; a price list that
moves with the network breaks that, and costs an offline machine a timeout on every
import. So this module, which ``orchestrator.core.llm`` imports before anything else,
pins LiteLLM to its bundled map — with ``setdefault``, so an operator who wants today's
prices can still set ``LITELLM_LOCAL_MODEL_COST_MAP=False``.

**Tool calling is not optional any more.** Codegen forces a ``submit_files`` call and
the judge forces ``submit_verdict``; on a model without function calling both fall
back to parsing prose, which is the failure this pipeline spent a whole cycle
removing. ``supports_tools`` is therefore a hard filter in ``recommended``, not a
footnote.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

# Before any `import litellm`, which reads it at import time. See "Installed means installed".
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# The stage defaults. Anthropic ids are exact strings with no date suffix — appending
# one 404s. Opus 5 is the default for every stage because model choice is the
# operator's call to downgrade, not ours to make quietly on their behalf.
DEFAULT_MODEL = "claude-opus-5"

# Per-stage override, then the global one, then the default above. Codegen keeps
# reading ORCHESTRATOR_INTAKE_MODEL so a single-variable setup still drives the whole
# pipeline, which is the behaviour `resolve_codegen_model` was written for. Ideation
# (`sdlc.ideation`, generated solution options) reads it too, for the same reason, but has
# its own variable so it can be pointed at a cheap or local model without moving intake.
STAGE_ENV: dict[str, tuple[str, ...]] = {
    "codegen": ("SDLC_CODEGEN_MODEL", "ORCHESTRATOR_INTAKE_MODEL", "ORCHESTRATOR_MODEL"),
    "judge": ("SDLC_JUDGE_MODEL", "ORCHESTRATOR_MODEL"),
    "intake": ("ORCHESTRATOR_INTAKE_MODEL", "ORCHESTRATOR_MODEL"),
    "ideation": ("ORCHESTRATOR_IDEATION_MODEL", "ORCHESTRATOR_INTAKE_MODEL", "ORCHESTRATOR_MODEL"),
}


def resolve(stage: str, override: str | None = None) -> str:
    """The model a stage should use: explicit flag > stage env > global env > default."""
    if override:
        return override
    for name in STAGE_ENV.get(stage, ()):
        value = os.getenv(name)
        if value:
            return value
    return DEFAULT_MODEL


# Codegen's output ceiling: the most it asks any model for, thinking plus reply.
#
# Without an explicit cap the provider default (~4k tokens) applies, and a large generated test
# file gets truncated mid-JSON — the "model output was not a JSON object" failures that sank
# author_tests in runs #16/#18. Raised from 16k with the move off Sonnet 4.6: on the current
# Claude models thinking is ON unless disabled, and this cap covers thinking *plus* the reply.
#
# **A ceiling, not a request (B172).** Asking every model for 32,000 broke every model that allows
# fewer: `gpt-4o` allows 16,384 and rejects the whole request, so an OpenAI-only setup could not
# build at all. `codegen_output_cap` asks for the smaller of this and the model's own limit.
CODEGEN_OUTPUT_CEILING = 32_000
# The escape hatch for a model the installed table does not know, or knows wrongly (a proxy, a
# fine-tune): a positive integer here is sent as-is.
CODEGEN_MAX_TOKENS_ENV = "SDLC_CODEGEN_MAX_TOKENS"
# Unusable override values already warned about. Module-level and not persisted, like the client's
# once-per-process warnings: the cap is computed per codegen call, and once is news, every call is noise.
_OVERRIDE_WARNED: set[str] = set()


def name_candidates(model_id: str) -> tuple[str, ...]:
    """The keys one model id may be listed under in LiteLLM's table, most specific first.

    LiteLLM dispatches on ``openai/gpt-4o`` but lists it as ``gpt-4o``, and a tag
    (``llama3:8b``) is listed without it. An exact-key lookup therefore called a configured
    ``openai/gpt-4o`` "not in the installed catalog" and priced nothing (B183). The rule is the
    one the price check already used (B87): as given, without the provider prefix, and each of
    those without a ``:tag``.
    """
    bare = model_id.split("/", 1)[-1]
    names = (model_id, bare, model_id.split(":", 1)[0], bare.split(":", 1)[0])
    return tuple(dict.fromkeys(n for n in names if n))


def _lookup(model_id: str) -> tuple[str, dict[str, object]] | None:
    """The table key and entry for ``model_id``, tried under each of its `name_candidates`."""
    import litellm

    for name in name_candidates(model_id):
        info = litellm.model_cost.get(name)
        if isinstance(info, dict) and info:
            return name, info
    return None


@dataclass(frozen=True)
class ModelInfo:
    """What the installed LiteLLM knows about one model."""

    id: str
    provider: str
    context_tokens: int | None
    input_usd_per_mtok: float | None
    output_usd_per_mtok: float | None
    supports_tools: bool
    # The most output tokens the model accepts per call, from the table's `max_output_tokens`.
    # ``None`` when the table does not say. Last, with a default, so positional construction holds.
    max_output_tokens: int | None = None

    @property
    def usable(self) -> bool:
        """Whether this pipeline can drive it.

        Tool calling is the whole test. Everything else — context, price — is a
        tradeoff; a model that cannot be forced to call a tool silently degrades
        codegen and the judge back to parsing prose out of a text reply.
        """
        return self.supports_tools


def describe(model_id: str) -> ModelInfo | None:
    """Look one model up, or ``None`` when LiteLLM has never heard of it.

    Found under any of its `name_candidates`; the returned ``id`` is the key it was found
    under, so ``describe("openai/gpt-4o").id == "gpt-4o"`` — the name the table prices.
    """
    found = _lookup(model_id)
    if found is None:
        return None
    key, info = found
    return _from_litellm(key, info)


def codegen_output_cap(model_id: str, env: Mapping[str, str] | None = None) -> tuple[int, str]:
    """The ``max_tokens`` codegen sends ``model_id``, and the basis for it in words.

    ``SDLC_CODEGEN_MAX_TOKENS`` when it holds a positive integer; else the smaller of
    `CODEGEN_OUTPUT_CEILING` and the model's own ``max_output_tokens``; else the ceiling, for a
    model the installed table does not know or gives no limit for. The default model is
    unaffected: ``claude-opus-5`` allows 128,000, so it is still asked for 32,000.
    """
    environ = os.environ if env is None else env
    raw = (environ.get(CODEGEN_MAX_TOKENS_ENV) or "").strip()
    ignored = ""
    if raw:
        if raw.isdigit() and int(raw) > 0:
            return int(raw), f"{CODEGEN_MAX_TOKENS_ENV}={raw}"
        ignored = f"; {CODEGEN_MAX_TOKENS_ENV}={raw!r} ignored: not a positive integer"
        if raw not in _OVERRIDE_WARNED:
            _OVERRIDE_WARNED.add(raw)
            logging.getLogger("orchestrator.core.llm").warning(
                "%s=%r is not a positive integer, so it is ignored and codegen's cap comes from the model.",
                CODEGEN_MAX_TOKENS_ENV,
                raw,
                extra={"event": "llm.codegen_max_tokens_ignored"},
            )
    info = describe(model_id)
    if info is None:
        return CODEGEN_OUTPUT_CEILING, (
            f"`{model_id}` is not in the installed catalog, so codegen's "
            f"{CODEGEN_OUTPUT_CEILING:,} ceiling{ignored}"
        )
    limit = info.max_output_tokens
    if limit is not None and limit < CODEGEN_OUTPUT_CEILING:
        return limit, f"`{info.id}` allows {limit:,} output tokens{ignored}"
    stated = f", `{info.id}` allows {limit:,}" if limit is not None else ""
    return CODEGEN_OUTPUT_CEILING, f"codegen's {CODEGEN_OUTPUT_CEILING:,} ceiling{stated}{ignored}"


def catalog(provider: str | None = None, *, chat_only: bool = True) -> list[ModelInfo]:
    """Every model the installed LiteLLM knows, newest-looking last.

    ``provider`` filters to one vendor (``"anthropic"``, ``"openai"``, …). Sorted by
    id so related families group together; no attempt is made to guess which is
    "latest", because a sort order is not a release date and pretending otherwise is
    how a stale list starts.
    """
    import litellm

    out: list[ModelInfo] = []
    for name, ids in litellm.models_by_provider.items():
        if provider and name != provider:
            continue
        for model_id in ids:
            info = litellm.model_cost.get(model_id)
            if not info:
                continue
            if chat_only and info.get("mode") != "chat":
                continue
            out.append(_from_litellm(model_id, info, provider=name))
    return sorted(out, key=lambda m: (m.provider, m.id))


def _from_litellm(model_id: str, info: dict[str, object], *, provider: str = "") -> ModelInfo:
    def _rate(key: str) -> float | None:
        value = info.get(key)
        # LiteLLM stores per-token; per-million is the unit every price list uses.
        return round(float(value) * 1_000_000, 2) if isinstance(value, int | float) else None

    context = info.get("max_input_tokens")
    output = info.get("max_output_tokens")
    return ModelInfo(
        id=model_id,
        provider=provider or str(info.get("litellm_provider") or ""),
        context_tokens=int(context) if isinstance(context, int | float) else None,
        input_usd_per_mtok=_rate("input_cost_per_token"),
        output_usd_per_mtok=_rate("output_cost_per_token"),
        supports_tools=bool(info.get("supports_function_calling")),
        max_output_tokens=int(output) if isinstance(output, int | float) and output > 0 else None,
    )


def render(models: list[ModelInfo], *, current: str = "") -> str:
    """One table: what you can point this at, and what it costs."""
    if not models:
        return "No models found. Is `litellm` installed?"
    lines = ["| Model | Provider | Context | $/Mtok in | $/Mtok out | Tools |", "|---|---|---|---|---|---|"]
    for m in models:
        mark = " ←" if m.id == current else ""
        ctx = f"{m.context_tokens:,}" if m.context_tokens else "—"
        cin = f"{m.input_usd_per_mtok:.2f}" if m.input_usd_per_mtok is not None else "—"
        cout = f"{m.output_usd_per_mtok:.2f}" if m.output_usd_per_mtok is not None else "—"
        # A model without tool calling is listed but marked: it will run, and codegen
        # and the judge will quietly drop to parsing prose. Say so in the table rather
        # than letting someone discover it from a bad run.
        tools = "yes" if m.supports_tools else "**NO**"
        lines.append(f"| `{m.id}`{mark} | {m.provider} | {ctx} | {cin} | {cout} | {tools} |")
    return "\n".join(lines)


__all__ = [
    "CODEGEN_MAX_TOKENS_ENV",
    "CODEGEN_OUTPUT_CEILING",
    "DEFAULT_MODEL",
    "STAGE_ENV",
    "ModelInfo",
    "catalog",
    "codegen_output_cap",
    "describe",
    "name_candidates",
    "render",
    "resolve",
]
