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
    StopReason,
    TokenUsage,
)
from substrate.llm.pricing import CostLedger, price
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
}


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
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
        }
        if request.json_schema is not None:
            kwargs["output_config"] = {
                "format": {"type": "json_schema", "schema": request.json_schema}
            }
        return kwargs

    def _to_response(self, raw: Any, *, run_id: str, when: date, model: str) -> CompletionResponse:
        stop = _STOP_REASONS.get(raw.stop_reason or "", StopReason.OTHER)

        # Check the stop reason BEFORE touching content. On a refusal the
        # content list can be empty, and `raw.content[0]` would raise
        # IndexError on what is a perfectly successful HTTP 200.
        text = ""
        if stop is not StopReason.REFUSAL:
            text = "".join(b.text for b in raw.content if b.type == "text")

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
        cost = price(usage, model=model, when=when)
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
            run_total_usd=str(running),
        )
        return CompletionResponse(
            text=text,
            usage=usage,
            stop_reason=stop,
            model=raw.model,
            refusal_category=category,
            cost_usd=cost,
        )
