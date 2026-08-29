"""OpenRouterCompletions against a faked HTTP transport — no live network.

httpx.MockTransport rather than a new test dependency: httpx is already the
project's HTTP client (ecfr/client.py), and MockTransport is part of it.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from substrate.llm import CompletionError, CompletionRequest, Message, Role, StopReason
from substrate.llm.openrouter_adapter import OpenRouterCompletions

MODEL = "google/gemma-4-26b-a4b-it:free"


def _request(**overrides: object) -> CompletionRequest:
    defaults: dict[str, object] = {
        "model": MODEL,
        "system": "Answer briefly.",
        "messages": (Message(Role.USER, "What is the grace period?"),),
        "run_id": "test-run",
    }
    defaults.update(overrides)
    return CompletionRequest(**defaults)  # type: ignore[arg-type]


def _chat_completion(
    *,
    content: str = "hello",
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 4,
    model: str = MODEL,
) -> dict[str, object]:
    return {
        "id": "gen-1",
        "model": model,
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _adapter(handler: object, *, max_attempts: int = 3) -> OpenRouterCompletions:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OpenRouterCompletions(
        api_key="sk-or-test",
        transport=transport,
        max_attempts=max_attempts,
        clock=lambda: date(2026, 8, 23),
    )


@pytest.mark.asyncio
async def test_successful_completion_returns_text_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-or-test"
        body = json.loads(request.content)
        assert body["model"] == MODEL
        return httpx.Response(200, json=_chat_completion(content="Sixty days."))

    adapter = _adapter(handler)
    response = await adapter.complete(_request())

    assert response.text == "Sixty days."
    assert response.stop_reason is StopReason.END_TURN
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 4


@pytest.mark.asyncio
async def test_cost_is_priced_and_recorded_to_the_ledger() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion())

    adapter = _adapter(handler)
    response = await adapter.complete(_request(run_id="run-a"))

    assert response.cost_usd == Decimal("0.000000")  # :free model
    assert adapter.ledger.total("run-a") == Decimal("0.000000")


@pytest.mark.asyncio
async def test_length_finish_reason_maps_to_max_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion(finish_reason="length"))

    response = await _adapter(handler).complete(_request())
    assert response.stop_reason is StopReason.MAX_TOKENS


@pytest.mark.asyncio
async def test_content_filter_is_a_refusal_not_a_crash() -> None:
    """A refusal can arrive with no content at all. Reading content before
    checking the stop reason must not raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = _chat_completion(content="", finish_reason="content_filter")
        return httpx.Response(200, json=body)

    response = await _adapter(handler).complete(_request())
    assert response.refused
    assert response.stop_reason is StopReason.REFUSAL


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json=_chat_completion(content="ok"))

    response = await _adapter(handler).complete(_request())
    assert response.text == "ok"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_429_notifies_retry_listeners_before_backing_off() -> None:
    """`RetryListening`: a registered listener fires the moment a 429 is seen
    (once per 429), so a caller's concurrency governor can ease off before the
    adapter's own retries turn the throttle into a storm. A non-429 retryable
    status (503 here) does not fire it — the signal is specifically rate limiting."""
    attempts = {"n": 0}
    fired = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, json={"error": {"message": "down"}})
        if attempts["n"] == 2:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json=_chat_completion(content="ok"))

    adapter = _adapter(handler, max_attempts=4)
    adapter.add_retry_listener(lambda: fired.__setitem__("n", fired["n"] + 1))
    response = await adapter.complete(_request())
    assert response.text == "ok"
    assert fired["n"] == 1  # the one 429, not the 503


@pytest.mark.asyncio
async def test_add_retry_listener_cancel_stops_further_notifications() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    fired = {"n": 0}
    adapter = _adapter(handler, max_attempts=2)
    cancel = adapter.add_retry_listener(lambda: fired.__setitem__("n", fired["n"] + 1))
    cancel()
    with pytest.raises(CompletionError):
        await adapter.complete(_request())
    assert fired["n"] == 0


@pytest.mark.asyncio
async def test_exhausted_retries_raise_completion_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "down"}})

    with pytest.raises(CompletionError):
        await _adapter(handler, max_attempts=2).complete(_request())


@pytest.mark.asyncio
async def test_auth_failure_is_not_retried() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(CompletionError):
        await _adapter(handler, max_attempts=3).complete(_request())
    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_json_schema_is_injected_into_the_system_prompt() -> None:
    """The free model doesn't enforce a schema server-side, so the adapter's
    only lever is telling it what shape to return."""
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_completion(content='{"answer": "ok"}'))

    await _adapter(handler).complete(_request(json_schema=schema))

    assert captured["response_format"] == {"type": "json_object"}
    messages = captured["messages"]
    assert isinstance(messages, list)
    system_message = next(m for m in messages if m["role"] == "system")
    assert "answer" in system_message["content"]


@pytest.mark.asyncio
async def test_unknown_model_gets_a_conservative_estimate_not_a_lost_response() -> None:
    """A BYOK model with no RATES entry must not crash a real, successful
    response over its own cost accounting (found live 2026-08-28 against two
    of Snag's ACCEPTED_MODELS). It also must not look free — `price_or_fallback`
    charges a documented conservative flat rate and flags `unknown_pricing`,
    preserving the module's real discipline (never silently free) one layer
    up from the exact, still-raising `price()`."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion(model="some/unpriced-model"))

    response = await _adapter(handler).complete(_request(model="some/unpriced-model"))
    assert response.cost_usd > 0


async def test_structured_output_disables_reasoning_by_default() -> None:
    """A reasoning model asked for JSON spends its whole budget thinking and
    returns nothing — measured on qwen/qwen3.8-flash at 2048/4096/8192, where
    reasoning_tokens == max_tokens and content was empty every time."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_completion())

    await _adapter(handler).complete(_request(json_schema={"type": "object"}))
    assert captured["reasoning"] == {"enabled": False}


async def test_a_free_form_call_disables_reasoning_too() -> None:
    """Thinking is off by default for EVERY call, not just the structured
    ones. Measured against deepseek/deepseek-v4-flash-0731 with the body a
    Snag attack dispatch actually sends: 320 completion tokens, 307 of them
    reasoning, leaving thirteen for the reply — which is why replies were
    coming back truncated and unscorable.

    The deeper reason is that the assistants these attacks simulate do not
    ship with thinking on; it is slow and expensive per turn. Testing with it
    on measures a configuration nobody deploys, and biases the result toward
    finding FEWER breaks, because a model reasoning its way through "this
    looks like an injection" resists better than the deployed one."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_completion())

    await _adapter(handler).complete(_request())
    assert captured["reasoning"] == {"enabled": False}


async def test_reasoning_can_still_be_asked_for_explicitly() -> None:
    """The off-by-default is a default, not a ban: a caller testing a
    deployment that really does run with thinking on says so per request."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_completion())

    await _adapter(handler).complete(_request(reasoning=True))
    assert "reasoning" not in captured


async def test_explicit_reasoning_flag_overrides_the_schema_default() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_completion())

    await _adapter(handler).complete(
        _request(json_schema={"type": "object"}, reasoning=True)
    )
    assert "reasoning" not in captured
