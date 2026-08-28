"""snag.runner: the real scan runner (01-09) — substrate.queue-backed,
budget-guarded, full-transcript persistence (SCAN-01, SCAN-02, BREAK-01,
PRIV-03). `FakeCompletions` throughout; no live network — the runner's own
pre-dispatch cost estimate is primed via `snag.cost._PRICING_CACHE` rather
than a live OpenRouter catalogue fetch (see `snag.cost.fetch_model_pricing`).

Task 1 (enqueue/estimate/endpoint) and Task 2 (handler/persist/repeats/
direct/tool) coverage lives in this one file per the plan's own `<verify>`
`-k` filters; Task 3 (budget caps) has its own file, `test_budget_caps.py`.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Any

import httpx
import pytest

from snag import cost as cost_module
from snag import runner
from snag.attacks.instantiate import Attack, instantiate
from snag.attacks.instantiate import Rule as AttackRule
from snag.attacks.instantiate import Surface as AttackSurface
from snag.attacks.library import TECHNIQUES, RuleCategory, SurfaceKind
from snag.cost import ModelPricing
from snag.gaps import GAP_CHECKLIST
from substrate.db import Database
from substrate.llm import CompletionResponse, Completions, FakeCompletions, StopReason, TokenUsage
from substrate.queue import Worker

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]
DrainScanQueue = Callable[[Database, Completions], Awaitable[Worker]]

MODEL = "qwen/qwen3.8-flash"
_KNOWN_TECHNIQUE_IDS = {t.id for t in TECHNIQUES}


@pytest.fixture(autouse=True)
def _prime_pricing_cache() -> Iterator[None]:
    """`run_scan` makes exactly ONE pre-dispatch cost estimate
    (`runner._projected_per_call_cost`) before its loop starts — priming
    the process-level cache here means that estimate never touches the
    real network."""
    cost_module._PRICING_CACHE[MODEL] = ModelPricing(
        model=MODEL,
        prompt_per_token=Decimal("0.000001"),
        completion_per_token=Decimal("0.000003"),
    )
    yield
    cost_module._PRICING_CACHE.pop(MODEL, None)


async def _make_project(
    db: Database, *, slug: str, system_prompt: str = "Be nice. Never do X."
) -> None:
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, MODEL)
        await conn.execute(
            "INSERT INTO prompt_versions (project_id, full_text) VALUES ($1, $2)",
            slug,
            system_prompt,
        )


async def _add_rule(
    db: Database,
    slug: str,
    *,
    category: str,
    checker_type: str,
    checker_config: dict[str, Any] | None = None,
    direction: str = "negative",
) -> int:
    async with db.acquire() as conn:
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, checker_type,
                                   checker_config, testable)
               VALUES ($1, $2, $3, $4, $5, $6, true) RETURNING id""",
            slug,
            f"a rule about {category}",
            category,
            direction,
            checker_type,
            checker_config,
        )
    return int(rule_id)


async def _add_surface(
    db: Database,
    slug: str,
    *,
    kind: str,
    path: str,
    confirmed: bool = True,
    user_controlled: bool = True,
) -> int:
    async with db.acquire() as conn:
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            slug,
            kind,
            path,
            confirmed,
            user_controlled,
        )
    return int(surface_id)


def _safe_response(text: str = "Sure, happy to help with that.") -> CompletionResponse:
    return CompletionResponse(
        text=text, usage=TokenUsage(20, 10), stop_reason=StopReason.END_TURN, model=MODEL
    )


def _dispatch_count(attacks: list[Attack], repeats: int) -> int:
    return (
        sum(len(a.prompt_or_turns) if isinstance(a.prompt_or_turns, tuple) else 1 for a in attacks)
        * repeats
    )


def _attack_rule(rule_id: int, category: RuleCategory) -> AttackRule:
    return AttackRule(id=str(rule_id), text="x", category=category, direction="negative")


def _attack_surface(surface_id: int, *, kind: SurfaceKind, path: str) -> AttackSurface:
    return AttackSurface(id=str(surface_id), kind=kind, path=path)


# --------------------------------------------------------------------- Task 1
# (enqueue / estimate / endpoint)


