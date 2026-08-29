"""The only module in this project that imports a vendor SDK."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

import anthropic
import structlog

from substrate.llm import (
    CompletionError,
    CompletionRequest,
    CompletionResponse,
    Role,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolsNotSupportedError,
)
from substrate.llm.pricing import CostLedger, price_or_fallback
from substrate.resilience import full_jitter_delay

log = structlog.get_logger(__name__)

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
)

_STOP_REASONS = {
    "end_turn": StopReason.END_TURN,
    "max_tokens": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
    "tool_use": StopReason.TOOL_USE,
}

# Same heuristic as the OpenRouter adapter (openrouter_adapter.py): the SDK
# raises a plain `anthropic.APIError` for every 4xx, with no dedicated
# exception for "this model doesn't support tools", so the capability signal
# has to come from the error text. Only consulted when `request.tools` was
# actually sent.
_TOOLS_UNSUPPORTED_HINTS = ("tool", "function calling")


def _looks_like_tools_unsupported(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(hint in lowered for hint in _TOOLS_UNSUPPORTED_HINTS)


class AnthropicCompletions:
    """Adapter. Owns retry, usage extraction, and pricing.

    `max_retries=0` on the client is deliberate. The SDK retries twice by
    default, and left alone that composes with our loop into 3 x 3 = 9
    attempts — a retry storm assembled by accident out of two individually
    sensible policies. Substrate owns resilience in this codebase, so the
    SDK's copy is switched off rather than layered.
    """

    def __init__(
        self,
        *,
        api_key: str,
        ledger: CostLedger | None = None,
        max_attempts: int = 3,
        clock: Any = None,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)
        self._ledger = ledger or CostLedger()
        self._max_attempts = max_attempts
        self._today = clock or (lambda: datetime.now(UTC).date())

    @property
    def ledger(self) -> CostLedger:
        return self._ledger

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        last: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw = await self._client.messages.create(**self._kwargs(request))
            except _RETRYABLE as exc:
                last = exc
                if attempt < self._max_attempts:
                    delay = full_jitter_delay(attempt)
                    log.warning(
                        "llm.retry",
                        attempt=attempt,
                        delay_s=round(delay, 3),
                        error=type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
                continue
            except anthropic.APIError as exc:
                # Non-retryable provider failure (auth, invalid request, ...).
                # Still a provider outage, not a refusal and not a code bug:
                # surface it as CompletionError so the API layer renders 502.
                # Exception: a `tools` request rejected for lack of
                # function-calling support gets its own typed error (mirrors
                # OpenRouterCompletions.complete), so the runner can skip
                # tool-surface tests for this one model (§1.3).
                if request.tools and _looks_like_tools_unsupported(str(exc)):
                    raise ToolsNotSupportedError(
                        f"model {request.model} does not support tool calling: {exc}"
                    ) from exc
                raise CompletionError(
                    f"{type(exc).__name__} for model {request.model}: {exc}"
                ) from exc
            return self._to_response(
                raw, run_id=request.run_id, when=self._today(), model=request.model
            )

        raise CompletionError(
            f"{self._max_attempts} attempts failed for model {request.model}"
        ) from last

    def _kwargs(self, request: CompletionRequest) -> dict[str, Any]:
        """Built conditionally: passing `output_config=None` is not the same
        as omitting it, and the API rejects the former."""
        system: Any = request.system
        if request.cache_system:
            system = [
                {
                    "type": "text",
                    "text": request.system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": system,
            "messages": [_to_anthropic_message(m) for m in request.messages],
        }
        if request.json_schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": request.json_schema}
            }
        if request.tools:
            kwargs["tools"] = [_to_anthropic_tool(t) for t in request.tools]
        return kwargs

    def _to_response(self, raw: Any, *, run_id: str, when: date, model: str) -> CompletionResponse:
        stop = _STOP_REASONS.get(raw.stop_reason or "", StopReason.OTHER)

        # Check the stop reason BEFORE touching content. On a refusal the
        # content list can be empty, and `raw.content[0]` would raise
        # IndexError on what is a perfectly successful HTTP 200.
        text = ""
        tool_calls: tuple[ToolCall, ...] = ()
        if stop is not StopReason.REFUSAL:
            text = "".join(b.text for b in raw.content if b.type == "text")
            tool_calls = tuple(
                ToolCall(id=b.id, name=b.name, arguments=_coerce_tool_arguments(b.input))
                for b in raw.content
                if b.type == "tool_use"
            )

        usage = TokenUsage(
            input_tokens=raw.usage.input_tokens or 0,
            output_tokens=raw.usage.output_tokens or 0,
            cache_write_tokens=getattr(raw.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(raw.usage, "cache_read_input_tokens", 0) or 0,
        )
        # Price by the model we ASKED for, not the string the provider echoes
        # back — asking for `claude-haiku-4-5` returns the dated alias
        # `claude-haiku-4-5-20251001`, and the rate table is keyed by the
        # aliases we send. Billing is determined by the request, not the echo.
        cost, unknown_pricing = price_or_fallback(usage, model=model, when=when)
        running = self._ledger.record(run_id, cost)

        category = None
        if stop is StopReason.REFUSAL:
            details = getattr(raw, "stop_details", None)
            category = getattr(details, "category", None)

        log.info(
            "llm.complete",
            run_id=run_id,
            model=raw.model,
            stop_reason=stop.value,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cache_read=usage.cache_read_tokens,
            cost_usd=str(cost),
            unknown_pricing=unknown_pricing,
            run_total_usd=str(running),
        )
        return CompletionResponse(
            text=text,
            usage=usage,
            stop_reason=stop,
            model=raw.model,
            refusal_category=category,
            cost_usd=cost,
            tool_calls=tool_calls,
        )


def _to_anthropic_message(m: Any) -> dict[str, Any]:
    """Anthropic has no TOOL role — a tool result is a `user` turn carrying a
    `tool_result` content block keyed by the id of the `tool_use` block it
    answers."""
    if m.role is Role.TOOL:
        return {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            ],
        }
    return {"role": m.role.value, "content": m.content}


def _to_anthropic_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """`CompletionRequest.tools` is OpenAI-shaped (`{"type": "function",
    "function": {"name", "description", "parameters"}}`) because that is the
    wire format OpenRouter already speaks; Anthropic wants the function body
    flattened to the top level with `parameters` renamed `input_schema`."""
    function = tool.get("function", tool)
    return {
        "name": function["name"],
        "description": function.get("description", ""),
        "input_schema": function.get("parameters", {"type": "object", "properties": {}}),
    }


def _coerce_tool_arguments(value: Any) -> dict[str, Any]:
    """Anthropic's SDK already parses `tool_use.input` into a dict — unlike
    OpenRouter's raw JSON string (see `openrouter_adapter._parse_tool_arguments`)
    — but a malformed/non-dict value is still wrapped rather than trusted,
    same discipline as the OpenRouter side (T-05-01)."""
    if isinstance(value, dict):
        return value
    return {"_raw": value}
