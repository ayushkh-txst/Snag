"""Pricing is money arithmetic, so it gets exact assertions, not approximate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from substrate.llm import TokenUsage
from substrate.llm.pricing import CostLedger, UnknownRate, price, price_or_fallback, rate_for


def test_fresh_input_and_output_priced_separately() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert price(usage, model="claude-opus-5", when=date(2026, 8, 11)) == Decimal("30.000000")


def test_cache_reads_are_a_tenth_of_fresh_input() -> None:
    fresh = TokenUsage(input_tokens=1_000_000)
    cached = TokenUsage(cache_read_tokens=1_000_000)
    on = date(2026, 8, 11)
    assert price(cached, model="claude-opus-5", when=on) == Decimal("0.500000")
    assert price(fresh, model="claude-opus-5", when=on) == Decimal("5.000000")


def test_cache_writes_cost_more_than_fresh_input() -> None:
    usage = TokenUsage(cache_write_tokens=1_000_000)
    assert price(usage, model="claude-opus-5", when=date(2026, 8, 11)) == Decimal("6.250000")


def test_rate_lookup_is_as_of_a_date() -> None:
    """The introductory Sonnet rate expires mid-project. A query priced in
    August must stay priced at August's rate when re-read in September."""
    august = rate_for("claude-sonnet-5", date(2026, 8, 11))
    september = rate_for("claude-sonnet-5", date(2026, 9, 15))
    assert august.input_per_mtok == Decimal("2.00")
    assert september.input_per_mtok == Decimal("3.00")


def test_effective_to_is_exclusive() -> None:
    """Same convention as section_versions: the boundary day belongs to the
    NEW row, so there is never a day covered by two rates."""
    assert rate_for("claude-sonnet-5", date(2026, 8, 31)).input_per_mtok == Decimal("2.00")
    assert rate_for("claude-sonnet-5", date(2026, 9, 1)).input_per_mtok == Decimal("3.00")


def test_unknown_model_raises_rather_than_defaulting_to_free() -> None:
    with pytest.raises(UnknownRate):
        price(TokenUsage(input_tokens=10), model="gpt-hypothetical", when=date(2026, 8, 11))


def test_ledger_accumulates_per_run() -> None:
    ledger = CostLedger()
    ledger.record("run-a", Decimal("0.01"))
    ledger.record("run-a", Decimal("0.02"))
    ledger.record("run-b", Decimal("0.05"))
    assert ledger.total("run-a") == Decimal("0.03")
    assert ledger.total("run-b") == Decimal("0.05")
    assert ledger.total() == Decimal("0.08")


def test_sub_cent_costs_survive_rounding() -> None:
    """A single query is genuinely worth a fraction of a cent. Quantizing to
    two places would floor every real query to zero and make the ledger
    report nothing no matter how many calls it saw."""
    usage = TokenUsage(input_tokens=1_200, output_tokens=300)
    cost = price(usage, model="claude-opus-5", when=date(2026, 8, 11))
    assert cost > Decimal(0)
    assert cost < Decimal("0.02")


def test_free_openrouter_model_is_an_asserted_zero() -> None:
    """The `:free` suffix is OpenRouter's own promise of $0 — a rate row that
    says so explicitly, not an UnknownRate silently defaulted to nothing."""
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = price(usage, model="google/gemma-4-26b-a4b-it:free", when=date(2026, 8, 23))
    assert cost == Decimal("0.000000")


def test_openrouter_embedding_model_has_no_output_price() -> None:
    """Embedding calls never produce output tokens, but the rate row still
    needs an output_per_mtok — zero, since none is ever billed."""
    usage = TokenUsage(input_tokens=1_000_000)
    cost = price(usage, model="openai/text-embedding-3-small@512", when=date(2026, 8, 23))
    assert cost == Decimal("0.020000")


def test_liquid_lfm_free_model_is_an_asserted_zero() -> None:
    """A fallback free model, tried when gemma's shared upstream pool is
    congested (confirmed live against OpenRouter on 2026-08-23)."""
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = price(usage, model="liquid/lfm-2.5-2.6b:free", when=date(2026, 8, 23))
    assert cost == Decimal("0.000000")


def test_gpt_5_6_luna_pricing() -> None:
    """Paid fallback — no shared-pool congestion, a few cents per 1,000
    turns. Rate verified against OpenRouter's own /models pricing."""
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = price(usage, model="openai/gpt-5.6-luna", when=date(2026, 8, 23))
    assert cost == Decimal("1.400000")


def test_price_or_fallback_never_raises_for_an_unpriced_model() -> None:
    """Found live on 2026-08-28: two of Snag's ACCEPTED_MODELS (BYOK,
    arbitrary OpenRouter models) have no RATES entry. `price()` itself must
    stay exact and raising (tested above); the adapters' own post-call cost
    accounting must not crash a real, successful response over it."""
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    when = date(2026, 8, 28)
    with pytest.raises(UnknownRate):
        price(usage, model="qwen/qwen3.8-flash", when=when)
    cost, unknown_pricing = price_or_fallback(usage, model="qwen/qwen3.8-flash", when=when)
    assert unknown_pricing is True
    assert cost == Decimal("4.000000")


def test_price_or_fallback_matches_price_for_a_known_model() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    when = date(2026, 8, 23)
    exact = price(usage, model="openai/gpt-5.6-luna", when=when)
    cost, unknown_pricing = price_or_fallback(usage, model="openai/gpt-5.6-luna", when=when)
    assert unknown_pricing is False
    assert cost == exact
