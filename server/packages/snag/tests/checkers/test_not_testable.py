""""No mechanical test exists" is not "the rule broke".

The `none` checker is the menu entry for rules like "be warm and brief" that
no pattern can score. It returned `passed=False` and said so in its output —
but left `applicable` at its default of True, so every attack against such a
rule was recorded as a break whose evidence read "not testable by code —
needs human review".

Live on the deployed service (scan 10): one rule with `checker_type = 'none'`
took 46 attacks and produced 46 breaks, a 100% break rate on a rule nothing
had actually tested. Fifty of that scan's fifty-one breaks were this.
"""

from __future__ import annotations

from snag.checkers import run_checker
from snag.checkers.transcript import Transcript, Turn


def _t(text: str = "Happy to help with that.") -> Transcript:
    return Transcript(turns=[Turn(role="assistant", content=text)])


def test_the_none_checker_is_not_applicable_rather_than_broken() -> None:
    result = run_checker("none", _t(), {})
    assert result.applicable is False, "nothing was tested, so there is nothing to count"


def test_it_still_says_why_a_person_has_to_look() -> None:
    result = run_checker("none", _t(), {})
    assert "human review" in result.output


def test_it_never_raises_whatever_it_is_handed() -> None:
    """It is the fallback: an empty transcript and an empty config are both
    ordinary inputs here."""
    assert run_checker("none", Transcript(turns=[]), {}).applicable is False
