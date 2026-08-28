"""Where the provider branch lives, so it exists exactly once.

Without this, `api/state.py` and `cli.py` would each grow their own copy of
"if provider == openrouter else anthropic" — and the two copies would drift
the first time a third provider shows up.
"""

from __future__ import annotations

from substrate.llm import Completions
from substrate.llm.anthropic_adapter import AnthropicCompletions
from substrate.llm.openrouter_adapter import OpenRouterCompletions
from substrate.llm.pricing import CostLedger


class UnknownProvider(ValueError):
    """An unrecognized provider name — raised rather than defaulted, same
    discipline as an unpriced model: a typo in config should fail loudly at
    startup, not silently fall back to whichever provider happens to be
    first in a branch."""


def build_completions(
    *,
    provider: str,
    api_key: str,
    ledger: CostLedger | None = None,
) -> Completions:
    if provider == "openrouter":
        return OpenRouterCompletions(api_key=api_key, ledger=ledger)
    if provider == "anthropic":
        return AnthropicCompletions(api_key=api_key, ledger=ledger)
    raise UnknownProvider(f"unknown llm provider {provider!r} (want 'openrouter' or 'anthropic')")