async def test_post_scans_enqueues_exactly_one_scan_and_one_job(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-enqueue"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        res = await client.post("/api/scans", json={"slug": slug, "mode": "quick"})
        assert res.status_code == 200, res.text
        scan_id = res.json()["scan_id"]

    async with clean_db.acquire() as conn:
        scans_count = await conn.fetchval(
            "SELECT count(*) FROM scans WHERE project_id = $1", slug
        )
        jobs = await conn.fetch(
            "SELECT * FROM jobs WHERE kind = 'scan' AND payload ->> 'scan_id' = $1",
            str(scan_id),
        )
    assert scans_count == 1
    assert len(jobs) == 1
    assert jobs[0]["queue"] == "scan"
    assert jobs[0]["idempotency_key"] == str(scan_id)


async def test_scan_endpoint_applies_documented_mode_presets_and_ignores_client_overrides(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-modes"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        for mode, (preset_surfaces, preset_repeats) in runner.MODE_PRESETS.items():
            res = await client.post(
                "/api/scans",
                json={
                    "slug": slug,
                    "mode": mode,
                    # A client can't smuggle a bigger shape past a preset
                    # mode's documented, cheap surfaces+repeats.
                    "surfaces": ["direct", "tool", "multiturn", "indirect"],
                    "repeats": 9,
                },
            )
            assert res.status_code == 200, res.text
            scan_id = res.json()["scan_id"]
            got = await client.get(f"/api/scans/{scan_id}")
            assert got.json()["surfaces"] == preset_surfaces
            assert got.json()["repeats"] == preset_repeats


async def test_custom_mode_endpoint_rejects_repeats_outside_the_1_to_10_bound(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-repeats-bound"
    await _make_project(clean_db, slug=slug)
    body = {"slug": slug, "mode": "custom", "surfaces": ["direct"]}
    async with client_factory(FakeCompletions()) as client:
        too_many = await client.post("/api/scans", json={**body, "repeats": 11})
        too_few = await client.post("/api/scans", json={**body, "repeats": 0})
    assert too_many.status_code == 422
    assert too_few.status_code == 422


async def test_custom_mode_endpoint_requires_surfaces_and_rejects_unknown_categories(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-custom-validation"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        missing_surfaces = await client.post(
            "/api/scans", json={"slug": slug, "mode": "custom", "repeats": 3}
        )
        unknown_category = await client.post(
            "/api/scans",
            json={"slug": slug, "mode": "custom", "surfaces": ["telepathy"], "repeats": 3},
        )
    assert missing_surfaces.status_code == 422
    assert unknown_category.status_code == 400


async def test_get_scan_endpoint_flags_repeats_1_as_indicative_only(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-indicative"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        res = await client.post("/api/scans", json={"slug": slug, "mode": "quick"})
        scan_id = res.json()["scan_id"]
        got = await client.get(f"/api/scans/{scan_id}")
    assert got.status_code == 200
    assert got.json()["repeats"] == 1
    assert got.json()["indicative_only"] is True


async def test_get_scan_endpoint_404s_for_an_unknown_scan(client_factory: ClientFactory) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.get("/api/scans/999999")
    assert res.status_code == 404


async def test_scans_estimate_endpoint_returns_a_decimal_cost_before_dispatch(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-estimate"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        res = await client.post("/api/scans/estimate", json={"slug": slug, "mode": "standard"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert Decimal(str(body["estimated_cost_usd"])) > 0
    assert body["estimated_calls"] > 0
    assert body["unknown_pricing"] is False


async def test_worker_registers_kind_scan_and_drains_an_enqueued_job_end_to_end(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    fake = FakeCompletions()
    slug = "proj-drain"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tone_style",
        checker_type="forbidden_text",
        checker_config={"strings": ["nope-never-matches-xyz"]},
    )
    chat_id = await _add_surface(clean_db, slug, kind="chat", path="user message")

    expected = instantiate(
        [_attack_rule(rule_id, "tone_style")],
        [_attack_surface(chat_id, kind="chat", path="user message")],
    )
    fake.responses.extend(
        _safe_response() for _ in range(_dispatch_count(expected, repeats=1) + len(GAP_CHECKLIST))
    )

    async with client_factory(fake) as client:
        res = await client.post("/api/scans", json={"slug": slug, "mode": "quick"})
        assert res.status_code == 200, res.text
        scan_id = res.json()["scan_id"]

        worker = await drain_scan_queue(clean_db, fake)
        assert worker.processed == 1
        assert worker.failed == 0

        got = await client.get(f"/api/scans/{scan_id}")
    assert got.json()["status"] == "completed"
    assert got.json()["attacks_done"] == len(expected)


# --------------------------------------------------------------------- Task 2
# (handler / persist / repeats / direct / tool)


async def test_scan_handler_persists_attack_runs_across_rules_surfaces_and_repeats(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    fake = FakeCompletions()
    slug = "proj-matrix"
    await _make_project(clean_db, slug=slug)
    rule1_id = await _add_rule(
        clean_db,
        slug,
        category="tone_style",
        checker_type="forbidden_text",
        checker_config={"strings": ["nope-never-matches-xyz"]},
    )
    rule2_id = await _add_rule(
        clean_db,
        slug,
        category="tool_authorization",
        checker_type="tool_not_called",
        checker_config={"tool": "issue_refund"},
    )
    chat_id = await _add_surface(clean_db, slug, kind="chat", path="user message")
    tool_id = await _add_surface(clean_db, slug, kind="tool_param", path="issue_refund.amount")

    expected = instantiate(
        [
            _attack_rule(rule1_id, "tone_style"),
            AttackRule(
                id=str(rule2_id), text="x", category="tool_authorization", direction="negative"
            ),
        ],
        [
            _attack_surface(chat_id, kind="chat", path="user message"),
            _attack_surface(tool_id, kind="tool_param", path="issue_refund.amount"),
        ],
    )
    assert len(expected) >= 2  # a real matrix, not a single coincidental match
    repeats = 2
    fake.responses.extend(
        _safe_response() for _ in range(_dispatch_count(expected, repeats) + len(GAP_CHECKLIST))
    )

    scan_body = {"slug": slug, "mode": "custom", "surfaces": ["direct", "tool"], "repeats": repeats}
    async with client_factory(fake) as client:
        res = await client.post("/api/scans", json=scan_body)
        assert res.status_code == 200, res.text
        scan_id = res.json()["scan_id"]
        await drain_scan_queue(clean_db, fake)
        got = await client.get(f"/api/scans/{scan_id}")

    assert got.json()["status"] == "completed"
    assert got.json()["attacks_done"] == len(expected) * repeats

    async with clean_db.acquire() as conn:
        runs = await conn.fetch("SELECT * FROM attack_runs WHERE scan_id = $1", scan_id)
    assert len(runs) == len(expected) * repeats
    for run in runs:
        assert len(run["conversation"]) >= 2  # at least one user + one assistant turn
        assert run["checker_output"]
        assert run["false_positive"] is False
        assert run["passed"] in (True, False)


async def test_direct_and_tool_abuse_surfaces_are_both_exercised(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    fake = FakeCompletions()
    slug = "proj-direct-and-tool"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tool_authorization",
        checker_type="tool_not_called",
        checker_config={"tool": "issue_refund"},
    )
    chat_id = await _add_surface(clean_db, slug, kind="chat", path="user message")
    tool_id = await _add_surface(clean_db, slug, kind="tool_param", path="issue_refund.amount")

    expected = instantiate(
        [_attack_rule(rule_id, "tool_authorization")],
        [
            _attack_surface(chat_id, kind="chat", path="user message"),
            _attack_surface(tool_id, kind="tool_param", path="issue_refund.amount"),
        ],
    )
    fake.responses.extend(
        _safe_response() for _ in range(_dispatch_count(expected, repeats=1) + len(GAP_CHECKLIST))
    )

    async with client_factory(fake) as client:
        res = await client.post(
            "/api/scans",
            json={"slug": slug, "mode": "custom", "surfaces": ["direct", "tool"], "repeats": 1},
        )
        scan_id = res.json()["scan_id"]
        await drain_scan_queue(clean_db, fake)

    async with clean_db.acquire() as conn:
        surface_ids = {
            r["surface_id"]
            for r in await conn.fetch(
                "SELECT DISTINCT surface_id FROM attack_runs WHERE scan_id = $1", scan_id
            )
        }
    assert surface_ids == {chat_id, tool_id}


async def test_scan_handler_persists_a_refusal_as_a_normal_run_not_an_error(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    fake = FakeCompletions()
    slug = "proj-refusal"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tone_style",
        checker_type="forbidden_text",
        checker_config={"strings": ["nope-never-matches-xyz"]},
    )
    chat_id = await _add_surface(clean_db, slug, kind="chat", path="user message")

    expected = instantiate(
        [_attack_rule(rule_id, "tone_style")],
        [_attack_surface(chat_id, kind="chat", path="user message")],
    )
    fake.responses.extend(
        CompletionResponse(
            text="", usage=TokenUsage(5, 0), stop_reason=StopReason.REFUSAL, model=MODEL
        )
        for _ in range(_dispatch_count(expected, repeats=1) + len(GAP_CHECKLIST))
    )

    async with client_factory(fake) as client:
        res = await client.post("/api/scans", json={"slug": slug, "mode": "quick"})
        scan_id = res.json()["scan_id"]
        worker = await drain_scan_queue(clean_db, fake)
        got = await client.get(f"/api/scans/{scan_id}")

    assert worker.failed == 0
    assert got.json()["status"] == "completed"
    async with clean_db.acquire() as conn:
        runs = await conn.fetch("SELECT * FROM attack_runs WHERE scan_id = $1", scan_id)
    assert len(runs) == len(expected)
    assert all(r["passed"] is True for r in runs)  # empty text never contains the forbidden string


async def test_technique_stats_persist_counts_only_and_never_prompt_text(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    fake = FakeCompletions()
    slug = "proj-stats"
    await _make_project(clean_db, slug=slug, system_prompt="Never say the secret word BANANA123.")
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tone_style",
        checker_type="forbidden_text",
        checker_config={"strings": ["BANANA123"]},
    )
    chat_id = await _add_surface(clean_db, slug, kind="chat", path="user message")

    expected = instantiate(
        [_attack_rule(rule_id, "tone_style")],
        [_attack_surface(chat_id, kind="chat", path="user message")],
    )
    repeats = 3
    fake.responses.extend(
        _safe_response() for _ in range(_dispatch_count(expected, repeats) + len(GAP_CHECKLIST))
    )

    scan_body = {"slug": slug, "mode": "custom", "surfaces": ["direct"], "repeats": repeats}
    async with client_factory(fake) as client:
        res = await client.post("/api/scans", json=scan_body)
        assert res.status_code == 200, res.text
        await drain_scan_queue(clean_db, fake)

    async with clean_db.acquire() as conn:
        stats_rows = await conn.fetch("SELECT * FROM technique_stats")
    assert stats_rows
    assert sum(r["attempts"] for r in stats_rows) == len(expected) * repeats
    for row in stats_rows:
        assert row["technique_id"] in _KNOWN_TECHNIQUE_IDS
        assert row["rule_category"] == "tone_style"
        assert row["surface_kind"] == "chat"
        # PRIV-03: nothing here can even shape-check as prompt text — every
        # column is a fixed, short, categorical identifier.
        assert "BANANA123" not in row["technique_id"]
        assert len(row["technique_id"]) < 64

    # Structural guarantee, not just a lucky fixture: the write path itself
    # never receives a transcript/prompt argument to leak in the first place.
    source = inspect.getsource(runner._record_technique_stats)
    for forbidden in ("transcript", "prompt_or_turns", "conversation", "system_prompt"):
        assert forbidden not in source


async def test_run_scan_handler_only_attacks_seam_runs_just_that_subset(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    fake = FakeCompletions()
    slug = "proj-only-attacks"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(
        clean_db,
        slug,
        category="tool_authorization",
        checker_type="tool_not_called",
        checker_config={"tool": "issue_refund"},
    )
    chat_id = await _add_surface(clean_db, slug, kind="chat", path="user message")

    all_attacks = instantiate(
        [_attack_rule(rule_id, "tool_authorization")],
        [_attack_surface(chat_id, kind="chat", path="user message")],
    )
    assert len(all_attacks) >= 2  # tool_authorization + chat matches several techniques
    target = all_attacks[0]
    # `only_attacks` narrows the ATTACK matrix to just `target` (one dispatch),
    # but the gap-probe pass at the end of `_run_scan` still runs in full and
    # unconditionally for every completed scan (GAP-01) — it has no
    # `only_attacks` seam of its own.
    fake.responses.append(_safe_response())
    fake.responses.extend(_safe_response() for _ in range(len(GAP_CHECKLIST)))

    async with client_factory(fake) as client:
        res = await client.post("/api/scans", json={"slug": slug, "mode": "quick"})
        scan_id = res.json()["scan_id"]

    await runner.run_scan(clean_db, scan_id, completions=fake, only_attacks=[target.key()])

    async with clean_db.acquire() as conn:
        runs = await conn.fetch("SELECT * FROM attack_runs WHERE scan_id = $1", scan_id)
    assert len(runs) == 1
    assert runs[0]["technique_id"] == target.technique_id
