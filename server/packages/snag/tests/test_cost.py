"""snag.cost against a faked OpenRouter /models catalogue — no live network.

httpx.MockTransport, same idiom as substrate's own
test_openrouter_adapter.py: no new test dependency, and it exercises the
real httpx.AsyncClient code path rather than mocking it away.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from snag import cost as cost_module
from snag.cost import ModelPricing, estimate_cost, estimate_scan_cost, fetch_model_pricing

PRICED_MODEL = "some/priced-model"
UNPRICED_MODEL = "some/unpriced-model"


@pytest.fixture(autouse=True)
def _clear_pricing_cache() -> None:
    """`_PRICING_CACHE` is module-level and process-lifetime by design (that
    is the whole point of Task 2's third behaviour); tests need a clean
    slate each time so one test's cached lookup can't hide another test's
    assertion about call counts."""
    cost_module._PRICING_CACHE.clear()


def _models_catalogue(handler_calls: list[int] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if handler_calls is not None:
            handler_calls.append(1)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": PRICED_MODEL,
                        "pricing": {"prompt": "0.0000005", "completion": "0.0000015"},
                    },
                    {
                        "id": "some/other-model",
                        "pricing": {"prompt": "0.000001", "completion": "0.000003"},
                    },
                ]
            },
        )

    return httpx.MockTransport(handler)


async def test_fetch_model_pricing_reads_prompt_and_completion_price() -> None:
    transport = _models_catalogue()
    pricing = await fetch_model_pricing(PRICED_MODEL, transport=transport)
    assert pricing == ModelPricing(
        model=PRICED_MODEL,
        prompt_per_token=Decimal("0.0000005"),
        completion_per_token=Decimal("0.0000015"),
    )


async def test_fetch_model_pricing_returns_none_for_a_model_absent_from_the_catalogue() -> None:
    transport = _models_catalogue()
    pricing = await fetch_model_pricing(UNPRICED_MODEL, transport=transport)
    assert pricing is None


async def test_estimate_scan_cost_reads_real_pricing_and_returns_a_positive_decimal() -> None:
    transport = _models_catalogue()
    cost, unknown_pricing = await estimate_scan_cost(
        PRICED_MODEL,
        calls=300,
        avg_input_tokens=800,
        avg_output_tokens=200,
        transport=transport,
    )
    assert isinstance(cost, Decimal)
    assert cost > 0
    assert unknown_pricing is False
    # 300 * (800 * 0.0000005 + 200 * 0.0000015) = 300 * 0.0007 = 0.21
    assert cost == Decimal("0.210000")


async def test_estimate_scan_cost_never_raises_for_an_unknown_model_and_flags_it() -> None:
    """A model absent from OpenRouter's own catalogue (and therefore never
    in substrate.llm.pricing.RATES either) must fall back, not crash a
    scan-config screen."""
    transport = _models_catalogue()
    cost, unknown_pricing = await estimate_scan_cost(
        UNPRICED_MODEL,
        calls=300,
        avg_input_tokens=800,
        avg_output_tokens=200,
        transport=transport,
    )
    assert isinstance(cost, Decimal)
    assert cost > 0
    assert unknown_pricing is True


async def test_pricing_is_cached_per_model_and_not_refetched() -> None:
    handler_calls: list[int] = []
    transport = _models_catalogue(handler_calls)

    first = await fetch_model_pricing(PRICED_MODEL, transport=transport)
    second = await fetch_model_pricing(PRICED_MODEL, transport=transport)

    assert first == second
    assert len(handler_calls) == 1


def test_estimate_cost_is_a_pure_decimal_calculation() -> None:
    pricing = ModelPricing(
        model=PRICED_MODEL,
        prompt_per_token=Decimal("0.000001"),
        completion_per_token=Decimal("0.000003"),
    )
    result = estimate_cost(pricing, calls=100, avg_input_tokens=1000, avg_output_tokens=500)
    # 100 * (1000 * 0.000001 + 500 * 0.000003) = 100 * 0.0025 = 0.25
    assert result == Decimal("0.250000")
