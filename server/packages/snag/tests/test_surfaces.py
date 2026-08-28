"""§5 injection-point mapping (SURFACE-01/02/03): template-slot detection,
tool-parameter risk classification, the combined surface map, and the
generate/list/edit endpoints built on top of them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx

from snag.surfaces import (
    BASE_TESTS_BY_RISK,
    build_surface_map,
    classify_tool_params,
    detect_template_slots,
)
from substrate.llm import CompletionResponse, FakeCompletions, StopReason, TokenUsage

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]

# ------------------------------------------------------------------- fixtures

TOOLS = [
    {
        "name": "search_docs",
        "description": "Search internal docs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "locale": {"type": "string", "pattern": "^[a-z]{2}$"},
                "limit": {"type": "integer"},
                "sort": {"type": "string", "enum": ["relevance", "date"]},
                "verbose": {"type": "boolean"},
                "filters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": ["a", "b"]},
                        "notes": {"type": "string"},
                    },
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    }
]

PROMPT_WITH_SLOT = "You are a bot for Acme. Context: {{context}}. Follow the rules."

# ------------------------------------------------------------- detect_template_slots


def test_detect_template_slots_finds_all_four_syntaxes_as_high_risk() -> None:
    prompt = "Use {{context}} and {user_input} plus <<documents>> and %s here."
    surfaces = detect_template_slots(prompt)
    paths = {s.path for s in surfaces}
    assert paths == {"{{context}}", "{user_input}", "<<documents>>", "%s"}
    assert all(s.kind == "template_var" for s in surfaces)
    assert all(s.risk == "high" for s in surfaces)
    assert all(s.user_controlled for s in surfaces)
    assert all(s.source == "prompt template" for s in surfaces)


def test_detect_template_slots_dedupes_repeated_slots_in_first_seen_order() -> None:
    prompt = "{{context}} appears twice: {{context}} plus {user_input} once."
    surfaces = detect_template_slots(prompt)
    assert [s.path for s in surfaces] == ["{{context}}", "{user_input}"]


def test_detect_template_slots_on_a_prompt_with_no_slots_is_empty() -> None:
    assert detect_template_slots("No slots here at all, just plain prose.") == []


def test_detect_template_slots_handles_none_and_empty_input() -> None:
    assert detect_template_slots(None) == []
    assert detect_template_slots("") == []


def test_detect_template_slots_does_not_double_match_nested_braces() -> None:
    surfaces = detect_template_slots("{{context}}")
    assert len(surfaces) == 1
    assert surfaces[0].path == "{{context}}"


# ------------------------------------------------------------- classify_tool_params


def test_classify_tool_params_applies_the_shape_to_risk_table() -> None:
    surfaces = classify_tool_params(TOOLS)
    by_path = {s.path: s for s in surfaces}

    assert by_path["search_docs.query"].risk == "high"
    assert by_path["search_docs.locale"].risk == "medium"
    assert by_path["search_docs.limit"].risk == "medium"
    assert by_path["search_docs.sort"].risk == "low"
    assert by_path["search_docs.verbose"].risk == "none"
    assert all(s.kind == "tool_param" for s in surfaces)
    assert all(s.source == "tool parameter" for s in surfaces)


def test_classify_tool_params_recurses_into_object_and_array_fields() -> None:
    surfaces = classify_tool_params(TOOLS)
    by_path = {s.path: s for s in surfaces}

    assert by_path["search_docs.filters.category"].risk == "low"
    assert by_path["search_docs.filters.notes"].risk == "high"
    # An array of free-text strings carries the same risk as the free-text
    # leaf shape it holds.
    assert by_path["search_docs.tags"].risk == "high"


def test_classify_tool_params_defaults_session_like_names_to_not_user_controlled() -> None:
    tools = [
        {
            "name": "get_order",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "session_token": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        }
    ]
    surfaces = classify_tool_params(tools)
    by_path = {s.path: s for s in surfaces}

    assert by_path["get_order.order_id"].user_controlled is False
    assert by_path["get_order.session_token"].user_controlled is False
    assert by_path["get_order.reason"].user_controlled is True


def test_classify_tool_params_defends_against_a_malformed_schema_node() -> None:
    tools = [
        {
            "name": "broken_tool",
            "parameters": {"type": "object", "properties": {"weird": "not-a-schema"}},
        }
    ]
    surfaces = classify_tool_params(tools)
    assert len(surfaces) == 1
    assert surfaces[0].risk == "medium"
    assert "confirm" in surfaces[0].note.lower()


def test_classify_tool_params_bounds_recursion_on_a_deeply_nested_schema() -> None:
    schema: dict[str, object] = {"type": "string"}
    for i in range(50):
        schema = {"type": "object", "properties": {f"level{i}": schema}}
    tools = [
        {"name": "deep_tool", "parameters": {"type": "object", "properties": {"root": schema}}}
    ]
    # Must return promptly with a bounded result, not hang or raise.
    surfaces = classify_tool_params(tools)
    assert isinstance(surfaces, list)
    assert len(surfaces) <= 1


def test_classify_tool_params_handles_missing_or_invalid_tools_json() -> None:
    assert classify_tool_params(None) == []
    assert classify_tool_params("not json") == []
    assert classify_tool_params([{"name": "no_params_tool"}]) == []
    assert classify_tool_params([{"parameters": {"type": "object", "properties": {}}}]) == []


# ------------------------------------------------------------------ build_surface_map


def test_build_surface_map_includes_all_four_kinds_with_source_risk_and_tests() -> None:
    surfaces = build_surface_map(PROMPT_WITH_SLOT, TOOLS)
    kinds = {s.kind for s in surfaces}
    assert kinds == {"chat", "template_var", "tool_param", "tool_return"}

    chat = next(s for s in surfaces if s.kind == "chat")
    assert chat.path == "user message"
    assert chat.source == "chat input"
    assert chat.risk == "high"
    assert chat.tests == BASE_TESTS_BY_RISK["high"]

    tool_return = next(s for s in surfaces if s.kind == "tool_return")
    assert tool_return.path == "search_docs → return value"
    assert tool_return.source == "tool output"
    assert tool_return.risk == "high"

    for s in surfaces:
        assert s.source
        assert s.risk in ("high", "medium", "low", "none")
        assert s.tests >= 0


def test_build_surface_map_is_pure_and_deterministic() -> None:
    a = build_surface_map(PROMPT_WITH_SLOT, TOOLS)
    b = build_surface_map(PROMPT_WITH_SLOT, TOOLS)
    assert a == b


def test_build_surface_map_with_no_prompt_or_tools_still_has_the_chat_surface() -> None:
    surfaces = build_surface_map("", None)
    assert len(surfaces) == 1
    assert surfaces[0].kind == "chat"


# --------------------------------------------------------------------- endpoints

EXTRACTION_JSON = json.dumps({"rules": []})
TOOLS_JSON_STR = json.dumps(TOOLS)


def _extraction_response() -> CompletionResponse:
    return CompletionResponse(
        text=EXTRACTION_JSON,
        usage=TokenUsage(100, 50),
        stop_reason=StopReason.END_TURN,
        model="qwen/qwen3.8-flash",
    )


async def _create_project(
    client: httpx.AsyncClient, *, system_prompt: str, tools: str | None = None
) -> str:
    res = await client.post(
        "/api/projects",
        json={"system_prompt": system_prompt, "tools": tools, "model": "qwen/qwen3.8-flash"},
    )
    assert res.status_code == 200, res.text
    return str(res.json()["slug"])


async def test_generate_surfaces_endpoint_persists_the_full_map(
    client_factory: ClientFactory,
) -> None:
    fake = FakeCompletions(responses=[_extraction_response()])
    async with client_factory(fake) as client:
        slug = await _create_project(client, system_prompt=PROMPT_WITH_SLOT, tools=TOOLS_JSON_STR)

        res = await client.post(f"/api/projects/{slug}/surfaces")
        assert res.status_code == 200, res.text
        surfaces = res.json()["surfaces"]

    kinds = {s["kind"] for s in surfaces}
    assert kinds == {"chat", "template_var", "tool_param", "tool_return"}

    for s in surfaces:
        assert {
            "id",
            "path",
            "kind",
            "source",
            "risk",
            "tests",
            "userControlled",
            "confirmed",
            "note",
        } <= set(s.keys())
        assert s["risk"] in ("high", "medium", "low", "none")

    template_surface = next(s for s in surfaces if s["kind"] == "template_var")
    assert template_surface["path"] == "{{context}}"
    assert template_surface["risk"] == "high"

    query_surface = next(s for s in surfaces if s["path"] == "search_docs.query")
    assert query_surface["risk"] == "high"
    limit_surface = next(s for s in surfaces if s["path"] == "search_docs.limit")
    assert limit_surface["risk"] == "medium"

    tool_return = next(s for s in surfaces if s["kind"] == "tool_return")
    assert tool_return["path"] == "search_docs → return value"


async def test_generate_surfaces_endpoint_for_unknown_slug_is_404(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.post("/api/projects/does-not-exist/surfaces")
    assert res.status_code == 404


async def test_get_surfaces_endpoint_returns_the_persisted_map_in_ui_shape(
    client_factory: ClientFactory,
) -> None:
    fake = FakeCompletions(responses=[_extraction_response()])
    async with client_factory(fake) as client:
        slug = await _create_project(client, system_prompt=PROMPT_WITH_SLOT, tools=TOOLS_JSON_STR)
        generated = await client.post(f"/api/projects/{slug}/surfaces")
        assert generated.status_code == 200

        res = await client.get(f"/api/projects/{slug}/surfaces")

    assert res.status_code == 200, res.text
    got = res.json()["surfaces"]
    generated_surfaces = generated.json()["surfaces"]
    assert len(got) == len(generated_surfaces)
    assert {s["id"] for s in got} == {s["id"] for s in generated_surfaces}


async def test_surfaces_endpoints_for_an_unknown_slug_are_404(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(FakeCompletions()) as client:
        get_res = await client.get("/api/projects/does-not-exist/surfaces")
        patch_res = await client.patch(
            "/api/projects/does-not-exist/surfaces/1", json={"confirmed": True}
        )
    assert get_res.status_code == 404
    assert patch_res.status_code == 404


async def test_patch_surface_toggles_user_controlled_and_confirmed_and_persists(
    client_factory: ClientFactory,
) -> None:
    fake = FakeCompletions(responses=[_extraction_response()])
    async with client_factory(fake) as client:
        slug = await _create_project(client, system_prompt=PROMPT_WITH_SLOT, tools=TOOLS_JSON_STR)
        generated = (await client.post(f"/api/projects/{slug}/surfaces")).json()["surfaces"]
        target = next(s for s in generated if s["path"] == "search_docs.limit")
        assert target["userControlled"] is True
        assert target["confirmed"] is False

        patched = await client.patch(
            f"/api/projects/{slug}/surfaces/{target['id']}",
            json={"user_controlled": False, "confirmed": True},
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
        assert body["userControlled"] is False
        assert body["confirmed"] is True

        reget = await client.get(f"/api/projects/{slug}/surfaces")

    row = next(s for s in reget.json()["surfaces"] if s["id"] == target["id"])
    assert row["userControlled"] is False
    assert row["confirmed"] is True


async def test_patch_surface_confirms_and_edits_the_tests_count(
    client_factory: ClientFactory,
) -> None:
    fake = FakeCompletions(responses=[_extraction_response()])
    async with client_factory(fake) as client:
        slug = await _create_project(client, system_prompt=PROMPT_WITH_SLOT, tools=TOOLS_JSON_STR)
        generated = (await client.post(f"/api/projects/{slug}/surfaces")).json()["surfaces"]
        target = generated[0]

        patched = await client.patch(
            f"/api/projects/{slug}/surfaces/{target['id']}",
            json={"confirmed": True, "tests": 5},
        )

    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["confirmed"] is True
    assert body["tests"] == 5


async def test_patch_surface_for_an_unknown_id_is_404(client_factory: ClientFactory) -> None:
    fake = FakeCompletions(responses=[_extraction_response()])
    async with client_factory(fake) as client:
        slug = await _create_project(client, system_prompt=PROMPT_WITH_SLOT)
        res = await client.patch(f"/api/projects/{slug}/surfaces/999999", json={"confirmed": True})
    assert res.status_code == 404


async def test_patch_surface_with_no_fields_is_400(client_factory: ClientFactory) -> None:
    fake = FakeCompletions(responses=[_extraction_response()])
    async with client_factory(fake) as client:
        slug = await _create_project(client, system_prompt=PROMPT_WITH_SLOT)
        generated = (await client.post(f"/api/projects/{slug}/surfaces")).json()["surfaces"]
        target = generated[0]

        res = await client.patch(f"/api/projects/{slug}/surfaces/{target['id']}", json={})

    assert res.status_code == 400
