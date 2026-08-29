"""Injection-point mapping (spec §5): every place text flows into the model.

Three pure, deterministic building blocks — no model calls, no I/O:

- `detect_template_slots` finds template syntaxes in the pasted system
  prompt (§5.1) and lists each as a high-severity `template_var` surface.
- `classify_tool_params` walks a tool-definitions JSON list and classifies
  every parameter by shape into a risk (§5.2), recursing into object/array
  fields.
- `build_surface_map` combines both with the always-present `chat` surface
  and one `tool_return` surface per tool (§5.3) into the full, confirmable
  map (§5.4) the API layer (`api/routers/surfaces.py`) persists.

T-07-01/T-07-02 (threat register): `classify_tool_params` bounds its own
recursion depth and the total number of schema nodes it will walk, and
never raises on malformed tool JSON — an unclassifiable node falls back to
a safe "medium" risk and is flagged in its `note` for the user to confirm,
rather than crashing the whole map.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

SurfaceKind = Literal["template_var", "tool_param", "tool_return", "chat"]
Risk = Literal["high", "medium", "low", "none"]

# T-07-01: a bound on both recursion depth and total schema nodes visited,
# so a deeply nested or enormous tool schema can't turn classification into
# an unbounded (or exponential) walk.
_MAX_RECURSION_DEPTH = 6
_MAX_SCHEMA_NODES = 500

# §5.4's own example numbers, reused as a simple, deterministic per-risk
# baseline — every surface at a given risk starts with the same test count;
# 01-09/01-10 own the real per-attack count once a scan actually runs.
BASE_TESTS_BY_RISK: dict[Risk, int] = {"high": 28, "medium": 12, "low": 4, "none": 0}

# A parameter (or template-var/tool-return path) whose leaf name looks like
# it's pulled from a session rather than typed by an attacker — SURFACE-02's
# "a user_id pulled from a session isn't attacker-controlled" heuristic.
_SESSION_LIKE_RE = re.compile(r"(?:^|_)(id|session|token|auth|key)(?:$|_)", re.IGNORECASE)

# §5.1: the four template syntaxes, tried in this order at every position so
# `{{...}}` is always matched whole before `{...}` gets a chance to match
# its inner half (Python's `re` tries alternatives left-to-right and takes
# the first that succeeds at the current position — finditer never revisits
# characters already consumed by an earlier match).
_SLOT_RE = re.compile(
    r"\{\{[^{}]+\}\}"
    r"|\{[^{}]+\}"
    r"|<<[^<>]+>>"
    r"|%s"
)


@dataclass(frozen=True, slots=True)
class SurfaceSpec:
    """One detected/classified surface, ready to persist. Mirrors the UI's
    `Surface` shape (`src/data/types.ts`) minus `id`/`confirmed`, which only
    exist once a row is in the database."""

    kind: SurfaceKind
    path: str
    source: str
    risk: Risk
    tests: int
    user_controlled: bool
    note: str = ""


def _default_user_controlled(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1]
    return not bool(_SESSION_LIKE_RE.search(leaf))


def detect_template_slots(prompt: str | None) -> list[SurfaceSpec]:
    """§5.1: every `{{...}}`, `{...}`, `<<...>>`, and `%s` slot in the
    pasted prompt is the highest-severity surface there is — content
    landing there sits at the same level as the rules themselves.

    Duplicate slots (the same syntax appearing more than once) collapse to
    one surface, in first-seen order.
    """
    if not prompt:
        return []
    seen: dict[str, None] = {}
    for match in _SLOT_RE.finditer(prompt):
        seen.setdefault(match.group(0), None)
    return [
        SurfaceSpec(
            kind="template_var",
            path=slot_text,
            source="prompt template",
            risk="high",
            tests=BASE_TESTS_BY_RISK["high"],
            user_controlled=True,
            note=(
                "Filled in at runtime and read as part of your instructions, "
                "not as something you're quoting."
            ),
        )
        for slot_text in seen
    ]


def _normalize_tools(tools_json: Any) -> list[dict[str, Any]]:
    """Defensive parsing (T-07-02): accepts the parsed list asyncpg hands
    back for a `jsonb` column, a raw JSON string, a `{"tools": [...]}`
    wrapper, or a single tool dict — anything else (`None`, garbage)
    becomes an empty list rather than raising."""
    if tools_json is None:
        return []
    if isinstance(tools_json, str):
        try:
            tools_json = json.loads(tools_json)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(tools_json, dict):
        candidate = tools_json.get("tools")
        tools_json = candidate if isinstance(candidate, list) else [tools_json]
    if not isinstance(tools_json, list):
        return []
    return [t for t in tools_json if isinstance(t, dict)]


def _tool_name(tool: dict[str, Any]) -> str:
    name = tool.get("name")
    return str(name).strip() if name else ""


def _tool_params_schema(tool: dict[str, Any]) -> dict[str, Any]:
    params = tool.get("parameters")
    return params if isinstance(params, dict) else {}


def _leaf_risk(schema: dict[str, Any]) -> Risk:
    """§5.2's shape -> risk table, for a schema node that is not itself an
    object/array (those recurse instead of being classified directly)."""
    if "enum" in schema:
        return "low"
    schema_type = schema.get("type")
    if schema_type == "boolean":
        return "none"
    if schema_type == "string":
        return "medium" if (schema.get("pattern") or schema.get("format")) else "high"
    if schema_type in ("number", "integer"):
        return "medium"
    # Unknown/missing `type`, or any other malformed shape: T-07-02's safe
    # default — flagged in the note so the user notices and confirms it.
    return "medium"


_RISK_NOTE: dict[Risk, str] = {
    "high": "Free text reaches the model with nothing filtering it first.",
    "medium": "Constrained, but not tightly enough to rule out.",
    "low": "Restricted to a closed, tightly limited set.",
    "none": "Only a fixed, small set of values — nothing to attack.",
}


def _make_param_surface(path: str, risk: Risk, *, unclassifiable: bool = False) -> SurfaceSpec:
    note = _RISK_NOTE[risk]
    if unclassifiable:
        note = "Could not classify this parameter automatically — please confirm its risk."
    return SurfaceSpec(
        kind="tool_param",
        path=path,
        source="tool parameter",
        risk=risk,
        tests=BASE_TESTS_BY_RISK[risk],
        user_controlled=_default_user_controlled(path),
        note=note,
    )


class _Budget:
    """Mutable node counter threaded through the recursion (T-07-01)."""

    __slots__ = ("remaining",)

    def __init__(self, remaining: int) -> None:
        self.remaining = remaining


def _classify_property(path: str, schema: Any, depth: int, budget: _Budget) -> list[SurfaceSpec]:
    if budget.remaining <= 0 or depth > _MAX_RECURSION_DEPTH:
        return []
    budget.remaining -= 1

    if not isinstance(schema, dict):
        # T-07-02: a malformed node (not even a dict) can't be shape-
        # classified — default to "medium" and say so, rather than raising.
        return [_make_param_surface(path, "medium", unclassifiable=True)]

    schema_type = schema.get("type")

    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            return []
        out: list[SurfaceSpec] = []
        for sub_name, sub_schema in properties.items():
            out.extend(_classify_property(f"{path}.{sub_name}", sub_schema, depth + 1, budget))
        return out

    if schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict) and items.get("type") == "object":
            properties = items.get("properties")
            if not isinstance(properties, dict) or not properties:
                return []
            out = []
            for sub_name, sub_schema in properties.items():
                out.extend(
                    _classify_property(f"{path}.{sub_name}", sub_schema, depth + 1, budget)
                )
            return out
        # An array of primitives (or an array with no usable `items`
        # schema) is classified as one surface at the array's own path.
        leaf_schema = items if isinstance(items, dict) else {}
        return [_make_param_surface(path, _leaf_risk(leaf_schema))]

    return [_make_param_surface(path, _leaf_risk(schema))]


def classify_tool_params(tools_json: Any) -> list[SurfaceSpec]:
    """§5.2: every tool parameter, classified by shape into a risk —
    free-text string -> high, patterned string/format -> medium, unbounded
    number -> medium, enum -> low, boolean -> none — recursing into
    object/array fields (path = `tool.param[.subfield...]`)."""
    tools = _normalize_tools(tools_json)
    budget = _Budget(_MAX_SCHEMA_NODES)
    surfaces: list[SurfaceSpec] = []
    for tool in tools:
        name = _tool_name(tool)
        if not name:
            continue
        properties = _tool_params_schema(tool).get("properties")
        if not isinstance(properties, dict):
            continue
        for prop_name, prop_schema in properties.items():
            surfaces.extend(_classify_property(f"{name}.{prop_name}", prop_schema, 0, budget))
    return surfaces


def _tool_return_surfaces(tools: list[dict[str, Any]]) -> list[SurfaceSpec]:
    """§5.3: whatever a tool returns is read by the model as fact — one
    `tool_return` surface per tool, defaulted to `high` risk since a tool's
    return value is, by construction, content the app itself doesn't fully
    control (search results, documents, external API responses)."""
    surfaces = []
    for tool in tools:
        name = _tool_name(tool)
        if not name:
            continue
        surfaces.append(
            SurfaceSpec(
                kind="tool_return",
                path=f"{name} → return value",
                source="tool output",
                risk="high",
                tests=BASE_TESTS_BY_RISK["high"],
                user_controlled=True,
                note="Whatever this tool sends back is read by the model as fact.",
            )
        )
    return surfaces


def _chat_surface() -> SurfaceSpec:
    return SurfaceSpec(
        kind="chat",
        path="user message",
        source="chat input",
        risk="high",
        tests=BASE_TESTS_BY_RISK["high"],
        user_controlled=True,
        note="Whatever someone types. Always tested.",
    )


def build_surface_map(prompt: str | None, tools_json: Any) -> list[SurfaceSpec]:
    """§5.4: the full, confirmable map — chat, then every template slot,
    then every tool parameter, then every tool's return value. Pure and
    deterministic: identical inputs always yield an identical,
    identically-ordered list."""
    tools = _normalize_tools(tools_json)
    return [
        _chat_surface(),
        *detect_template_slots(prompt),
        *classify_tool_params(tools_json),
        *_tool_return_surfaces(tools),
    ]
