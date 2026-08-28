"""snag.api.ratelimit: a per-IP sliding-window guard on owner-funded scans.

Unit-level against `check_rate`/`guard_owner_scans` directly — no live
network, no database, no need to wait out a real window: staying within
`limit` calls made back-to-back is enough to exercise the count logic.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from snag.api import ratelimit as ratelimit_module
from snag.api.ratelimit import check_rate, guard_owner_scans
from snag.config import Settings
from substrate.llm.pricing import CostLedger


@pytest.fixture(autouse=True)
def _clear_windows() -> None:
    """`_WINDOWS` is module-level and process-lifetime by design (this is a
    single-process, in-memory limiter — see the module docstring); tests
    need a clean slate each time so one test's calls can't count against
    another's limit."""
    ratelimit_module._WINDOWS.clear()


def _make_request(
    *, headers: dict[str, str] | None = None, openrouter_api_key: str = "", ip: str = "203.0.113.9"
) -> Request:
    settings = Settings(openrouter_api_key=openrouter_api_key)
    state = SimpleNamespace(ctx=SimpleNamespace(settings=settings, ledger=CostLedger(), db=None))
    scope = {
        "type": "http",
        "headers": Headers(headers or {}).raw,
        "app": SimpleNamespace(state=state),
        "client": (ip, 12345),
        "method": "POST",
        "path": "/api/scans",
    }
    return Request(scope)


def test_check_rate_allows_up_to_the_limit_then_429s() -> None:
    for _ in range(3):
        check_rate("203.0.113.1", limit=3, window_s=60.0)
    with pytest.raises(HTTPException) as exc_info:
        check_rate("203.0.113.1", limit=3, window_s=60.0)
    assert exc_info.value.status_code == 429


def test_check_rate_keys_on_ip_independently() -> None:
    for _ in range(3):
        check_rate("203.0.113.2", limit=3, window_s=60.0)
    # A different IP has its own independent budget.
    check_rate("203.0.113.3", limit=3, window_s=60.0)


def test_guard_owner_scans_limits_owner_funded_requests_over_the_limit() -> None:
    for _ in range(ratelimit_module.DEFAULT_LIMIT):
        guard_owner_scans(_make_request(openrouter_api_key="sk-owner-test", ip="203.0.113.4"))
    with pytest.raises(HTTPException) as exc_info:
        guard_owner_scans(_make_request(openrouter_api_key="sk-owner-test", ip="203.0.113.4"))
    assert exc_info.value.status_code == 429


def test_guard_owner_scans_never_limits_byok_requests() -> None:
    """A BYOK request funds itself — it must never be rate limited here,
    even well past the owner-funded limit."""
    for _ in range(ratelimit_module.DEFAULT_LIMIT + 5):
        guard_owner_scans(
            _make_request(headers={"X-OpenRouter-Key": "sk-byok-test"}, ip="203.0.113.5")
        )
