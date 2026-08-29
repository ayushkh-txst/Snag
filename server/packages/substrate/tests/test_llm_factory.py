"""Provider selection lives in one place, so `api/state.py` and `cli.py`
don't each grow their own copy of the branch."""

from __future__ import annotations

import pytest

from substrate.llm.anthropic_adapter import AnthropicCompletions
from substrate.llm.factory import UnknownProvider, build_completions
from substrate.llm.openrouter_adapter import OpenRouterCompletions


def test_openrouter_provider_builds_an_openrouter_adapter() -> None:
    llm = build_completions(provider="openrouter", api_key="sk-or-test")
    assert isinstance(llm, OpenRouterCompletions)


def test_anthropic_provider_builds_an_anthropic_adapter() -> None:
    llm = build_completions(provider="anthropic", api_key="sk-ant-test")
    assert isinstance(llm, AnthropicCompletions)


def test_shared_ledger_is_passed_through() -> None:
    from substrate.llm.pricing import CostLedger

    ledger = CostLedger()
    llm = build_completions(provider="openrouter", api_key="k", ledger=ledger)
    assert isinstance(llm, OpenRouterCompletions)
    assert llm.ledger is ledger


def test_unknown_provider_raises_rather_than_silently_picking_one() -> None:
    with pytest.raises(UnknownProvider):
        build_completions(provider="chatgpt", api_key="k")
