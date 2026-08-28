"""Real function-calling on the vendored `Completions` port (§6.4, tool
abuse — the highest-value attack surface). OpenAI-style shape (Option A,
confirmed 01-05 checkpoint decision): `Role.TOOL`, `ToolCall`,
`StopReason.TOOL_USE`, `CompletionRequest.tools`, `CompletionResponse.tool_calls`.

Covers the port dataclasses, the OpenRouter adapter's request/response
mapping (httpx.MockTransport, no network), the tool-capability signal, and
the Anthropic adapter's translation to/from its own tool_use/tool_result
wire shape (pure-function unit tests — the SDK client is not mocked here,
matching the rest of this suite's httpx-only no-network discipline; there is
no existing pattern in this repo for mocking `anthropic.AsyncAnthropic`
itself, so the adapter's request/response *shaping* functions are exercised
directly instead of through `.complete()`)."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from substrate.llm import (
    CompletionError,
    CompletionRequest,
    CompletionResponse,
    Message,
    Role,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolsNotSupportedError,
)
from substrate.llm.anthropic_adapter import (
    AnthropicCompletions,
    _coerce_tool_arguments,
    _to_anthropic_message,
    _to_anthropic_tool,
)
from substrate.llm.openrouter_adapter import OpenRouterCompletions, _parse_tool_arguments

MODEL = "google/gemma-4-26b-a4b-it:free"

WEATHER_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Look up the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def _request(**overrides: object) -> CompletionRequest:
    defaults: dict[str, object] = {
        "model": MODEL,
        "system": "Answer briefly.",
        "messages": (Message(Role.USER, "What is the weather in Reno?"),),
        "run_id": "test-run",
    }
    defaults.update(overrides)
    return CompletionRequest(**defaults)  # type: ignore[arg-type]


def _adapter(handler: object, *, max_attempts: int = 3) -> OpenRouterCompletions:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OpenRouterCompletions(
        api_key="sk-or-test",
        transport=transport,
        max_attempts=max_attempts,
        clock=lambda: date(2026, 8, 23),
    )


def _plain_chat_completion() -> dict[str, object]:
    """An ordinary, no-tools chat completion body — used by tests that only
    care about what got sent, not what comes back."""
    message = {"role": "assistant", "content": "ok"}
    return {
        "id": "gen-1",
        "model": MODEL,
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


# ---------------------------------------------------------------------------
# Port-level dataclasses
# ---------------------------------------------------------------------------


def test_role_tool_exists() -> None:
    assert Role.TOOL.value == "tool"


def test_stop_reason_tool_use_exists() -> None:
    assert StopReason.TOOL_USE.value == "tool_use"


def test_tool_call_carries_id_name_and_dict_arguments() -> None:
    call = ToolCall(id="call_1", name="get_weather", arguments={"city": "Reno"})
    assert call.id == "call_1"
    assert call.name == "get_weather"
    assert call.arguments == {"city": "Reno"}


def test_completion_request_tools_defaults_to_none() -> None:
    assert _request().tools is None


def test_completion_request_carries_tools_when_given() -> None:
    request = _request(tools=(WEATHER_TOOL,))
    assert request.tools == (WEATHER_TOOL,)


def test_completion_response_tool_calls_defaults_to_empty_tuple() -> None:
    response = CompletionResponse(
        text="hi",
        usage=TokenUsage(),
        stop_reason=StopReason.END_TURN,
        model=MODEL,
    )
    assert response.tool_calls == ()


def test_message_name_and_tool_call_id_default_to_none_for_existing_callers() -> None:
    """Every USER/ASSISTANT caller that predates this plan constructs
    `Message(role, content)` positionally; the new fields must not break it."""
    message = Message(Role.USER, "hello")
    assert message.name is None
    assert message.tool_call_id is None


# ---------------------------------------------------------------------------
# OpenRouter: request body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_are_carried_onto_the_openrouter_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_plain_chat_completion())

    await _adapter(handler).complete(_request(tools=(WEATHER_TOOL,)))

    assert captured["tools"] == [WEATHER_TOOL]


@pytest.mark.asyncio
async def test_a_request_without_tools_has_no_tools_key() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_plain_chat_completion())

    await _adapter(handler).complete(_request())

    assert "tools" not in captured
    # No regression: the existing shape (model/max_tokens/messages) is
    # untouched by this plan's changes.
    assert captured["model"] == MODEL
    assert isinstance(captured["messages"], list)


@pytest.mark.asyncio
async def test_tool_role_message_maps_to_openai_tool_message_shape() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_plain_chat_completion())

    messages = (
        Message(Role.ASSISTANT, ""),
        Message(Role.TOOL, "72F and sunny", tool_call_id="call_1", name="get_weather"),
    )
    await _adapter(handler).complete(_request(messages=messages, tools=(WEATHER_TOOL,)))

    tool_message = captured["messages"][-1]  # type: ignore[index]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert tool_message["content"] == "72F and sunny"


# ---------------------------------------------------------------------------
# OpenRouter: response parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_calls_response_parses_into_tool_call_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "model": MODEL,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city": "Reno"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    response = await _adapter(handler).complete(_request(tools=(WEATHER_TOOL,)))

    assert response.stop_reason is StopReason.TOOL_USE
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "get_weather"
    assert call.arguments == {"city": "Reno"}


@pytest.mark.asyncio
async def test_a_response_with_no_tool_calls_has_an_empty_tuple() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "model": MODEL,
                "choices": [
                    {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    response = await _adapter(handler).complete(_request())
    assert response.tool_calls == ()


def test_malformed_arguments_are_wrapped_not_raised_or_evaluated() -> None:
    """T-05-01: attacker-influenced `arguments` text must never crash the
    adapter and is never `eval`'d — a non-JSON string is defensively wrapped
    instead of dropped."""
    parsed = _parse_tool_arguments("not json; __import__('os').system('rm -rf /')")
    assert parsed == {"_raw": "not json; __import__('os').system('rm -rf /')"}


def test_arguments_that_parse_to_a_non_object_are_wrapped() -> None:
    parsed = _parse_tool_arguments("[1, 2, 3]")
    assert parsed == {"_raw": [1, 2, 3]}


def test_missing_arguments_default_to_an_empty_dict() -> None:
    assert _parse_tool_arguments(None) == {}


# ---------------------------------------------------------------------------
# Capability signal: a model that rejects tool calling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_unsupported_raises_a_distinct_typed_error() -> None:
    """The runner needs to tell 'this model can't do tools' apart from 'this
    call failed' so it can skip tool-surface tests for the one model and say
    so in the report instead of aborting the whole scan (§1.3)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "No endpoints found that support tool use for this model."}},
        )

    with pytest.raises(ToolsNotSupportedError):
        await _adapter(handler).complete(_request(tools=(WEATHER_TOOL,)))


