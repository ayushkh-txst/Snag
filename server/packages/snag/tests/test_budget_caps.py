"""SCAN-03: hard call cap and hard spend cap enforced BEFORE every single
dispatch — the scan stops at the cap and records what it didn't run. Every
assertion here is against `len(fake.calls)` (dispatches actually made), not
just against the final DB counters, so a bug that dispatches one call too
many is caught even if the counters happen to still look right.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from decimal import Decimal

import pytest

from snag import cost as cost_module
from snag import runner
from snag.attacks.instantiate import Rule as AttackRule
from snag.attacks.instantiate import Surface as AttackSurface
from snag.attacks.instantiate import instantiate
from snag.cost import ModelPricing
from substrate.db import Database
from substrate.llm import CompletionResponse, FakeCompletions, StopReason, TokenUsage

MODEL = "qwen/qwen3.8-flash"
PER_CALL_COST = Decimal("0.002")  # 800*0.000001 + 400*0.000003, matching _prime_pricing_cache below


@pytest.fixture(autouse=True)
def _prime_pricing_cache() -> Iterator[None]:
    cost_module._PRICING_CACHE[MODEL] = ModelPricing(
        model=MODEL,
        prompt_per_token=Decimal("0.000001"),
        completion_per_token=Decimal("0.000003"),
    )
    yield
    cost_module._PRICING_CACHE.pop(MODEL, None)


async def _make_scannable_project(db: Database, *, slug: str) -> tuple[int, int]:
    """One rule/one surface whose category+kind matches EXACTLY ONE
    single-turn technique (`roleplay.01`, via category="tone_style" on the
    `chat` surface) — so `len(attacks) == 1` and every repeat is exactly one
    dispatch, making the exact call count fully predictable."""
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, MODEL)
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, checker_type,
                                   checker_config, testable)
               VALUES ($1, 'never break character', 'tone_style', 'negative',
                       'forbidden_text', $2, true) RETURNING id""",
            slug,
            {"strings": ["this-never-matches-anything"]},
        )
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
               VALUES ($1, 'chat', 'user message', true, true) RETURNING id""",
            slug,
        )
    attacks = instantiate(
        [AttackRule(id=str(rule_id), text="x", category="tone_style", direction="negative")],
        [AttackSurface(id=str(surface_id), kind="chat", path="user message")],
    )
    assert len(attacks) == 1  # the whole point of this fixture's exact-count guarantee
    assert not isinstance(attacks[0].prompt_or_turns, tuple)  # single-turn
    return int(rule_id), int(surface_id)


async def _insert_pending_scan(
    db: Database, *, slug: str, repeats: int, call_cap: int | None, spend_cap: Decimal | None
) -> int:
    async with db.acquire() as conn:
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, surfaces, models, status,
                                   call_cap, spend_cap)
               VALUES ($1, 'custom', $2, $3, $4, 'pending', $5, $6) RETURNING id""",
            slug,
            repeats,
            ["direct"],
            [MODEL],
            call_cap,
            spend_cap,
        )
    return int(scan_id)


def _safe_response() -> CompletionResponse:
    return CompletionResponse(
        text="Sure, happy to help.",
        usage=TokenUsage(20, 10),
        stop_reason=StopReason.END_TURN,
        model=MODEL,
        cost_usd=PER_CALL_COST,
    )


async def test_hard_call_cap_stops_the_scan_before_exceeding_it_and_records_skipped(
    clean_db: Database,
) -> None:
    slug = "proj-call-cap"
    await _make_scannable_project(clean_db, slug=slug)
    repeats = 5
    call_cap = 2
    scan_id = await _insert_pending_scan(
        clean_db, slug=slug, repeats=repeats, call_cap=call_cap, spend_cap=None
    )

    fake = FakeCompletions(responses=[_safe_response() for _ in range(call_cap)])
    await runner.run_scan(clean_db, scan_id, completions=fake)

    # The cap, not the cap + 1: exactly `call_cap` dispatches were made, and
    # the fake would have raised AssertionError had a 3rd been attempted —
    # it was only ever scripted two responses.
    assert len(fake.calls) == call_cap

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
        run_count = await conn.fetchval(
            "SELECT count(*) FROM attack_runs WHERE scan_id = $1", scan_id
        )
    assert row["status"] == "stopped_at_cap"
    assert row["call_count"] == call_cap
    assert run_count == call_cap
    assert row["attacks_done"] == call_cap
    assert row["skipped_count"] == repeats - call_cap
    assert row["finished_at"] is not None


