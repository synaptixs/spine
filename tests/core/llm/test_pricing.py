"""The two knobs that make the run cap count a model whose price Spine does not know (SSPN-180 / B87).

`SDLC_UNPRICED_USD_PER_MTOK` is the rate charged to the cap for a call with no known price;
`SDLC_MODEL_PRICES` gives one model its exact price so it is priced natively and never estimated. Both
fail at startup, naming the variable, like `SDLC_RUN_BUDGET_USD` does: a typo that silently meant "no
estimate" is the defect this track exists to close.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.core.llm import pricing
from orchestrator.core.llm.pricing import (
    DEFAULT_UNPRICED_RATE,
    UnpricedRate,
    price_overrides_from_env,
    unpriced_rate_from_env,
)


def test_the_default_rate_is_the_default_models_price() -> None:
    """$5 in / $25 out per million tokens is Claude Opus 5's price, the model Spine defaults to."""
    assert UnpricedRate(input_per_mtok=5.0, output_per_mtok=25.0) == DEFAULT_UNPRICED_RATE
    assert unpriced_rate_from_env({}) == DEFAULT_UNPRICED_RATE
    assert unpriced_rate_from_env({"SDLC_UNPRICED_USD_PER_MTOK": "  "}) == DEFAULT_UNPRICED_RATE


def test_the_rate_is_read_as_input_then_output() -> None:
    assert unpriced_rate_from_env({"SDLC_UNPRICED_USD_PER_MTOK": "7, 35"}) == UnpricedRate(7.0, 35.0)
    assert unpriced_rate_from_env({"SDLC_UNPRICED_USD_PER_MTOK": "0.5,1.5"}) == UnpricedRate(0.5, 1.5)


@pytest.mark.parametrize("raw", ["abc", "5", "5,25,1", "-1,2", "5,-2", "nan,3", "3,inf", ",", "5,"])
def test_a_malformed_rate_fails_naming_the_variable(raw: str) -> None:
    with pytest.raises(ValueError, match="SDLC_UNPRICED_USD_PER_MTOK"):
        unpriced_rate_from_env({"SDLC_UNPRICED_USD_PER_MTOK": raw})


def test_no_overrides_when_unset() -> None:
    assert price_overrides_from_env({}) == {}
    assert price_overrides_from_env({"SDLC_MODEL_PRICES": "  "}) == {}


def test_an_inline_override_is_converted_to_litellms_per_token_shape() -> None:
    raw = json.dumps({"gpt-6-astra": {"input_per_mtok": 10, "output_per_mtok": 40, "provider": "openai"}})
    entries = price_overrides_from_env({"SDLC_MODEL_PRICES": raw})
    assert entries == {
        "gpt-6-astra": {
            "input_cost_per_token": pytest.approx(1e-5),
            "output_cost_per_token": pytest.approx(4e-5),
            "litellm_provider": "openai",
            "mode": "chat",
        }
    }


def test_the_provider_is_optional_because_litellm_infers_some_names() -> None:
    raw = json.dumps({"claude-sonnet-5-5": {"input_per_mtok": 3, "output_per_mtok": 15}})
    entry = price_overrides_from_env({"SDLC_MODEL_PRICES": raw})["claude-sonnet-5-5"]
    assert "litellm_provider" not in entry
    assert entry["input_cost_per_token"] == pytest.approx(3e-6)


def test_a_path_to_a_json_file_works(tmp_path: Path) -> None:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"acme-7b": {"input_per_mtok": 1, "output_per_mtok": 2}}), encoding="utf-8")
    assert "acme-7b" in price_overrides_from_env({"SDLC_MODEL_PRICES": str(path)})


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "/no/such/file.json",
        json.dumps(["gpt-6"]),
        json.dumps({"m": 5}),
        json.dumps({"m": {"input_per_mtok": 1}}),
        json.dumps({"m": {"input_per_mtok": -1, "output_per_mtok": 2}}),
        json.dumps({"m": {"input_per_mtok": "1", "output_per_mtok": 2}}),
        json.dumps({"m": {"input_per_mtok": True, "output_per_mtok": 2}}),
        json.dumps({"m": {"input_per_mtok": 1, "output_per_mtok": 2, "provider": 7}}),
    ],
)
def test_a_malformed_override_fails_naming_the_variable(raw: str) -> None:
    with pytest.raises(ValueError, match="SDLC_MODEL_PRICES"):
        price_overrides_from_env({"SDLC_MODEL_PRICES": raw})


def test_a_registered_override_prices_a_name_litellm_cannot_route() -> None:
    """The real library, no network: without a provider `gpt-6-astra` raises; with one it is priced."""
    litellm = pytest.importorskip("litellm")
    from litellm import ModelResponse

    raw = json.dumps({"gpt-6-astra": {"input_per_mtok": 10, "output_per_mtok": 40, "provider": "openai"}})
    entries = price_overrides_from_env({"SDLC_MODEL_PRICES": raw})
    try:
        pricing.register_price_overrides(litellm, entries)
        response = ModelResponse(
            model="gpt-6-astra",
            choices=[{"message": {"role": "assistant", "content": "x"}, "finish_reason": "stop", "index": 0}],
            usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        )
        assert float(litellm.completion_cost(completion_response=response)) == pytest.approx(0.03)
    finally:
        litellm.model_cost.pop("gpt-6-astra", None)
