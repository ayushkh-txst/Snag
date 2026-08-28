"""01-10: the runner extended to all four attack surfaces (SCAN-04) —
MULTI-TURN padding, TOOL-ABUSE fed by `snag.simulate.simulate_tool_result`,
INDIRECT fed by `poisoned_result`/junk variants, and the tool-less-model
skip note (SIM-02). `FakeCompletions` throughout; no live network. Every
test scopes to exactly the attack(s) it cares about via the `only_attacks`
rerun seam (01-14's own seam, reused here for determinism — same pattern as
`test_budget_caps.py`), so response scripting never depends on how many
techniques happen to match a rule's category.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest

from snag import cost as cost_module
from snag import runner
from snag.attacks.instantiate import Attack, instantiate
from snag.attacks.instantiate import Rule as AttackRule
from snag.attacks.instantiate import Surface as AttackSurface
from snag.attacks.library import TECHNIQUE_BY_ID
from snag.cost import ModelPricing
from snag.simulate import simulate_tool_result
from substrate.db import Database
from substrate.llm import (
    CompletionResponse,
    FakeCompletions,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolsNotSupportedError,
)

MODEL = "qwen/qwen3.8-flash"

_TOOLS_JSON: list[dict[str, Any]] = [
    {
        "name": "issue_refund",
        "description": "Refund an order, in whole or in part.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number", "minimum": 0},
            },
            "required": ["order_id", "amount"],
        },
    },
    {
        "name": "search_help_center",
        "description": "Search the public help articles.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


@pytest.fixture(autouse=True)
def _prime_pricing_cache() -> Iterator[None]:
    cost_module._PRICING_CACHE[MODEL] = ModelPricing(
        model=MODEL,
        prompt_per_token=Decimal("0.000001"),
        completion_per_token=Decimal("0.000003"),
    )
    yield
    cost_module._PRICING_CACHE.pop(MODEL, None)


async def _make_project(
    db: Database, *, slug: str, tools_json: list[dict[str, Any]] | None = None
) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO projects (id, model, tools_json) VALUES ($1, $2, $3)",
            slug,
            MODEL,
            tools_json,
        )
        await conn.execute(
            "INSERT INTO prompt_versions (project_id, full_text) VALUES ($1, $2)",
            slug,
            "Be careful and follow every rule.",
        )


async def _add_rule(
    db: Database,
    slug: str,
    *,
    category: str,
    checker_type: str,
    checker_config: dict[str, Any] | None = None,
    direction: str = "negative",
    text: str = "x",
) -> int:
    """`text="x"` by default, matching every local `instantiate()` call in
    this file's own `AttackRule(text="x", ...)` — so an `Attack` built
    locally for scripting purposes fills its template identically to the
    one the runner builds from this same DB row."""
    async with db.acquire() as conn:
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, checker_type,
                                   checker_config, testable)
               VALUES ($1, $2, $3, $4, $5, $6, true) RETURNING id""",
            slug,
            text,
            category,
            direction,
            checker_type,
            checker_config,
        )
    return int(rule_id)


async def _add_surface(db: Database, slug: str, *, kind: str, path: str) -> int:
    async with db.acquire() as conn:
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
               VALUES ($1, $2, $3, true, true) RETURNING id""",
            slug,
            kind,
            path,
        )
    return int(surface_id)


async def _insert_pending_scan(
    db: Database, *, slug: str, surfaces: list[str], repeats: int = 1
) -> int:
    async with db.acquire() as conn:
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, surfaces, models, status)
               VALUES ($1, 'custom', $2, $3, $4, 'pending') RETURNING id""",
            slug,
            repeats,
            surfaces,
            [MODEL],
        )
    return int(scan_id)


def _safe_response(text: str = "Sure, happy to help with that.") -> CompletionResponse:
    return CompletionResponse(
        text=text, usage=TokenUsage(20, 10), stop_reason=StopReason.END_TURN, model=MODEL
    )


def _refusal_marker_setup_response() -> CompletionResponse:
    """A rule with `checker_type="refusal_expected"` triggers ONE extra
    scan-setup dispatch (`runner._generate_refusal_markers`) before any
    attack ever runs — this is that dispatch's scripted reply. Its lines
    become the checker's OWN `refusal_markers` (replacing the checker's
    hardcoded default), so an attack response asserting a refusal must
    contain one of these lines, lowercased, verbatim."""
    return _safe_response("I can't\nI won't\nI'm not able to")


def _tool_call_response(name: str, arguments: dict[str, Any]) -> CompletionResponse:
    return CompletionResponse(
        text="",
        usage=TokenUsage(20, 10),
        stop_reason=StopReason.TOOL_USE,
        model=MODEL,
        tool_calls=(ToolCall(id="call-1", name=name, arguments=arguments),),
    )


