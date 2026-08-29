"""A second vendor behind the same port. Talks HTTP directly, not the
`openai` SDK — the SDK's own retries would have to be disabled anyway (see
AnthropicCompletions' `max_retries=0`), so there's nothing it buys here that
plain httpx doesn't already have.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog

from substrate.llm import (
    CompletionError,
    CompletionRequest,
    CompletionResponse,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolsNotSupportedError,
)
from substrate.llm.pricing import CostLedger, price_or_fallback
from substrate.resilience import full_jitter_delay

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_STOP_REASONS = {
    "stop": StopReason.END_TURN,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
    "tool_calls": StopReason.TOOL_USE,
}

# Substring seen in OpenRouter's own error body when the upstream model/
# endpoint doesn't support function calling at all (e.g. "No endpoints found
# that support tool use", "does not support tools"). A heuristic on purpose —
# OpenRouter proxies many upstream providers and does not have one stable
# error code for this — but it only fires when `request.tools` was actually
# sent, so it can't misfire on an unrelated 4xx.
_TOOLS_UNSUPPORTED_HINTS = ("tool", "function calling")


def _looks_like_tools_unsupported(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(hint in lowered for hint in _TOOLS_UNSUPPORTED_HINTS)


class OpenRouterCompletions:
    """Adapter for OpenRouter's OpenAI-compatible chat completions endpoint.

    Owns retry, usage extraction, and pricing — same responsibilities as
    AnthropicCompletions, so the two are interchangeable behind `Completions`.

    One real behavioural difference: OpenRouter's free-tier models accept
    `response_format: json_object` but don't enforce a schema server-side the
    way Anthropic's `output_config` does. So `json_schema`, when present, is
    also folded into the system prompt as an explicit instruction — the
    server-side flag alone isn't enough to get schema-shaped JSON back.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        ledger: CostLedger | None = None,
        max_attempts: int = 5,
        clock: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=60.0,
        )
        self._ledger = ledger or CostLedger()
        self._max_attempts = max_attempts
        self._today = clock or (lambda: datetime.now(UTC).date())
        self._retry_listeners: list[Callable[[], None]] = []

    @property
    def ledger(self) -> CostLedger:
        return self._ledger

    def add_retry_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """`substrate.llm.RetryListening`: register `listener` to be called the
        moment a 429 is seen, before this adapter backs off and retries. Fires
        for every registered listener (a broadcast — concurrent scans share
        one provider rate limit, so all of them should ease off together).
        Returns a cancel callback; call it when the caller no longer wants the
        signal."""
        self._retry_listeners.append(listener)

        def _cancel() -> None:
            with contextlib.suppress(ValueError):
                self._retry_listeners.remove(listener)

        return _cancel

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        last: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw = await self._client.post("/chat/completions", json=self._body(request))
            except httpx.TransportError as exc:
                last = exc
                if attempt < self._max_attempts:
                    await self._backoff(attempt, exc)
                continue

            if raw.status_code in _RETRYABLE_STATUS:
                if raw.status_code == 429:
                    self._notify_rate_limited()
                last = httpx.HTTPStatusError(
                    f"{raw.status_code} from OpenRouter", request=raw.request, response=raw
                )
                if attempt < self._max_attempts:
                    await self._backoff(attempt, last)
                continue

            if raw.status_code >= 400:
                # Non-retryable provider failure (auth, invalid request, ...).
                # Not a refusal and not a code bug: an outage/misconfiguration,
                # surfaced as CompletionError so the API layer renders 502.
                # Exception: a `tools` request rejected because the model has
                # no function-calling support gets its own typed error, so
                # the runner can skip tool-surface tests for this one model
                # instead of aborting the whole scan (§1.3).
                if request.tools and _looks_like_tools_unsupported(raw.text):
                    raise ToolsNotSupportedError(
                        f"model {request.model} does not support tool calling: "
                        f"{raw.text[:200]}"
                    )
                raise CompletionError(
                    f"HTTP {raw.status_code} for model {request.model}: {raw.text[:200]}"
                )

            return self._to_response(
                raw.json(),
                run_id=request.run_id,
                when=self._today(),
                requested_model=request.model,
            )

        raise CompletionError(
            f"{self._max_attempts} attempts failed for model {request.model}"
        ) from last

    def _notify_rate_limited(self) -> None:
        # A snapshot: a listener's cancel could mutate the list mid-broadcast.
        for listener in list(self._retry_listeners):
            listener()

    async def _backoff(self, attempt: int, exc: Exception) -> None:
        delay = full_jitter_delay(attempt)
        log.warning(
            "llm.retry",
            attempt=attempt,
            delay_s=round(delay, 3),
            error=type(exc).__name__,
        )
        await asyncio.sleep(delay)

    def _body(self, request: CompletionRequest) -> dict[str, Any]:
        system = request.system
        response_format: dict[str, Any] | None = None
        if request.json_schema is not None:
            response_format = {"type": "json_object"}
            system = (
                f"{system}\n\nRespond with a single JSON object matching this schema. "
                f"Return only the JSON, no other text:\n{json.dumps(request.json_schema)}"
            )

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in request.messages:
            entry: dict[str, Any] = {"role": m.role.value, "content": m.content}
            # A TOOL-role message answers a prior tool call; OpenAI's wire
            # format keys that answer to the call by id, not by position.
            if m.tool_call_id is not None:
                entry["tool_call_id"] = m.tool_call_id
            if m.name is not None:
                entry["name"] = m.name
            messages.append(entry)

        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            # Ask OpenRouter to report what it actually billed on this call,
            # so `_to_response` can use a real figure instead of estimating
            # from the static RATES table for arbitrary BYOK models.
            "usage": {"include": True},
        }
        if response_format is not None:
            body["response_format"] = response_format
        if request.tools:
            body["tools"] = list(request.tools)

        # A reasoning model asked for structured output will happily spend
        # the whole completion budget thinking and return an empty string.
        # Measured against qwen/qwen3.8-flash on Snag's rule extraction:
        # reasoning_tokens == max_tokens and content == "" at 2048, 4096 AND
        # 8192 — more budget just buys more thinking. With reasoning off it
        # answered inside 2048. So an unset `reasoning` means "off when a
        # schema is set": the tokens belong to the answer, not a preamble
        # that gets discarded. An explicit True/False always wins, which is
        # what keeps a free-form attack dispatch thinking normally — there
        # the model's own unprompted behaviour is the thing under test.
        reasoning = request.reasoning
        if reasoning is None:
            reasoning = request.json_schema is None
        if not reasoning:
            body["reasoning"] = {"enabled": False}
        return body

    def _to_response(
        self, raw: dict[str, Any], *, run_id: str, when: date, requested_model: str
    ) -> CompletionResponse:
        choice = raw["choices"][0]
        stop = _STOP_REASONS.get(choice.get("finish_reason") or "", StopReason.OTHER)

        # Check the stop reason before touching content, same discipline as
        # the Anthropic adapter: a refusal can arrive with empty content.
        text = ""
        if stop is not StopReason.REFUSAL:
            text = choice.get("message", {}).get("content") or ""

        tool_calls = _parse_tool_calls(choice.get("message", {}).get("tool_calls"))

        raw_usage = raw.get("usage") or {}
        usage = TokenUsage(
            input_tokens=raw_usage.get("prompt_tokens") or 0,
            output_tokens=raw_usage.get("completion_tokens") or 0,
        )

        model = str(raw.get("model") or requested_model)
        # Cost resolution order: (1) OpenRouter's own reported cost for this
        # exact call — the only figure that's actually correct for an
        # arbitrary BYOK model; (2) the static RATES table when the model is
        # one of ours and the provider didn't report a cost; (3)
        # `price_or_fallback`'s documented conservative estimate, which never
        # raises `UnknownRate` and never looks silently free (found live
        # 2026-08-28 — see `pricing.price_or_fallback`).
        response_cost = raw_usage.get("cost")
        if response_cost is not None:
            cost = Decimal(str(response_cost))
            unknown_pricing = False
        else:
            cost, unknown_pricing = price_or_fallback(usage, model=requested_model, when=when)
        running = self._ledger.record(run_id, cost)

        log.info(
            "llm.complete",
            run_id=run_id,
            model=model,
            stop_reason=stop.value,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cost_usd=str(cost),
            unknown_pricing=unknown_pricing,
            run_total_usd=str(running),
        )
        return CompletionResponse(
            text=text,
            usage=usage,
            stop_reason=stop,
            model=model,
            cost_usd=cost,
            tool_calls=tool_calls,
        )


def _parse_tool_calls(raw_tool_calls: Any) -> tuple[ToolCall, ...]:
    """OpenAI wire shape: a list of `{"id", "type": "function", "function":
    {"name", "arguments": "<json string>"}}`. Absent/empty on every ordinary
    (non-tool) response, so the common case is a zero-cost no-op."""
    if not raw_tool_calls:
        return ()
    parsed: list[ToolCall] = []
    for item in raw_tool_calls:
        function = item.get("function") or {}
        parsed.append(
            ToolCall(
                id=item.get("id", ""),
                name=function.get("name", ""),
                arguments=_parse_tool_arguments(function.get("arguments")),
            )
        )
    return tuple(parsed)


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Defensive json-load (T-05-01): a model-influenced, malformed
    `arguments` string must not crash the adapter and is never `eval`'d. A
    string that isn't valid JSON, or JSON that isn't an object, is wrapped
    rather than dropped, so the caller still sees exactly what the model
    sent."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        loaded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("llm.tool_call_arguments_malformed", raw=str(raw)[:200])
        return {"_raw": raw}
    if isinstance(loaded, dict):
        return loaded
    return {"_raw": loaded}
