"""Provider-agnostic text completion, with usage reported in full.

The port exists because there will be more than one implementation of it:
a real adapter and a recording fake today, and a second vendor in Ratchet.
Nothing above this module may import a vendor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

__all__ = [
    "CompletionError",
    "CompletionRequest",
    "CompletionResponse",
    "Completions",
    "FakeCompletions",
    "Message",
    "Role",
    "StopReason",
    "TokenUsage",
]


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class StopReason(StrEnum):
    """Why generation stopped.

    REFUSAL is the one that matters here. It is not an error: the request
    succeeded, a safety classifier declined, and `content` may be empty or
    partial. Code that reads the first content block without checking this
    first will crash on a perfectly ordinary response.
    """

    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Four buckets, because there are four prices.

    Cached input is not the same price as fresh input, and writing the cache
    is not the same price as reading it. A usage type with two fields quietly
    makes prompt caching invisible: you would pay for it and never see the
    saving in your own numbers.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_tokens
            + self.cache_read_tokens
        )


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """One call. Deliberately narrow — this is a port, not an SDK wrapper.

    Note what is absent: temperature, top_p, top_k. Current models reject all
    three with a 400, and `temperature=0` never actually bought determinism
    anyway. The levers that remain are the prompt and `json_schema`, which
    constrain meaning and shape rather than sampling.
    """

    model: str
    system: str
    messages: tuple[Message, ...]
    max_tokens: int = 2048
    json_schema: dict[str, Any] | None = None
    cache_system: bool = True
    """Cache the system prompt. It is stable across every request and, once
    the cite-or-refuse instructions are written, comfortably over the
    512-token minimum. Below that minimum nothing caches and no error is
    raised — you simply pay full price forever and wonder why."""

    run_id: str = "adhoc"
    """Which run to bill this to. On the request rather than as a keyword
    argument to `complete`, so the Protocol stays a single-parameter method
    and every implementation has the same signature. An adapter that quietly
    accepts an extra kwarg is not substitutable for one that doesn't."""


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    text: str
    usage: TokenUsage
    stop_reason: StopReason
    model: str
    refusal_category: str | None = None
    """Populated only when stop_reason is REFUSAL. Free-form by design: the
    provider's category set is open and will grow."""

    cost_usd: Decimal = Decimal(0)
    """Priced by the adapter that made the call, because only the adapter
    knows which model actually served it. Defaults to zero so the fake needs
    no pricing logic."""

    @property
    def refused(self) -> bool:
        return self.stop_reason is StopReason.REFUSAL


class CompletionError(RuntimeError):
    """A call that failed after exhausting retries.

    Distinct from a refusal. A refusal is a successful call with a decision
    attached; this is the call not happening.
    """


class Completions(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...


@dataclass
class FakeCompletions:
    """A recording double. Every test in this project uses it.

    Two jobs: return whatever the test scripted, and remember exactly what it
    was asked. The second job is the one that catches prompt regressions —
    a test can assert that the chunk ids really were in the prompt, which is
    the whole basis of the citation validator's guarantee.
    """

    responses: list[CompletionResponse | Exception] = field(default_factory=list)
    calls: list[CompletionRequest] = field(default_factory=list)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError(
                "FakeCompletions ran out of scripted responses; "
                f"call #{len(self.calls)} was unexpected"
            )
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @property
    def last(self) -> CompletionRequest:
        return self.calls[-1]
