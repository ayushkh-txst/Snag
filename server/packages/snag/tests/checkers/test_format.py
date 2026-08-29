"""Hand-written pass/fail fixtures for every format checker (CHECK-01)."""

from __future__ import annotations

from snag.checkers import run_checker
from snag.checkers.transcript import Transcript, Turn


def _assistant(text: str) -> Transcript:
    return Transcript(turns=[Turn(role="user", content="hi"), Turn(role="assistant", content=text)])


def test_json_parseable_fails_on_bad_json() -> None:
    result = run_checker("json_parseable", _assistant("not json at all"), {})
    assert result.passed is False


def test_json_parseable_passes_on_good_json() -> None:
    result = run_checker("json_parseable", _assistant('{"ok": true}'), {})
    assert result.passed is True


SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


def test_json_schema_fails_on_mismatch() -> None:
    result = run_checker("json_schema", _assistant('{"age": 5}'), {"schema": SCHEMA})
    assert result.passed is False


def test_json_schema_passes_on_match() -> None:
    result = run_checker("json_schema", _assistant('{"name": "Ada"}'), {"schema": SCHEMA})
    assert result.passed is True


def test_required_fields_fails_when_missing() -> None:
    result = run_checker("required_fields", _assistant('{"name": ""}'), {"fields": ["name"]})
    assert result.passed is False


def test_required_fields_passes_when_present() -> None:
    result = run_checker("required_fields", _assistant('{"name": "Ada"}'), {"fields": ["name"]})
    assert result.passed is True


def test_enum_value_fails_on_disallowed() -> None:
    result = run_checker("enum_value", _assistant("purple"), {"allowed": ["red", "green", "blue"]})
    assert result.passed is False


def test_enum_value_passes_on_allowed() -> None:
    result = run_checker("enum_value", _assistant("red"), {"allowed": ["red", "green", "blue"]})
    assert result.passed is True


def test_markup_format_fails_on_plain_text_for_markdown() -> None:
    result = run_checker(
        "markup_format", _assistant("just a plain sentence"), {"format": "markdown"}
    )
    assert result.passed is False


def test_markup_format_passes_on_markdown() -> None:
    result = run_checker(
        "markup_format", _assistant("## Heading\n- item one"), {"format": "markdown"}
    )
    assert result.passed is True


def test_markup_format_fails_on_bad_xml() -> None:
    result = run_checker("markup_format", _assistant("<a><b></a>"), {"format": "xml"})
    assert result.passed is False


def test_markup_format_passes_on_good_xml() -> None:
    result = run_checker("markup_format", _assistant("<a><b>hi</b></a>"), {"format": "xml"})
    assert result.passed is True
