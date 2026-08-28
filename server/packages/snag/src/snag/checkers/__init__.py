"""The §4 checker registry.

A fixed menu of pure, side-effect-free functions — `(Transcript, config) ->
CheckResult`. An LLM picks one of these 31 named types (plus `none`) and
fills in `config` once, at setup time; this registry runs the actual check
on every reply, every time, deterministically. No checker here ever
imports an LLM client, calls a network, or executes anything found in
`config` — `config` is data only (strings, regexes, numbers, lists).

The four checkers that look "fuzzy" (`no_prompt_leak`, `instruction_isolation`,
`no_role_confusion`, `refusal_expected`) stay mechanical by consuming
setup-time data supplied in `config` — translations, canaries, refusal
markers — rather than judging anything themselves. See
`.planning/notes/backend-feasibility.md`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from snag.checkers.transcript import Transcript

# Regex- and substring-driven checkers bound the text they search (T-03-01):
# a fixed cap means no checker's running time depends on an attacker-chosen
# input beyond this ceiling, regardless of pattern complexity.
MAX_CHECK_INPUT_LENGTH = 20_000


def bounded_text(text: str) -> str:
    """Cap `text` to `MAX_CHECK_INPUT_LENGTH` before any regex or run-length
    search touches it — the ReDoS mitigation every content/format/tool
    checker in this package shares."""
    return text[:MAX_CHECK_INPUT_LENGTH]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The result of running one checker once. `parts` is populated only by
    checkers that report multiple named sub-results (currently
    `refusal_expected`) so a failure can name which part failed instead of
    showing one opaque red mark."""

    passed: bool
    output: str
    evidence: str | None = None
    parts: dict[str, bool] | None = None


Checker = Callable[[Transcript, dict[str, Any]], CheckResult]

CHECKERS: dict[str, Checker] = {}


def register(name: str) -> Callable[[Checker], Checker]:
    """Decorator: `@register("forbidden_text")` adds the function to
    `CHECKERS` under that name and returns it unchanged."""

    def _wrap(fn: Checker) -> Checker:
        CHECKERS[name] = fn
        return fn

    return _wrap


def run_checker(name: str, transcript: Transcript, config: dict[str, Any]) -> CheckResult:
    """Look `name` up in `CHECKERS` and run it. Raises `KeyError` for an
    unregistered name — that is a configuration bug upstream (the Questions
    step should never hand this an unknown checker type), not something to
    swallow silently."""
    try:
        checker = CHECKERS[name]
    except KeyError as exc:
        raise KeyError(f"unknown checker type: {name!r}") from exc
    return checker(transcript, config)


@register("none")
def _not_testable(transcript: Transcript, config: dict[str, Any]) -> CheckResult:
    """The one entry in the menu that isn't a real checker: some rules
    ('be helpful', 'sound friendly') have no mechanical test. Always
    returns the same not-testable sentinel and never raises, so a report
    can show it as 'needs human review' instead of erroring."""
    return CheckResult(
        passed=False,
        output="not testable by code — needs human review",
    )


# Importing these registers their functions into CHECKERS as a side effect
# of module import (the `register` decorator above). Import order among
# them doesn't matter; only that all four happen after CheckResult/register/
# bounded_text exist above, since each module imports those names back from
# this package.
from snag.checkers import content as _content  # noqa: E402,F401
from snag.checkers import flow as _flow  # noqa: E402,F401
from snag.checkers import format_checks as _format_checks  # noqa: E402,F401
from snag.checkers import tools as _tools  # noqa: E402,F401
