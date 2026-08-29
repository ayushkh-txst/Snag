"""OpenRouter cost resolution for arbitrary BYOK models.

Snag dispatches to whichever OpenRouter model the user configures (subject to
`deps.validate_model`'s allow-list elsewhere in the codebase — untouched by
this plan). Most of those models have no entry in the static `RATES` table,
so `OpenRouterCompletions` must never lose a real, successful response over
its own cost accounting. Resolution order, most to least authoritative:

1. OpenRouter's own reported cost for this exact call (`usage.cost` on the
   response, requested via `usage: {"include": true}` on the body) — the
   only figure that's actually correct for an arbitrary model.
2. The static `RATES` table, for models Snag has a rate for and the
   provider didn't report a cost.
3. `pricing.price_or_fallback`'s documented conservative estimate, which
   never raises `UnknownRate` (see test_llm_pricing.py and the window-1 fix,
   commit 1ab874f — this plan builds on that fix rather than replacing it
   with a bare `Decimal(0)`, since a silent zero is exactly the "looks free"
   failure `pricing.py` is written to avoid).

`ledger.record` is called on every path so the CostLedger stays the
authoritative running total for budget caps regardless of which pricing
source served this particular call.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from substrate.llm import CompletionRequest, Message, Role, TokenUsage
from substrate.llm.openrouter_adapter import OpenRouterCompletions
from substrate.llm.pricing import UnknownRate, price

KNOWN_MODEL = "openai/gpt-5.6-luna"
UNKNOWN_MODEL = "qwen/qwen3.8-flash"  # a real ACCEPTED_MODELS entry with no RATES row


def _request(**overrides: object) -> CompletionRequest:
    defaults: dict[str, object] = {
        "model": KNOWN_MODEL,
        "system": "Answer briefly.",
        "messages": (Message(Role.USER, "hi"),),
        "run_id": "test-run",
    }
    defaults.update(overrides)
    return CompletionRequest(**defaults)  # type: ignore[arg-type]


def _chat_completion(
    *, cost: str | None = None, prompt_tokens: int = 1_000_000, completion_tokens: int = 1_000_000
) -> dict[str, object]:
    usage: dict[str, object] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if cost is not None:
        usage["cost"] = cost
    return {
        "id": "gen-1",
        "model": KNOWN_MODEL,
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": usage,
    }


def _adapter(handler: object) -> OpenRouterCompletions:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OpenRouterCompletions(
        api_key="sk-or-test", transport=transport, clock=lambda: date(2026, 8, 23)
    )


@pytest.mark.asyncio
async def test_request_body_asks_openrouter_to_report_usage_cost() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_completion())

    await _adapter(handler).complete(_request())

    assert captured["usage"] == {"include": True}


@pytest.mark.asyncio
async def test_reported_cost_is_used_verbatim_when_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion(cost="0.041234"))

    response = await _adapter(handler).complete(_request(run_id="run-reported"))

    assert response.cost_usd == Decimal("0.041234")


@pytest.mark.asyncio
async def test_known_model_without_a_reported_cost_falls_back_to_rates() -> None:
    """No `usage.cost` on the response: for a model Snag has a rate for, the
    RATES price is used exactly (not the conservative unknown-model
    estimate)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion())

    response = await _adapter(handler).complete(_request(model=KNOWN_MODEL, run_id="run-known"))

    expected = price(
        TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        model=KNOWN_MODEL,
        when=date(2026, 8, 23),
    )
    assert response.cost_usd == expected
    assert response.cost_usd == Decimal("1.400000")


@pytest.mark.asyncio
async def test_unrated_model_never_raises_unknown_rate_and_still_gets_a_cost() -> None:
    """The core INFRA-03 guarantee: an arbitrary BYOK model with no RATES
    entry and no reported cost must not lose a real, successful response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={**_chat_completion(), "model": UNKNOWN_MODEL}
        )

    # Sanity: `price()` itself still raises for this model — the adapter's
    # fallback discipline is what's under test, not a change to `price`.
    with pytest.raises(UnknownRate):
        price(TokenUsage(input_tokens=1), model=UNKNOWN_MODEL, when=date(2026, 8, 23))

    response = await _adapter(handler).complete(_request(model=UNKNOWN_MODEL, run_id="run-unrated"))

    assert response.cost_usd > 0


@pytest.mark.asyncio
async def test_ledger_records_the_reported_cost_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion(cost="0.5"))

    adapter = _adapter(handler)
    await adapter.complete(_request(run_id="run-a"))

    assert adapter.ledger.total("run-a") == Decimal("0.5")


@pytest.mark.asyncio
async def test_ledger_records_the_rates_fallback_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion())

    adapter = _adapter(handler)
    await adapter.complete(_request(model=KNOWN_MODEL, run_id="run-b"))

    assert adapter.ledger.total("run-b") == Decimal("1.400000")


@pytest.mark.asyncio
async def test_ledger_records_the_unrated_fallback_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_chat_completion(), "model": UNKNOWN_MODEL})

    adapter = _adapter(handler)
    await adapter.complete(_request(model=UNKNOWN_MODEL, run_id="run-c"))

    assert adapter.ledger.total("run-c") > 0


@pytest.mark.asyncio
async def test_zero_reported_cost_is_trusted_not_treated_as_absent() -> None:
    """A genuinely free call (`cost: "0"`) must not be reinterpreted as
    'no cost reported' and fall through to RATES/estimate pricing — `0` is a
    valid, meaningful value here, not a falsy sentinel."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion(cost="0"))

    response = await _adapter(handler).complete(_request(run_id="run-zero"))

    assert response.cost_usd == Decimal("0")
