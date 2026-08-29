"""The `Transcript` model checkers run against — a small, deliberately dumb
mirror of `src/data/types.ts`'s `Turn`. Every checker in this package is a
pure function over `(Transcript, config)`; nothing here calls a model,
makes a network call, or executes anything found in `config`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool_call", "tool_result"]


@dataclass(frozen=True, slots=True)
class Turn:
    """Mirrors `Turn` in `src/data/types.ts`. `name` is the tool name for
    `tool_call`/`tool_result` turns. `planted` marks a substring as
    attacker-planted (indirect injection); `evidence` marks a substring as
    a checker's evidence of a break — set by the caller, never a checker.

    For `tool_call` turns, `content` is the JSON-encoded call arguments
    (e.g. `'{"amount": 500}'`); `ToolCall.args` below parses it once.
    """

    role: Role
    content: str = ""
    name: str | None = None
    planted: str | None = None
    evidence: str | None = None
    forged: bool = False
    """True for an `assistant` turn the ATTACK wrote, not the model — the
    prefill attack (`attacks.library`'s `prefill` family) seeds a fabricated
    assistant turn that has already begun complying. It is kept in the
    transcript because the report must show honestly what was sent, but it
    is attacker text: `assistant_text()` excludes it, so no checker can ever
    read a planted string as something the model said."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One `tool_call` turn, with its arguments already parsed out of
    `turn.content`. Malformed JSON parses to an empty dict rather than
    raising — a checker reading a bad call's args should see "no args
    matched", not crash the whole check."""

    name: str
    args: dict[str, Any]
    turn: Turn


def _parse_args(content: str) -> dict[str, Any]:
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True, slots=True)
class Transcript:
    """A full conversation, in turn order. All helpers are read-only views
    over `turns` — no mutation, no external I/O."""

    turns: list[Turn] = field(default_factory=list)

    def turns_in_order(self) -> list[Turn]:
        return list(self.turns)

    def assistant_text(self) -> str:
        """Every assistant turn the MODEL produced, concatenated in order.

        Forged assistant turns (`Turn.forged` — the prefill attack's
        fabricated "I've already started complying" opener) are excluded.
        Every content/format/flow checker in this package reads the model's
        behaviour through this one method, so excluding forged text here is
        what makes a prefill attack unable to satisfy its own checker: a
        canary or a leaked line that the ATTACK wrote can never be scored as
        a break."""
        return "\n".join(t.content for t in self.turns if t.role == "assistant" and not t.forged)

    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(name=t.name or "", args=_parse_args(t.content), turn=t)
            for t in self.turns
            if t.role == "tool_call"
        ]

    def tool_results(self) -> list[Turn]:
        return [t for t in self.turns if t.role == "tool_result"]