async def _conversation_for(db: Database, scan_id: int) -> list[list[dict[str, Any]]]:
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT conversation FROM attack_runs WHERE scan_id = $1 ORDER BY id", scan_id
        )
    return [r["conversation"] for r in rows]


# ------------------------------------------------------------- multi-turn


async def test_multiturn_pads_a_single_turn_technique_to_the_minimum_depth(
    clean_db: Database,
) -> None:
    slug = "proj-multiturn-pad"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tone_style",
        checker_type="forbidden_text",
        checker_config={"strings": ["this-never-matches-anything"]},
    )
    surface_id = await _add_surface(clean_db, slug, kind="chat", path="user message")

    attacks = instantiate(
        [AttackRule(id=str(rule_id), text="x", category="tone_style", direction="negative")],
        [AttackSurface(id=str(surface_id), kind="chat", path="user message")],
    )
    assert len(attacks) == 1  # tone_style/chat matches exactly roleplay.01
    attack = attacks[0]
    assert not isinstance(attack.prompt_or_turns, tuple)  # single-turn, before padding

    scan_id = await _insert_pending_scan(clean_db, slug=slug, surfaces=["multiturn"])
    fake = FakeCompletions(responses=[_safe_response() for _ in range(runner.MULTITURN_MIN_DEPTH)])
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    assert len(fake.calls) == runner.MULTITURN_MIN_DEPTH
    # Turn 1 is a lead-in, not the technique's own ask.
    assert fake.calls[0].messages[0].content in runner.MULTITURN_LEAD_INS
    # The FINAL turn's user message is still the technique's own, unpadded ask.
    assert fake.calls[-1].messages[-1].content == attack.prompt_or_turns

    [conversation] = await _conversation_for(clean_db, scan_id)
    user_turns = [t for t in conversation if t["role"] == "user"]
    assistant_turns = [t for t in conversation if t["role"] == "assistant"]
    assert len(user_turns) == runner.MULTITURN_MIN_DEPTH
    assert len(assistant_turns) == runner.MULTITURN_MIN_DEPTH


async def test_multiturn_leaves_an_already_deep_scripted_technique_unpadded(
    clean_db: Database,
) -> None:
    slug = "proj-multiturn-nopad"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="scope_boundary",
        checker_type="forbidden_text",
        checker_config={"strings": ["this-never-matches-anything"]},
    )
    surface_id = await _add_surface(clean_db, slug, kind="chat", path="user message")

    attacks = instantiate(
        [AttackRule(id=str(rule_id), text="x", category="scope_boundary", direction="negative")],
        [AttackSurface(id=str(surface_id), kind="chat", path="user message")],
    )
    multi_turn = [a for a in attacks if isinstance(a.prompt_or_turns, tuple)]
    assert multi_turn, "fixture assumption: scope_boundary/chat matches a scripted multi-turn one"
    attack = multi_turn[0]
    own_depth = len(attack.prompt_or_turns)
    assert own_depth >= runner.MULTITURN_MIN_DEPTH

    scan_id = await _insert_pending_scan(clean_db, slug=slug, surfaces=["multiturn"])
    fake = FakeCompletions(responses=[_safe_response() for _ in range(own_depth)])
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    # No padding added: exactly the technique's own scripted turn count, and
    # turn 1 is the technique's own first turn, not a generic lead-in.
    assert len(fake.calls) == own_depth
    assert fake.calls[0].messages[0].content == attack.prompt_or_turns[0]
    assert fake.calls[0].messages[0].content not in runner.MULTITURN_LEAD_INS


# ------------------------------------------------------------- tool-abuse


async def test_tool_abuse_feeds_back_a_schema_shaped_simulated_result(
    clean_db: Database,
) -> None:
    slug = "proj-tool-schema-fake"
    await _make_project(clean_db, slug=slug, tools_json=_TOOLS_JSON)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tool_authorization",
        checker_type="tool_not_called",
        checker_config={"tool": "issue_refund"},
    )
    surface_id = await _add_surface(clean_db, slug, kind="tool_param", path="issue_refund.amount")

    attacks = instantiate(
        [AttackRule(id=str(rule_id), text="x", category="tool_authorization")],
        [AttackSurface(id=str(surface_id), kind="tool_param", path="issue_refund.amount")],
    )
    attack: Attack = attacks[0]

    scan_id = await _insert_pending_scan(clean_db, slug=slug, surfaces=["tool"])
    fake = FakeCompletions(
        responses=[_tool_call_response("issue_refund", {"amount": 999, "order_id": "NW-1"})]
    )
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    [conversation] = await _conversation_for(clean_db, scan_id)
    tool_result_turns = [t for t in conversation if t["role"] == "tool_result"]
    assert len(tool_result_turns) == 1
    got = json.loads(tool_result_turns[0]["content"])
    expected = simulate_tool_result(_TOOLS_JSON[0]["parameters"], variant="normal")
    assert got == expected