@pytest.mark.asyncio
async def test_tools_not_supported_error_is_a_completion_error_subtype() -> None:
    """Existing `except CompletionError` call sites (the API layer's 502
    mapping) must keep working unchanged."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "model does not support tools"}})

    with pytest.raises(CompletionError):
        await _adapter(handler).complete(_request(tools=(WEATHER_TOOL,)))


@pytest.mark.asyncio
async def test_an_unrelated_400_is_still_a_generic_completion_error() -> None:
    """The heuristic only fires when tools were actually requested and the
    error text mentions them — it must not swallow an ordinary bad request
    into the wrong exception type."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "invalid max_tokens"}})

    with pytest.raises(CompletionError) as excinfo:
        await _adapter(handler).complete(_request(tools=(WEATHER_TOOL,)))
    assert not isinstance(excinfo.value, ToolsNotSupportedError)


@pytest.mark.asyncio
async def test_400_without_tools_requested_never_raises_tools_not_supported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "no tool support here"}})

    with pytest.raises(CompletionError) as excinfo:
        await _adapter(handler).complete(_request())
    assert not isinstance(excinfo.value, ToolsNotSupportedError)


# ---------------------------------------------------------------------------
# Anthropic adapter: translation to/from its own tool_use/tool_result shape
# ---------------------------------------------------------------------------


def test_anthropic_tool_role_message_becomes_a_user_turn_with_a_tool_result_block() -> None:
    message = Message(Role.TOOL, "72F and sunny", tool_call_id="call_1")
    mapped = _to_anthropic_message(message)
    assert mapped == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "72F and sunny"}],
    }


def test_anthropic_user_and_assistant_messages_are_unchanged() -> None:
    assert _to_anthropic_message(Message(Role.USER, "hi")) == {"role": "user", "content": "hi"}


def test_openai_shaped_tool_def_converts_to_anthropic_input_schema() -> None:
    converted = _to_anthropic_tool(WEATHER_TOOL)
    assert converted == {
        "name": "get_weather",
        "description": "Look up the current weather for a city.",
        "input_schema": WEATHER_TOOL["function"]["parameters"],
    }


def test_anthropic_kwargs_include_tools_when_present() -> None:
    adapter = AnthropicCompletions(api_key="sk-ant-test")
    kwargs = adapter._kwargs(_request(tools=(WEATHER_TOOL,)))
    assert kwargs["tools"] == [_to_anthropic_tool(WEATHER_TOOL)]


def test_anthropic_kwargs_omit_tools_when_absent() -> None:
    adapter = AnthropicCompletions(api_key="sk-ant-test")
    kwargs = adapter._kwargs(_request())
    assert "tools" not in kwargs


def test_anthropic_tool_use_content_parses_into_tool_calls() -> None:
    adapter = AnthropicCompletions(api_key="sk-ant-test")
    tool_use_block = SimpleNamespace(
        type="tool_use", id="toolu_1", name="get_weather", input={"city": "Reno"}
    )
    raw = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="text", text=""), tool_use_block],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-opus-5",
        stop_details=None,
    )
    response = adapter._to_response(
        raw, run_id="test-run", when=date(2026, 8, 23), model="claude-opus-5"
    )
    expected = ToolCall(id="toolu_1", name="get_weather", arguments={"city": "Reno"})
    assert response.stop_reason is StopReason.TOOL_USE
    assert response.tool_calls == (expected,)


def test_anthropic_tool_use_with_non_dict_input_is_wrapped() -> None:
    assert _coerce_tool_arguments("not-a-dict") == {"_raw": "not-a-dict"}
    assert _coerce_tool_arguments({"city": "Reno"}) == {"city": "Reno"}
