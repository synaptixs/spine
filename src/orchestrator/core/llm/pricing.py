"""What the run cap charges for a model whose price Spine does not know (SSPN-180 / B87).

The cap (``SDLC_RUN_BUDGET_USD``) counts what each call *recorded*, and a call on a model with no known
price records $0 — so a run on a model the pinned price table does not list could spend without limit
while the budget read $0. Two models of the same kind failed in two ways, which is why this is more than
a missing flag: `gpt-6-astra` makes the price lookup raise (B81 marks it unpriced), while the Claude 5.5
family (`claude-sonnet-5-5`, ...) makes it return 0.0 *without* raising, so nothing marked them at all.

Two knobs, both read once, at construction, and both failing loudly there — a typo that silently meant
"no estimate" is the defect being closed:

``SDLC_UNPRICED_USD_PER_MTOK`` (``"5,25"``)
    The rate, in dollars per million tokens, input then output, the **budget** charges for a call with no
    known price. The default is Claude Opus 5's price, the model Spine defaults to, and it sits in the top
    tenth of the pinned table's chat models by price (which entries count moves the exact percentile a
    little, so none is claimed here). A much higher default would stop a run on a cheap model at a small
    fraction of its real spend, and a cap that trips that early is its own bug. It is an estimate and every
    surface says so.

``SDLC_MODEL_PRICES`` (a JSON object, or the path of a JSON file)
    The exact price of a model, ``{"<model>": {"input_per_mtok": 5, "output_per_mtok": 25}}``, registered
    with litellm so the *standard* path prices the model and nothing is estimated. ``"provider"`` is
    optional because litellm infers it for some names (the Claude ones) and cannot for others
    (`gpt-6-astra` raises "provider not provided" without ``"provider": "openai"``) — measured against
    the real library, not assumed.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RATE_ENV = "SDLC_UNPRICED_USD_PER_MTOK"
PRICES_ENV = "SDLC_MODEL_PRICES"


@dataclass(frozen=True)
class UnpricedRate:
    """Dollars per million tokens charged to the run budget for a call with no known price."""

    input_per_mtok: float
    output_per_mtok: float


DEFAULT_UNPRICED_RATE = UnpricedRate(input_per_mtok=5.0, output_per_mtok=25.0)


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def unpriced_rate_from_env(env: Mapping[str, str] | None = None) -> UnpricedRate:
    """``SDLC_UNPRICED_USD_PER_MTOK`` as ``input,output``; the default when unset or blank."""
    raw = _env(env).get(RATE_ENV, "").strip()
    if not raw:
        return DEFAULT_UNPRICED_RATE
    parts = [p.strip() for p in raw.split(",")]
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = []
    if len(values) != 2 or not all(math.isfinite(v) and v >= 0 for v in values):
        # `nan` would compare false against every spend and silently mean "no estimate".
        raise ValueError(
            f"{RATE_ENV} must be two dollar rates per million tokens, 'input,output', got {raw!r}"
        )
    return UnpricedRate(input_per_mtok=values[0], output_per_mtok=values[1])


def _rate(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise ValueError(
            f"{PRICES_ENV}: {where} must be a number of dollars per million tokens, got {value!r}"
        )
    return float(value)


def price_overrides_from_env(env: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """``SDLC_MODEL_PRICES`` converted to litellm's per-token entries; empty when unset."""
    raw = _env(env).get(PRICES_ENV, "").strip()
    if not raw:
        return {}
    try:
        text = raw if raw.startswith(("{", "[")) else Path(raw).expanduser().read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{PRICES_ENV} must be a JSON object or the path of a JSON file ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{PRICES_ENV} must be a JSON object of model -> prices, got {type(data).__name__}")
    entries: dict[str, dict[str, Any]] = {}
    for model, spec in data.items():
        if not isinstance(spec, dict) or "input_per_mtok" not in spec or "output_per_mtok" not in spec:
            raise ValueError(f"{PRICES_ENV}: {model!r} needs 'input_per_mtok' and 'output_per_mtok'")
        entry: dict[str, Any] = {
            "input_cost_per_token": _rate(spec["input_per_mtok"], f"{model!r} input_per_mtok") / 1e6,
            "output_cost_per_token": _rate(spec["output_per_mtok"], f"{model!r} output_per_mtok") / 1e6,
            "mode": "chat",
        }
        provider = spec.get("provider")
        if provider is not None:
            if not isinstance(provider, str) or not provider:
                raise ValueError(f"{PRICES_ENV}: {model!r} provider must be a non-empty string")
            entry["litellm_provider"] = provider
        entries[str(model)] = entry
    return entries


def register_price_overrides(litellm: Any, entries: Mapping[str, Mapping[str, Any]]) -> None:
    """Give litellm the configured prices, so its own cost lookup answers for those models."""
    register = getattr(litellm, "register_model", None)
    if entries and register is not None:
        register(dict(entries))


__all__ = [
    "DEFAULT_UNPRICED_RATE",
    "PRICES_ENV",
    "RATE_ENV",
    "UnpricedRate",
    "price_overrides_from_env",
    "register_price_overrides",
    "unpriced_rate_from_env",
]
