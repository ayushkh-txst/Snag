"""Format checkers (§4 "Format" table) — does the reply parse as the shape
it was supposed to. Pure parse-and-check; `json_schema` uses the
`jsonschema` library only to validate a JSON *document* against a JSON
*Schema* the LLM filled in at setup — never to execute anything.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import jsonschema
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from snag.checkers import CheckResult, bounded_text, register
from snag.checkers.transcript import Transcript

_MARKDOWN_TOKENS = ("# ", "## ", "### ", "- ", "* ", "**", "```", "](")


@register("json_parseable")
def json_parseable(transcript: Transcript, config: dict[str, Any]) -> CheckResult:
    text = bounded_text(transcript.assistant_text()).strip()
    try:
        json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return CheckResult(False, "reply is not valid JSON")
    return CheckResult(True, "reply is valid JSON")


@register("json_schema")
def json_schema_checker(transcript: Transcript, config: dict[str, Any]) -> CheckResult:
    text = bounded_text(transcript.assistant_text()).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return CheckResult(False, "reply is not valid JSON")
    schema = config.get("schema", {})
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        return CheckResult(
            False, f"reply does not match schema: {exc.message}", evidence=exc.message
        )
    except jsonschema.SchemaError as exc:
        return CheckResult(False, f"checker_config schema is itself invalid: {exc.message}")
    return CheckResult(True, "reply matches schema")


@register("required_fields")
def required_fields(transcript: Transcript, config: dict[str, Any]) -> CheckResult:
    text = bounded_text(transcript.assistant_text()).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return CheckResult(False, "reply is not valid JSON, cannot check required fields")
    if not isinstance(data, dict):
        return CheckResult(False, "reply JSON is not an object, cannot check required fields")
    missing = [f for f in config.get("fields", []) if not data.get(f)]
    if missing:
        return CheckResult(False, f"required fields missing or empty: {missing}")
    return CheckResult(True, "all required fields present and non-empty")


@register("enum_value")
def enum_value(transcript: Transcript, config: dict[str, Any]) -> CheckResult:
    text = bounded_text(transcript.assistant_text()).strip()
    allowed = config.get("allowed", [])
    value: Any = text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, str):
            value = parsed
    except (json.JSONDecodeError, ValueError):
        pass
    if value in allowed:
        return CheckResult(True, f"value {value!r} is allowed")
    return CheckResult(False, f"value {value!r} is not in the allowed set", evidence=str(value))


def _looks_like_markdown(text: str) -> bool:
    return any(token in text for token in _MARKDOWN_TOKENS)


@register("markup_format")
def markup_format(transcript: Transcript, config: dict[str, Any]) -> CheckResult:
    text = bounded_text(transcript.assistant_text()).strip()
    fmt = config.get("format", "markdown")
    if fmt == "xml":
        # defusedxml, not stdlib xml.etree — the reply is adversarial
        # content by design, so parsing it must not be a billion-laughs /
        # external-entity vector (S314).
        try:
            ET.fromstring(text)
        except ET.ParseError as exc:
            return CheckResult(False, f"reply is not valid XML: {exc}")
        except DefusedXmlException as exc:
            return CheckResult(False, f"reply is not safe XML: {exc}")
        return CheckResult(True, "reply parses as XML")
    if fmt == "csv":
        try:
            rows = [row for row in csv.reader(io.StringIO(text)) if row]
        except csv.Error as exc:
            return CheckResult(False, f"reply is not valid CSV: {exc}")
        if not rows:
            return CheckResult(False, "reply is not valid CSV (no rows)")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            return CheckResult(False, "reply is not valid CSV (inconsistent column count)")
        return CheckResult(True, "reply parses as CSV")
    if fmt == "markdown":
        if _looks_like_markdown(text):
            return CheckResult(True, "reply contains recognizable markdown formatting")
        return CheckResult(False, "reply contains no recognizable markdown formatting")
    return CheckResult(False, f"unknown markup format in checker_config: {fmt!r}")
