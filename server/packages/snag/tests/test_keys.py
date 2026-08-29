"""Unit tests for snag.api.deps's key-resolution seam: BYOK header -> owner
env key -> none.

These are pure functions of a Request + Settings, so they're tested directly
against a hand-built Request rather than through a full app + Postgres round
trip — no network, no database, matching 01-02's own environment note that
this plan is fully testable with httpx.MockTransport/monkeypatch.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from snag.api.deps import KeyResolution, get_completions, require_funding, resolve_key
from snag.config import Settings
from substrate.llm.pricing import CostLedger


def _make_request(
    *, headers: dict[str, str] | None = None, openrouter_api_key: str = ""
) -> Request:
    """A Request whose `app.state.ctx` carries just enough (`settings`,
    `ledger`) for `resolve_key`/`get_completions` — neither touches a
    Database, so `db` is left `None`."""
    settings = Settings(openrouter_api_key=openrouter_api_key)
    state = SimpleNamespace(ctx=SimpleNamespace(settings=settings, ledger=CostLedger(), db=None))
    scope = {
        "type": "http",
        "headers": Headers(headers or {}).raw,
        "app": SimpleNamespace(state=state),
        "client": ("203.0.113.5", 12345),
        "method": "POST",
        "path": "/api/scans",
    }
    return Request(scope)


def test_byok_header_takes_precedence_over_owner_env() -> None:
    request = _make_request(
        headers={"X-OpenRouter-Key": "sk-byok-test"}, openrouter_api_key="sk-owner-test"
    )
    resolution = resolve_key(request)
    assert resolution == KeyResolution(key="sk-byok-test", source="byok", owner_funded=False)


def test_owner_env_key_funds_when_no_byok_header_and_flags_owner_funded() -> None:
    request = _make_request(openrouter_api_key="sk-owner-test")
    resolution = resolve_key(request)
    assert resolution == KeyResolution(key="sk-owner-test", source="owner", owner_funded=True)


def test_no_key_at_all_resolves_to_none_and_is_not_owner_funded() -> None:
    request = _make_request()
    resolution = resolve_key(request)
    assert resolution == KeyResolution(key=None, source="none", owner_funded=False)


def test_get_completions_builds_a_fresh_adapter_with_the_byok_key() -> None:
    request = _make_request(headers={"X-OpenRouter-Key": "sk-byok-test"})
    completions = get_completions(request)
    # White-box: the resolved key's only destination is the adapter's own
    # httpx client auth header — never logged, never returned (T-02-01).
    assert completions._client.headers["authorization"] == "Bearer sk-byok-test"  # type: ignore[attr-defined]


def test_get_completions_builds_a_fresh_adapter_with_the_owner_key_when_no_byok() -> None:
    request = _make_request(openrouter_api_key="sk-owner-test")
    completions = get_completions(request)
    assert completions._client.headers["authorization"] == "Bearer sk-owner-test"  # type: ignore[attr-defined]


def test_require_funding_raises_402_when_no_key_resolves() -> None:
    request = _make_request()
    with pytest.raises(HTTPException) as exc_info:
        require_funding(request)
    assert exc_info.value.status_code == 402


def test_require_funding_passes_for_byok() -> None:
    require_funding(_make_request(headers={"X-OpenRouter-Key": "sk-byok-test"}))


def test_require_funding_passes_for_owner_key() -> None:
    require_funding(_make_request(openrouter_api_key="sk-owner-test"))
