"""Pre-dispatch cost estimation, sourced from OpenRouter's own pricing.

BYOK means arbitrary OpenRouter models, and `substrate.llm.pricing`'s static
RATES table only knows the handful of models this project has priced by
hand — asking it for anything else raises `UnknownRate` (by design: see its
own docstring). That discipline is right for *post-call* cost accounting,
where a real response must never be lost over a pricing gap
(`price_or_fallback` handles that, adapter-side). This module is a
different concern: a *pre-dispatch* estimate for a scan-config screen, made
before any call happens, for any model a BYOK key might name. It reads
OpenRouter's `/models` catalogue directly rather than duplicating the RATES
table, and never raises for a model the catalogue doesn't list either — it
falls back to a conservative flat estimate and says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Deliberately pessimistic, same discipline as
# substrate.llm.pricing.FALLBACK_INPUT_PER_MTOK/FALLBACK_OUTPUT_PER_MTOK:
# these sit above real per-token OpenRouter prices, so a model missing from
# the catalogue estimates high rather than accidentally looking cheap (or
# free, which is the one wrong answer nobody double-checks).
FALLBACK_PROMPT_PER_TOKEN = Decimal("0.000001")  # $1 / 1M tokens
FALLBACK_COMPLETION_PER_TOKEN = Decimal("0.000003")  # $3 / 1M tokens

_QUANTIZE = Decimal("0.000001")

# Cached per model for the process, so a scan-config screen re-rendering an
# estimate doesn't refetch OpenRouter's whole catalogue on every keystroke.
# Only successful lookups are cached; a model absent today might be added to
# the catalogue later in the same process's lifetime.
_PRICING_CACHE: dict[str, ModelPricing] = {}


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-token USD prices for one model, as OpenRouter itself reports
    them — not the static RATES table."""

    model: str
    prompt_per_token: Decimal
    completion_per_token: Decimal


async def fetch_model_pricing(
    model: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> ModelPricing | None:
    """GET OpenRouter's `/models` catalogue and read this model's per-token
    prompt/completion price. Returns `None` when the model isn't listed —
    BYOK is arbitrary models, and OpenRouter's catalogue is not exhaustive
    of what a given key can actually call."""
    if model in _PRICING_CACHE:
        return _PRICING_CACHE[model]

    async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
        response = await client.get(OPENROUTER_MODELS_URL)
    response.raise_for_status()

    for entry in response.json().get("data", []):
        if entry.get("id") == model:
            pricing_json = entry.get("pricing") or {}
            result = ModelPricing(
                model=model,
                prompt_per_token=Decimal(str(pricing_json.get("prompt", "0"))),
                completion_per_token=Decimal(str(pricing_json.get("completion", "0"))),
            )
            _PRICING_CACHE[model] = result
            return result
    return None


def estimate_cost(
    pricing: ModelPricing, *, calls: int, avg_input_tokens: int, avg_output_tokens: int
) -> Decimal:
    """Projected spend for `calls` calls at `pricing`'s per-token rates.
    Decimal throughout — this is money, not a metric."""
    per_call = (
        pricing.prompt_per_token * avg_input_tokens
        + pricing.completion_per_token * avg_output_tokens
    )
    return (per_call * calls).quantize(_QUANTIZE)


async def estimate_scan_cost(
    model: str,
    *,
    calls: int,
    avg_input_tokens: int,
    avg_output_tokens: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[Decimal, bool]:
    """Pre-dispatch estimate for a scan-config screen. Combines
    `fetch_model_pricing` + `estimate_cost`; returns
    `(estimated_usd, unknown_pricing)` — the same two-value shape as
    `substrate.llm.pricing.price_or_fallback`, for the same reason: a BYOK
    model absent from OpenRouter's own catalogue must not raise, and must
    not silently look free either."""
    pricing = await fetch_model_pricing(model, transport=transport)
    if pricing is None:
        fallback = ModelPricing(
            model=model,
            prompt_per_token=FALLBACK_PROMPT_PER_TOKEN,
            completion_per_token=FALLBACK_COMPLETION_PER_TOKEN,
        )
        cost = estimate_cost(
            fallback,
            calls=calls,
            avg_input_tokens=avg_input_tokens,
            avg_output_tokens=avg_output_tokens,
        )
        return cost, True

    cost = estimate_cost(
        pricing, calls=calls, avg_input_tokens=avg_input_tokens, avg_output_tokens=avg_output_tokens
    )
    return cost, False
