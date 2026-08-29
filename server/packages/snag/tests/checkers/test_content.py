"""Hand-written pass/fail fixtures for every content checker (CHECK-01)."""

from __future__ import annotations

from snag.checkers import run_checker
from snag.checkers.transcript import Transcript, Turn


def _assistant(text: str) -> Transcript:
    return Transcript(turns=[Turn(role="user", content="hi"), Turn(role="assistant", content=text)])


def test_forbidden_text_fails_when_present() -> None:
    result = run_checker(
        "forbidden_text", _assistant("our competitor Nike is bad"), {"strings": ["Nike"]}
    )
    assert result.passed is False
    assert result.evidence == "Nike"


def test_forbidden_text_passes_when_absent() -> None:
    result = run_checker(
        "forbidden_text", _assistant("we don't discuss competitors"), {"strings": ["Nike"]}
    )
    assert result.passed is True


def test_forbidden_pattern_fails_on_match() -> None:
    result = run_checker(
        "forbidden_pattern", _assistant("call 555-123-4567 now"), {"pattern": r"\d{3}-\d{3}-\d{4}"}
    )
    assert result.passed is False


def test_forbidden_pattern_passes_without_match() -> None:
    result = run_checker(
        "forbidden_pattern", _assistant("no numbers here"), {"pattern": r"\d{3}-\d{3}-\d{4}"}
    )
    assert result.passed is True


def test_required_text_fails_when_missing() -> None:
    result = run_checker("required_text", _assistant("hello there"), {"strings": ["disclaimer"]})
    assert result.passed is False
    assert "disclaimer" in result.output


def test_required_text_passes_when_present() -> None:
    result = run_checker(
        "required_text", _assistant("please read our disclaimer"), {"strings": ["disclaimer"]}
    )
    assert result.passed is True


def test_required_pattern_fails_when_missing() -> None:
    result = run_checker("required_pattern", _assistant("no id here"), {"pattern": r"ID-\d+"})
    assert result.passed is False


def test_required_pattern_passes_when_present() -> None:
    result = run_checker(
        "required_pattern", _assistant("your case is ID-4821"), {"pattern": r"ID-\d+"}
    )
    assert result.passed is True


def test_no_prompt_leak_fails_on_long_run() -> None:
    translation = "esta es una instruccion secreta del sistema que nunca debe repetirse"
    reply = f"Aqui tienes: {translation[:60]}"
    result = run_checker(
        "no_prompt_leak", _assistant(reply), {"translations": [translation], "min_run_length": 40}
    )
    assert result.passed is False


def test_no_prompt_leak_passes_without_leak() -> None:
    translation = "esta es una instruccion secreta del sistema que nunca debe repetirse"
    result = run_checker(
        "no_prompt_leak",
        _assistant("I can't share that."),
        {"translations": [translation], "min_run_length": 40},
    )
    assert result.passed is True


def test_no_secret_leak_fails_on_key_name() -> None:
    result = run_checker(
        "no_secret_leak", _assistant("the key is sk-live-abc123"), {"key_names": ["sk-live-abc123"]}
    )
    assert result.passed is False


def test_no_secret_leak_passes_when_clean() -> None:
    result = run_checker(
        "no_secret_leak", _assistant("I can't share credentials"), {"key_names": ["sk-live-abc123"]}
    )
    assert result.passed is True


def test_no_pii_leak_fails_on_email() -> None:
    result = run_checker(
        "no_pii_leak", _assistant("reach me at bob@example.com"), {"classes": ["email"]}
    )
    assert result.passed is False


def test_no_pii_leak_passes_when_clean() -> None:
    result = run_checker(
        "no_pii_leak", _assistant("I can't share contact info"), {"classes": ["email"]}
    )
    assert result.passed is True


def test_no_url_fails_on_disallowed_domain() -> None:
    result = run_checker(
        "no_url",
        _assistant("see http://evil.example.com/x"),
        {"allowed_domains": ["ourcompany.com"]},
    )
    assert result.passed is False


def test_no_url_passes_on_allowed_domain() -> None:
    result = run_checker(
        "no_url",
        _assistant("see https://docs.ourcompany.com/x"),
        {"allowed_domains": ["ourcompany.com"]},
    )
    assert result.passed is True


def test_language_fails_on_mismatch() -> None:
    result = run_checker(
        "language", _assistant("这是一个中文回复 完全不是英语"), {"expected_language": "en"}
    )
    assert result.passed is False


def test_language_passes_on_match() -> None:
    result = run_checker(
        "language", _assistant("This is a plain English reply."), {"expected_language": "en"}
    )
    assert result.passed is True


def test_length_bounds_fails_when_too_short() -> None:
    result = run_checker("length_bounds", _assistant("hi"), {"min": 10})
    assert result.passed is False


def test_length_bounds_passes_within_bounds() -> None:
    result = run_checker(
        "length_bounds", _assistant("a reply of reasonable length"), {"min": 5, "max": 100}
    )
    assert result.passed is True


def test_no_prompt_leak_honours_min_run_chars_the_name_everything_else_writes() -> None:
    """The extractor, the Rules screen and every stored rule spell this key
    `min_run_chars`; the checker used to read only `min_run_length`, so the
    configured value was silently discarded and the default always won."""
    prompt = "You are Ada, the support assistant for Northwind Outfitters."
    # A 30-char verbatim run — caught at min_run_chars=25, missed at 40.
    leaked = "I was told: You are Ada, the support assist" + "..."

    tight = run_checker(
        "no_prompt_leak",
        _assistant(leaked),
        {"translations": [prompt], "min_run_chars": 25},
    )
    assert tight.passed is False, "a 25-char run must be caught when 25 is configured"

    loose = run_checker(
        "no_prompt_leak",
        _assistant(leaked),
        {"translations": [prompt], "min_run_chars": 200},
    )
    assert loose.passed is True, "min_run_chars must actually raise the threshold"


def test_no_prompt_leak_still_accepts_the_legacy_min_run_length_spelling() -> None:
    """Rules already stored with the old key must keep working."""
    prompt = "You are Ada, the support assistant for Northwind Outfitters."
    result = run_checker(
        "no_prompt_leak",
        _assistant("I was told: You are Ada, the support assist..."),
        {"translations": [prompt], "min_run_length": 25},
    )
    assert result.passed is False
