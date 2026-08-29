from __future__ import annotations

import pytest

from substrate.llm import (
    CompletionRequest,
    CompletionResponse,
    Completions,
    FakeCompletions,
    Message,
    Role,
    StopReason,
    TokenUsage,
)


def _request(text: str = "hello") -> CompletionRequest:
    return CompletionRequest(
        model="claude-opus-5",
        system="You are terse.",
        messages=(Message(Role.USER, text),),
    )


def _response(text: str = "hi", stop: StopReason = StopReason.END_TURN) -> CompletionResponse:
    return CompletionResponse(
        text=text, usage=TokenUsage(10, 5), stop_reason=stop, model="claude-opus-5"
    )


def test_fake_satisfies_the_protocol() -> None:
    fake: Completions = FakeCompletions()
    assert hasattr(fake, "complete")


@pytest.mark.asyncio
async def test_fake_records_what_it_was_asked() -> None:
    fake = FakeCompletions(responses=[_response()])
    await fake.complete(_request("what is the grace period"))
    assert "grace period" in fake.last.messages[0].content


@pytest.mark.asyncio
async def test_running_out_of_scripted_responses_is_a_loud_failure() -> None:
    """A fake that returns a default when the script runs out will let a test
    pass while the code under test made a call nobody expected."""
    fake = FakeCompletions()
    with pytest.raises(AssertionError):
        await fake.complete(_request())


@pytest.mark.asyncio
async def test_refusal_is_not_an_exception() -> None:
    fake = FakeCompletions(responses=[_response(text="", stop=StopReason.REFUSAL)])
    result = await fake.complete(_request())
    assert result.refused
    assert result.text == ""