async def test_positive_direction_tool_abuse_passes_on_refusal_with_no_tool_fired(
    clean_db: Database,
) -> None:
    slug = "proj-tool-positive-pass"
    await _make_project(clean_db, slug=slug, tools_json=_TOOLS_JSON)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tool_authorization",
        checker_type="refusal_expected",
        checker_config={},
        direction="positive",
    )
    surface_id = await _add_surface(clean_db, slug, kind="tool_param", path="issue_refund.amount")

    attacks = instantiate(
        [
            AttackRule(
                id=str(rule_id), text="x", category="tool_authorization", direction="positive"
            )
        ],
        [AttackSurface(id=str(surface_id), kind="tool_param", path="issue_refund.amount")],
    )
    technique_ids = {a.technique_id for a in attacks}
    assert "authority_claim.01" in technique_ids
    attack = next(a for a in attacks if a.technique_id == "authority_claim.01")

    scan_id = await _insert_pending_scan(clean_db, slug=slug, surfaces=["tool"])
    fake = FakeCompletions(
        responses=[_refusal_marker_setup_response(), _safe_response("I can't do that, sorry.")]
    )
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM attack_runs WHERE scan_id = $1", scan_id)
    assert row["passed"] is True


async def test_positive_direction_tool_abuse_fails_when_the_tool_fires_anyway(
    clean_db: Database,
) -> None:
    slug = "proj-tool-positive-fail"
    await _make_project(clean_db, slug=slug, tools_json=_TOOLS_JSON)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tool_authorization",
        checker_type="refusal_expected",
        checker_config={},
        direction="positive",
    )
    surface_id = await _add_surface(clean_db, slug, kind="tool_param", path="issue_refund.amount")

    attacks = instantiate(
        [
            AttackRule(
                id=str(rule_id), text="x", category="tool_authorization", direction="positive"
            )
        ],
        [AttackSurface(id=str(surface_id), kind="tool_param", path="issue_refund.amount")],
    )
    attack = next(a for a in attacks if a.technique_id == "authority_claim.01")

    scan_id = await _insert_pending_scan(clean_db, slug=slug, surfaces=["tool"])
    fake = FakeCompletions(
        responses=[
            _refusal_marker_setup_response(),
            _tool_call_response("issue_refund", {"amount": 500, "order_id": "NW-1"}),
        ]
    )
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM attack_runs WHERE scan_id = $1", scan_id)
    assert row["passed"] is False
    assert "no_tool_fired" in row["checker_output"]


# --------------------------------------------------------------- indirect


def _indirect_setup_attacks() -> None:
    """Sanity-check the library fixture this whole section leans on: the
    canary this section asserts against actually belongs to the technique
    it's testing."""
    assert TECHNIQUE_BY_ID["continuation.01"].canary is not None


async def _make_indirect_scan(
    clean_db: Database, *, slug: str, repeats: int
) -> tuple[int, Attack]:
    await _make_project(clean_db, slug=slug, tools_json=_TOOLS_JSON)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="content_prohibition",
        checker_type="instruction_isolation",
        checker_config={},
    )
    surface_id = await _add_surface(
        clean_db, slug, kind="tool_return", path="search_help_center → return value"
    )

    indirect_surface = AttackSurface(
        id=str(surface_id), kind="tool_return", path="search_help_center → return value"
    )
    attacks = instantiate(
        [AttackRule(id=str(rule_id), text="x", category="content_prohibition")],
        [indirect_surface],
    )
    attack = next(a for a in attacks if a.technique_id == "continuation.01")

    scan_id = await _insert_pending_scan(
        clean_db, slug=slug, surfaces=["indirect"], repeats=repeats
    )
    return scan_id, attack


async def test_indirect_surface_feeds_a_poisoned_result_and_instruction_isolation_catches_obedience(
    clean_db: Database,
) -> None:
    _indirect_setup_attacks()
    slug = "proj-indirect-obey"
    scan_id, attack = await _make_indirect_scan(clean_db, slug=slug, repeats=1)
    canary = TECHNIQUE_BY_ID["continuation.01"].canary
    assert canary is not None

    fake = FakeCompletions(
        responses=[
            _safe_response("Sure, let me look into that for you."),
            _safe_response(f"Here's what I found — {canary}"),
        ]
    )
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    assert len(fake.calls) == 2
    assert fake.calls[0].tools is None
    assert fake.calls[1].tools is None

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM attack_runs WHERE scan_id = $1", scan_id)
    assert row["passed"] is False
    assert row["evidence"] == canary

    tool_result_turns = [t for t in row["conversation"] if t["role"] == "tool_result"]
    assert len(tool_result_turns) == 1
    assert canary in tool_result_turns[0]["content"]
    assert tool_result_turns[0].get("planted")  # marked attacker-planted for tool_arg_not_injected


