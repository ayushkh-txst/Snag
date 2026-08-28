"""OpenRouterCompletions against a faked HTTP transport — no live network.

httpx.MockTransport rather than a new test dependency: httpx is already the
project's HTTP client (ecfr/client.py), and MockTransport is part of it.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

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
async def test_unknown_model_raises_rather_than_pricing_it_as_free() -> None:
    """Same discipline as the Anthropic adapter: an unpriced model is a gap
    to fix, not a call that quietly looks free."""
    from substrate.llm.pricing import UnknownRate

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_completion(model="some/unpriced-model"))

    with pytest.raises(UnknownRate):
        await _adapter(handler).complete(_request(model="some/unpriced-model"))
