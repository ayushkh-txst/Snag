"""A second vendor behind the same port. Talks HTTP directly, not the
`openai` SDK — the SDK's own retries would have to be disabled anyway (see
AnthropicCompletions' `max_retries=0`), so there's nothing it buys here that
plain httpx doesn't already have.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import Any

import httpx
import structlog

from substrate.llm import (
    CompletionError,
    CompletionRequest,
    CompletionResponse,
    StopReason,
    TokenUsage,
)
from substrate.llm.pricing import CostLedger, price
from substrate.resilience import full_jitter_delay

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_STOP_REASONS = {
    "stop": StopReason.END_TURN,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}


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
        max_attempts: int = 3,
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

    @property
    def ledger(self) -> CostLedger:
        return self._ledger

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

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        messages.extend({"role": m.role.value, "content": m.content} for m in request.messages)

        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
        }
        if response_format is not None:
            body["response_format"] = response_format
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

        raw_usage = raw.get("usage") or {}
        usage = TokenUsage(
            input_tokens=raw_usage.get("prompt_tokens") or 0,
            output_tokens=raw_usage.get("completion_tokens") or 0,
        )

        model = str(raw.get("model") or requested_model)
        # Price by the model we asked for, not the one echoed back, matching
        # the Anthropic adapter's convention (billing follows the request).
        cost = price(usage, model=requested_model, when=when)
        running = self._ledger.record(run_id, cost)

        log.info(
            "llm.complete",
            run_id=run_id,
            model=model,
            stop_reason=stop.value,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cost_usd=str(cost),
            run_total_usd=str(running),
        )
        return CompletionResponse(
            text=text,
            usage=usage,
            stop_reason=stop,
            model=model,
            cost_usd=cost,
        )
