"""Provider-agnostic text completion, with usage reported in full.

The port exists because there will be more than one implementation of it:
a real adapter and a recording fake today, and a second vendor in Ratchet.
Nothing above this module may import a vendor SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "CompletionError",
    "CompletionRequest",
    "CompletionResponse",
    "Completions",
    "FakeCompletions",
    "Message",
    "RetryListening",
    "Role",
    "StopReason",
    "TokenUsage",
    "ToolCall",
    "ToolsNotSupportedError",
]


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    """A tool-result turn: the answer to a ToolCall the model made on a prior
    assistant turn. Carries `tool_call_id` (see `Message`) so the provider can
    match the result back to the call that requested it."""


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
    TOOL_USE = "tool_use"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str
    name: str | None = None
    """The tool name, when this message either requests or answers a tool
    call. Optional and defaulted so every existing USER/ASSISTANT caller is
    unaffected."""

    tool_call_id: str | None = None
    """Set on a TOOL-role message: the id of the `ToolCall` this message
    answers, matching the id the provider assigned on the assistant turn that
    requested it. `None` on USER/ASSISTANT messages."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool invocation the model asked for.

    `arguments` is always a `dict` — a provider that returns a malformed
    arguments payload gets defensively wrapped rather than raised, crashed
    on, or `eval`'d (see each adapter's own parsing)."""

    id: str
    name: str
    arguments: dict[str, Any]


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

    reasoning: bool | None = None
    """Whether the provider may spend tokens on a thinking pass first.

    Off unless asked for. Two independent reasons, both measured.

    A reasoning model asked for STRUCTURED output spends the whole budget
    thinking and returns nothing: `qwen/qwen3.8-flash` on rule extraction
    came back empty with `reasoning_tokens == max_tokens` at 2048, 4096 and
    8192 alike — more budget only buys more thinking.

    And for a FREE-FORM call against a target under test, thinking-on is the
    wrong thing to measure. Deployed assistants of the kind these calls
    simulate run with it off, for latency and cost; a model that reasons its
    way through "this looks like an injection" resists better than the one
    actually shipped, so leaving it on quietly understates how often a rule
    breaks. On `deepseek/deepseek-v4-flash-0731`, 307 of 320 completion
    tokens went to reasoning, leaving thirteen for the reply — enough to
    truncate answers into being unscorable.

    Set `True` to test a deployment that really does run with thinking on."""

    run_id: str = "adhoc"
    """Which run to bill this to. On the request rather than as a keyword
    argument to `complete`, so the Protocol stays a single-parameter method
    and every implementation has the same signature. An adapter that quietly
    accepts an extra kwarg is not substitutable for one that doesn't."""

    tools: tuple[dict[str, Any], ...] | None = None
    """OpenAI function-tool definitions (`{"type": "function", "function":
    {...}}`), passed through to the provider verbatim. `None` means no tools
    are offered for this call — the OpenAI wire shape was chosen because it
    is what OpenRouter's own endpoint already speaks, so this field needs no
    translation on that adapter and only a small one on the Anthropic side."""


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

    tool_calls: tuple[ToolCall, ...] = ()
    """Populated when `stop_reason` is `TOOL_USE`. Empty tuple rather than
    `None` when there are none, so a caller can iterate unconditionally."""

    @property
    def refused(self) -> bool:
        return self.stop_reason is StopReason.REFUSAL


class CompletionError(RuntimeError):
    """A call that failed after exhausting retries.

    Distinct from a refusal. A refusal is a successful call with a decision
    attached; this is the call not happening.
    """


class ToolsNotSupportedError(CompletionError):
    """The provider rejected `tools` for this model.

    A distinct subtype of `CompletionError` (not a new sibling) so any
    existing `except CompletionError` call site keeps working unchanged,
    while the runner can `except ToolsNotSupportedError` specifically to
    skip tool-surface tests for that one model and say so in the report
    rather than aborting the whole scan (§1.3, honest coverage over fake
    completeness)."""


class Completions(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...


@runtime_checkable
class RetryListening(Protocol):
    """An OPTIONAL capability some adapters offer on top of `Completions`: the
    ability to be told, out of band, that a call just hit a retryable rate
    limit (HTTP 429). The port itself only promises `complete`; a caller that
    wants to react to throttling (`snag.runner`'s adaptive concurrency limiter)
    probes for this with `isinstance(x, RetryListening)` and degrades to a
    fixed bound when it isn't there — which is exactly what `FakeCompletions`
    and any minimal adapter do.

    The listener is invoked synchronously from inside `complete` when a 429 is
    seen (before the adapter's own backoff), so it must not block or await.
    Registration returns a zero-argument cancel callback."""

    def add_retry_listener(self, listener: Callable[[], None]) -> Callable[[], None]: ...


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
