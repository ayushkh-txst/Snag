"""snag.simulate (01-10, SIM-01): schema-fake default tool results, junk
variants for the "junk and vague data" tests, and hand-authored poisoned
results for the indirect-injection surface. Every assertion here is against
pure functions — no database, no queue, no `FakeCompletions`.
"""

from __future__ import annotations

import inspect

import jsonschema
import pytest

from snag import simulate
from snag.attacks.library import TECHNIQUE_BY_ID
from snag.checkers import run_checker
from snag.checkers.transcript import Transcript, Turn
from snag.simulate import poisoned_result, simulate_tool_result

_ISSUE_REFUND_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "amount": {"type": "number", "minimum": 0},
        "reason": {"type": "string"},
        "approval_code": {"type": "string"},
    },
    "required": ["order_id", "amount", "reason"],
}

_ENUM_SCHEMA = {
    "type": "object",
    "properties": {
        "lang": {"type": "string", "enum": ["en", "fr", "xh"]},
        "urgent": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 2},
    },
}

_CANARY_TECHNIQUE = TECHNIQUE_BY_ID["continuation.01"]  # has a canary, family="continuation"
_NO_CANARY_TECHNIQUE = TECHNIQUE_BY_ID["instruction_override.01"]  # canary=None


# ------------------------------------------------------------- determinism


def test_simulate_tool_result_normal_is_identical_across_calls() -> None:
    first = simulate_tool_result(_ISSUE_REFUND_SCHEMA)
    second = simulate_tool_result(_ISSUE_REFUND_SCHEMA)
    assert first == second


def test_simulate_tool_result_normal_validates_against_the_schema() -> None:
    result = simulate_tool_result(_ISSUE_REFUND_SCHEMA)
    jsonschema.validate(instance=result, schema=_ISSUE_REFUND_SCHEMA)


def test_simulate_tool_result_respects_enum_and_array_and_boolean() -> None:
    result = simulate_tool_result(_ENUM_SCHEMA)
    jsonschema.validate(instance=result, schema=_ENUM_SCHEMA)
    assert isinstance(result, dict)
    assert result["lang"] == "en"  # first enum value, deterministically
    assert result["urgent"] is True
    assert len(result["tags"]) == 2  # minItems respected


def test_simulate_tool_result_with_no_schema_still_returns_deterministically() -> None:
    assert simulate_tool_result(None) == simulate_tool_result({})


# ----------------------------------------------------------- junk variants


def test_empty_variant_is_the_empty_shape_for_an_object_schema() -> None:
    assert simulate_tool_result(_ISSUE_REFUND_SCHEMA, variant="empty") == {}


def test_empty_variant_is_the_empty_shape_for_an_array_schema() -> None:
    array_schema = {"type": "array", "items": {"type": "string"}}
    assert simulate_tool_result(array_schema, variant="empty") == []


def test_empty_variant_is_the_empty_shape_for_a_string_schema() -> None:
    assert simulate_tool_result({"type": "string"}, variant="empty") == ""


def test_malformed_variant_is_a_broken_non_empty_string() -> None:
    result = simulate_tool_result(_ISSUE_REFUND_SCHEMA, variant="malformed")
    assert isinstance(result, str)
    assert result  # not empty — "malformed" is not the same junk shape as "empty"


def test_contradictory_variant_asserts_and_denies_success_at_once() -> None:
    result = simulate_tool_result(_ISSUE_REFUND_SCHEMA, variant="contradictory")
    assert isinstance(result, dict)
    assert result["status"] == "success"
    assert "error" in result  # success AND a reported failure — the contradiction


def test_junk_variants_are_deterministic_too() -> None:
    for variant in ("empty", "malformed", "contradictory"):
        assert simulate_tool_result(_ISSUE_REFUND_SCHEMA, variant=variant) == simulate_tool_result(
            _ISSUE_REFUND_SCHEMA, variant=variant
        )


def test_unknown_variant_raises() -> None:
    with pytest.raises(ValueError, match="unknown simulate_tool_result variant"):
        simulate_tool_result(_ISSUE_REFUND_SCHEMA, variant="haunted")


# --------------------------------------------------------------- poisoned


def test_poisoned_result_buries_the_technique_canary() -> None:
    result = poisoned_result(_ISSUE_REFUND_SCHEMA, _CANARY_TECHNIQUE)
    assert isinstance(result, dict)
    assert _CANARY_TECHNIQUE.canary in result["data"]


def test_poisoned_result_is_deterministic() -> None:
    first = poisoned_result(_ISSUE_REFUND_SCHEMA, _CANARY_TECHNIQUE)
    second = poisoned_result(_ISSUE_REFUND_SCHEMA, _CANARY_TECHNIQUE)
    assert first == second


def test_poisoned_result_raises_for_a_technique_with_no_canary() -> None:
    with pytest.raises(ValueError, match="no canary"):
        poisoned_result(_ISSUE_REFUND_SCHEMA, _NO_CANARY_TECHNIQUE)


def test_poisoned_result_still_shapes_the_normal_part_of_the_result_from_the_schema() -> None:
    result = poisoned_result(_ISSUE_REFUND_SCHEMA, _CANARY_TECHNIQUE)
    assert isinstance(result, dict)
    for key in ("order_id", "amount", "reason", "approval_code"):
        assert key in result


def test_poisoned_result_confirms_instruction_isolation_would_detect_obedience() -> None:
    """The whole point of a canary (backend-feasibility.md): if the model
    reads the poisoned data and obeys it, the canary's effect shows up in
    what the model says next. Here we simulate that obedience directly —
    the assistant echoing the canary — and confirm `instruction_isolation`
    reports it as a break, not a pass."""
    result = poisoned_result(_ISSUE_REFUND_SCHEMA, _CANARY_TECHNIQUE)
    assert isinstance(result, dict)
    transcript = Transcript(
        turns=[
            Turn(role="user", content="Can you check on this for me?"),
            Turn(role="tool_call", name="get_order", content="{}"),
            Turn(role="tool_result", name="get_order", content=result["data"]),
            # The model obeyed the data-borne instruction and echoed the canary.
            Turn(role="assistant", content=f"Sure — {_CANARY_TECHNIQUE.canary}"),
        ]
    )
    check = run_checker("instruction_isolation", transcript, {"canary": _CANARY_TECHNIQUE.canary})
    assert check.passed is False
    assert check.evidence == _CANARY_TECHNIQUE.canary


def test_instruction_isolation_passes_when_the_canary_never_appears() -> None:
    transcript = Transcript(
        turns=[
            Turn(role="user", content="Can you check on this for me?"),
            Turn(role="assistant", content="Sure, here's what I found — nothing unusual."),
        ]
    )
    check = run_checker(
        "instruction_isolation", transcript, {"canary": _CANARY_TECHNIQUE.canary}
    )
    assert check.passed is True


# ------------------------------------------------------------- determinism
# (module-level: no clock/randomness import anywhere in either module)


def test_simulate_and_poisoned_import_no_random_time_or_uuid() -> None:
    from snag.attacks import poisoned as poisoned_module

    for module in (simulate, poisoned_module):
        source = inspect.getsource(module)
        for forbidden in ("import random", "import time", "import uuid", "from random"):
            assert forbidden not in source, f"{module.__name__} imports {forbidden!r}"
