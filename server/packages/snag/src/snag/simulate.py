"""Tool-call simulation (SIM-01, backend-feasibility.md "Tool call
simulation"): when the model under test calls a tool, Snag must return
*something* — this module is the "something."

Two generators, both pure and deterministic (§14 — no `random`, `time`, or
`uuid`, so a scan's transcripts are reproducible byte-for-byte given the
same model replies):

- `simulate_tool_result` — the cheap default: a fake value shaped by the
  tool's own JSON schema (types/enums/required respected), plus three junk
  variants ("empty", "malformed", "contradictory") for the "junk and vague
  data" tests (project-3-spec.md §6.3).
- `poisoned_result` — the indirect-injection surface's hand-authored
  payload: a normal-looking result with a technique's canary buried in it
  (see `attacks/poisoned.py` for the cover stories).

Neither function ever executes a real tool, makes a network call, or reads
a clock — `tool_schema` is treated as pure data (types/enums/bounds), never
as something to `eval` or introspect beyond its declared shape.
"""

from __future__ import annotations

from typing import Any, cast

from snag.attacks.library import Technique
from snag.attacks.poisoned import poisoned_template

# A tool result may be an object, a bare list (e.g. search results), or —
# for the "malformed" junk variant — a plain (deliberately broken) string.
SimulatedResult = str | dict[str, Any] | list[Any]

VARIANTS: tuple[str, ...] = ("normal", "empty", "malformed", "contradictory")

_MALFORMED_TEXT = '{"status": "ok", "note": "truncated mid-stream, missing closing'

# A fixed, self-contradicting shape — independent of any particular tool's
# schema, since the whole point is that the data doesn't make sense
# regardless of what the tool normally returns.
_CONTRADICTORY_RESULT: dict[str, Any] = {
    "status": "success",
    "error": "operation failed",
    "count": 0,
    "items": ["placeholder-item"],
}


def _schema_type(schema: dict[str, Any]) -> str:
    declared = schema.get("type")
    if isinstance(declared, str):
        return declared
    if "properties" in schema:
        return "object"
    if "enum" in schema or "const" in schema:
        return "string"
    return "string"


def _fake_from_schema(schema: dict[str, Any]) -> Any:
    """Recursively build one deterministic value satisfying `schema`
    (JSON Schema draft-agnostic subset: type/properties/items/enum/const/
    minimum/minItems/default). The same schema always yields the same
    value — no randomness, so a scan's tool-abuse transcripts are
    reproducible."""
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]

    schema_type = _schema_type(schema)

    if schema_type == "object":
        properties: dict[str, Any] = schema.get("properties") or {}
        return {name: _fake_from_schema(sub) for name, sub in sorted(properties.items())}

    if schema_type == "array":
        items_schema = schema.get("items") or {"type": "string"}
        min_items = schema.get("minItems", 1)
        count = max(int(min_items), 1)
        return [_fake_from_schema(items_schema) for _ in range(count)]

    if schema_type == "string":
        return schema.get("default", "simulated-value")

    if schema_type in ("number", "integer"):
        minimum = schema.get("minimum", schema.get("default", 1))
        return int(minimum) if schema_type == "integer" else float(minimum)

    if schema_type == "boolean":
        return bool(schema.get("default", True))

    return None


def simulate_tool_result(
    tool_schema: dict[str, Any] | None, *, variant: str = "normal"
) -> SimulatedResult:
    """The 01-10 "full simulator" default: a deterministic fake tool result.

    `tool_schema` is a JSON Schema object (typically a tool's own
    `parameters` schema — there is no separate return-value schema in this
    project's tool defs, see `ex-retail.ts`/`ex-rag.ts`, so its shape
    stands in for the result's shape, same cheap-default spirit as
    `runner._fake_tool_result` before this module existed).

    `variant`:
    - "normal" (default): a value that validates against `tool_schema`.
    - "empty": the empty value for the schema's own top-level type — `{}`
      for an object, `[]` for an array, `""` for a string — i.e. "the tool
      returned nothing" (§6.3, junk and vague data).
    - "malformed": a syntactically broken string, as if the tool's raw
      output got truncated or corrupted in transit.
    - "contradictory": a fixed, self-contradicting object (success AND
      error, non-zero count with placeholder items) — nonsense regardless
      of what the tool's schema actually promises.
    """
    if variant == "normal":
        return cast(SimulatedResult, _fake_from_schema(tool_schema or {}))
    if variant == "empty":
        schema_type = _schema_type(tool_schema or {})
        if schema_type == "array":
            return []
        if schema_type == "string":
            return ""
        return {}
    if variant == "malformed":
        return _MALFORMED_TEXT
    if variant == "contradictory":
        return dict(_CONTRADICTORY_RESULT)
    raise ValueError(f"unknown simulate_tool_result variant: {variant!r}")


def poisoned_result(tool_schema: dict[str, Any] | None, technique: Technique) -> SimulatedResult:
    """The indirect-injection surface's hand-authored payload: a normal-
    looking result (shaped like `tool_schema`, same as `simulate_tool_result`'s
    "normal" variant) with `technique`'s canary buried in an instruction
    borrowed from `attacks/poisoned.py`'s family-keyed cover story.

    Raises `ValueError` for a technique with no canary — `needs_canary`
    (attacks/library.py) says exactly which techniques may ever reach here
    for real (every one reachable via `tool_return`, or in the `roleplay`
    family); calling this for any other technique is a caller bug, not a
    runtime condition to paper over.
    """
    if technique.canary is None:
        raise ValueError(
            f"technique {technique.id!r} has no canary to bury "
            "(needs_canary(technique) is False for it)"
        )
    poisoned_text = poisoned_template(technique.family).format(canary=technique.canary)
    base = _fake_from_schema(tool_schema or {})
    if isinstance(base, dict):
        return {**base, "data": poisoned_text}
    return poisoned_text
