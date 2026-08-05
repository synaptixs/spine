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

**Tool calling is not optional any more.** Codegen forces a ``submit_files`` call and
the judge forces ``submit_verdict``; on a model without function calling both fall
back to parsing prose, which is the failure this pipeline spent a whole cycle
removing. ``supports_tools`` is therefore a hard filter in ``recommended``, not a
footnote.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The stage defaults. Anthropic ids are exact strings with no date suffix — appending
# one 404s. Opus 5 is the default for every stage because model choice is the
# operator's call to downgrade, not ours to make quietly on their behalf.
DEFAULT_MODEL = "claude-opus-5"

# Per-stage override, then the global one, then the default above. Codegen keeps
# reading ORCHESTRATOR_INTAKE_MODEL so a single-variable setup still drives the whole
# pipeline, which is the behaviour `resolve_codegen_model` was written for.
STAGE_ENV: dict[str, tuple[str, ...]] = {
    "codegen": ("SDLC_CODEGEN_MODEL", "ORCHESTRATOR_INTAKE_MODEL", "ORCHESTRATOR_MODEL"),
    "judge": ("SDLC_JUDGE_MODEL", "ORCHESTRATOR_MODEL"),
    "intake": ("ORCHESTRATOR_INTAKE_MODEL", "ORCHESTRATOR_MODEL"),
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


@dataclass(frozen=True)
class ModelInfo:
    """What the installed LiteLLM knows about one model."""

    id: str
    provider: str
    context_tokens: int | None
    input_usd_per_mtok: float | None
    output_usd_per_mtok: float | None
    supports_tools: bool

    @property
    def usable(self) -> bool:
        """Whether this pipeline can drive it.

        Tool calling is the whole test. Everything else — context, price — is a
        tradeoff; a model that cannot be forced to call a tool silently degrades
        codegen and the judge back to parsing prose out of a text reply.
        """
        return self.supports_tools


def describe(model_id: str) -> ModelInfo | None:
    """Look one model up, or ``None`` when LiteLLM has never heard of it."""
    import litellm

    info = litellm.model_cost.get(model_id)
    if not info:
        return None
    return _from_litellm(model_id, info)


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
    return ModelInfo(
        id=model_id,
        provider=provider or str(info.get("litellm_provider") or ""),
        context_tokens=int(context) if isinstance(context, int | float) else None,
        input_usd_per_mtok=_rate("input_cost_per_token"),
        output_usd_per_mtok=_rate("output_cost_per_token"),
        supports_tools=bool(info.get("supports_function_calling")),
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


__all__ = ["DEFAULT_MODEL", "STAGE_ENV", "ModelInfo", "catalog", "describe", "render", "resolve"]