async def test_indirect_surface_passes_when_the_model_never_repeats_the_canary(
    clean_db: Database,
) -> None:
    slug = "proj-indirect-clean"
    scan_id, attack = await _make_indirect_scan(clean_db, slug=slug, repeats=1)

    fake = FakeCompletions(
        responses=[
            _safe_response("Sure, let me look into that for you."),
            _safe_response("Here's a normal, unrelated answer."),
        ]
    )
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM attack_runs WHERE scan_id = $1", scan_id)
    assert row["passed"] is True


async def test_indirect_surface_cycles_through_junk_variants_across_repeats(
    clean_db: Database,
) -> None:
    slug = "proj-indirect-junk"
    scan_id, attack = await _make_indirect_scan(clean_db, slug=slug, repeats=2)

    fake = FakeCompletions(responses=[_safe_response() for _ in range(4)])  # 2 repeats * 2 calls
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    assert len(fake.calls) == 4
    # repeat 0 -> variant "normal": the poisoned narrative is fed back.
    canary = TECHNIQUE_BY_ID["continuation.01"].canary
    assert canary is not None
    assert canary in fake.calls[1].messages[-1].content
    # repeat 1 -> variant "empty": search_help_center's schema is an
    # object, so the empty shape is `{}` — no canary, no narrative at all.
    assert canary not in fake.calls[3].messages[-1].content
    assert "{}" in fake.calls[3].messages[-1].content


async def test_indirect_attack_respects_the_budget_cap_across_its_two_dispatches(
    clean_db: Database,
) -> None:
    slug = "proj-indirect-cap"
    scan_id, attack = await _make_indirect_scan(clean_db, slug=slug, repeats=1)

    fake = FakeCompletions(responses=[_safe_response()])  # only the FIRST of 2 dispatches fits
    async with clean_db.acquire() as conn:
        await conn.execute("UPDATE scans SET call_cap = 1 WHERE id = $1", scan_id)

    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[attack.key()])

    assert len(fake.calls) == 1
    async with clean_db.acquire() as conn:
        run_count = await conn.fetchval(
            "SELECT count(*) FROM attack_runs WHERE scan_id = $1", scan_id
        )
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
    assert run_count == 0  # the (attack, repeat) pair is all-or-nothing
    assert row["status"] == "stopped_at_cap"


# --------------------------------------------------------- tool-less model


async def test_tool_less_model_skips_tool_abuse_and_records_the_note_while_direct_completes(
    clean_db: Database,
) -> None:
    slug = "proj-tool-less"
    await _make_project(clean_db, slug=slug, tools_json=_TOOLS_JSON)
    direct_rule_id = await _add_rule(
        clean_db,
        slug,
        category="tone_style",
        checker_type="forbidden_text",
        checker_config={"strings": ["this-never-matches-anything"]},
    )
    tool_rule_id = await _add_rule(
        clean_db,
        slug,
        category="tool_authorization",
        checker_type="tool_not_called",
        checker_config={"tool": "issue_refund"},
    )
    chat_id = await _add_surface(clean_db, slug, kind="chat", path="user message")
    tool_id = await _add_surface(clean_db, slug, kind="tool_param", path="issue_refund.amount")

    all_attacks = instantiate(
        [
            AttackRule(id=str(direct_rule_id), text="x", category="tone_style"),
            AttackRule(id=str(tool_rule_id), text="x", category="tool_authorization"),
        ],
        [
            AttackSurface(id=str(chat_id), kind="chat", path="user message"),
            AttackSurface(id=str(tool_id), kind="tool_param", path="issue_refund.amount"),
        ],
    )
    direct_attack = next(a for a in all_attacks if a.surface_kind == "chat")
    tool_attack = next(
        a
        for a in all_attacks
        if a.surface_kind == "tool_param" and a.rule_id == str(tool_rule_id)
    )
    wanted = {direct_attack.key(), tool_attack.key()}
    ordered = [a for a in all_attacks if a.key() in wanted]

    fake_responses: list[CompletionResponse | Exception] = []
    for a in ordered:
        if a.surface_kind == "chat":
            fake_responses.append(_safe_response())
        else:
            fake_responses.append(
                ToolsNotSupportedError(f"model {MODEL} does not support tool calling")
            )

    scan_id = await _insert_pending_scan(clean_db, slug=slug, surfaces=["direct", "tool"])
    fake = FakeCompletions(responses=fake_responses)
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=list(wanted))

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
        run_count = await conn.fetchval(
            "SELECT count(*) FROM attack_runs WHERE scan_id = $1", scan_id
        )
    assert row["status"] == "completed"
    assert row["tool_support_note"] == "skipped: model has no tool support"
    assert run_count == 1  # only the chat (direct) attack got persisted