async def test_hard_spend_cap_stops_the_scan_before_the_dispatch_that_would_exceed_it(
    clean_db: Database,
) -> None:
    slug = "proj-spend-cap"
    await _make_scannable_project(clean_db, slug=slug)
    repeats = 5
    # Two calls at PER_CALL_COST fit ($0.004); a third would not ($0.006 > $0.005).
    spend_cap = Decimal("0.005")
    scan_id = await _insert_pending_scan(
        clean_db, slug=slug, repeats=repeats, call_cap=None, spend_cap=spend_cap
    )

    fake = FakeCompletions(responses=[_safe_response() for _ in range(2)])
    await runner.run_scan(clean_db, scan_id, completions=fake)

    assert len(fake.calls) == 2

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
    assert row["status"] == "stopped_at_cap"
    assert row["skipped_count"] == repeats - 2
    assert Decimal(row["cost"]) == PER_CALL_COST * 2


async def test_a_scan_within_both_caps_completes_normally_with_no_skips(
    clean_db: Database,
) -> None:
    slug = "proj-within-caps"
    await _make_scannable_project(clean_db, slug=slug)
    repeats = 3
    scan_id = await _insert_pending_scan(
        clean_db, slug=slug, repeats=repeats, call_cap=100, spend_cap=Decimal("5.00")
    )

    fake = FakeCompletions(responses=[_safe_response() for _ in range(repeats)])
    await runner.run_scan(clean_db, scan_id, completions=fake)

    assert len(fake.calls) == repeats
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
    assert row["status"] == "completed"
    assert row["skipped_count"] == 0
    assert row["attacks_done"] == repeats


async def test_the_budget_guard_precedes_every_single_dispatch_call_structurally() -> None:
    """Grep-level structural guarantee: `completions.complete(` is called
    from exactly one place in the whole module (`_dispatch`), and that one
    call site is preceded by the cap checks in the SAME function — so the
    guard cannot be bypassed by a new call site added later without also
    touching `_dispatch`."""
    source = inspect.getsource(runner)
    call_sites = source.count(".complete(")
    assert call_sites == 1, "completions.complete( must be called from exactly one place"

    dispatch_source = inspect.getsource(runner._dispatch)
    complete_index = dispatch_source.index(".complete(")
    call_cap_index = dispatch_source.index("call_cap")
    spend_cap_index = dispatch_source.index("spend_cap")
    assert call_cap_index < complete_index
    assert spend_cap_index < complete_index


async def test_budget_exceeded_mid_multi_turn_attack_does_not_persist_a_partial_run(
    clean_db: Database,
) -> None:
    """A scripted multi-turn technique (`context_switch.01`, 3 turns) that
    runs out of call cap on its 2nd of 3 turns must not leave a half-built
    attack_run behind — the whole (attack, repeat) pair is either fully
    dispatched and persisted, or not persisted at all."""
    slug = "proj-multiturn-cap"
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, MODEL)
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, checker_type,
                                   checker_config, testable)
               VALUES ($1, 'never break scope', 'scope_boundary', 'negative',
                       'forbidden_text', $2, true) RETURNING id""",
            slug,
            {"strings": ["this-never-matches-anything"]},
        )
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
               VALUES ($1, 'chat', 'user message', true, true) RETURNING id""",
            slug,
        )

    attacks = instantiate(
        [AttackRule(id=str(rule_id), text="x", category="scope_boundary", direction="negative")],
        [AttackSurface(id=str(surface_id), kind="chat", path="user message")],
    )
    multi_turn = [a for a in attacks if isinstance(a.prompt_or_turns, tuple)]
    assert multi_turn, "fixture assumption: scope_boundary/chat matches a multi-turn technique"
    assert len(multi_turn[0].prompt_or_turns) >= 2

    scan_id = await _insert_pending_scan(clean_db, slug=slug, repeats=1, call_cap=1, spend_cap=None)
    fake = FakeCompletions(responses=[_safe_response()])
    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[multi_turn[0].key()])

    assert len(fake.calls) == 1
    async with clean_db.acquire() as conn:
        run_count = await conn.fetchval(
            "SELECT count(*) FROM attack_runs WHERE scan_id = $1", scan_id
        )
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
    assert run_count == 0
    assert row["status"] == "stopped_at_cap"
    assert row["skipped_count"] == 1
