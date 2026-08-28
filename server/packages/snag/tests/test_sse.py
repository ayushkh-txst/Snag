"""PROGRESS-01: hand-rolled SSE progress streaming for scans (01-11).

Task 1 (`snag.api.sse.scan_event_stream` + `GET /scans/{id}/stream`) and
Task 2 (`snag.api.sse.write_progress`, called from the runner's per-attack
persistence seam) coverage lives in this one file per the plan's own
`<verify>` `-k` filters (`stream or resume`, `progress or write`).

No live network anywhere here — `FakeCompletions` throughout, and the
runner's one pre-dispatch cost estimate is primed via
`snag.cost._PRICING_CACHE`, exactly like `test_runner.py`/`test_budget_caps.py`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Any

import httpx
import pytest

from snag import cost as cost_module
from snag.api.sse import TERMINAL_STATUSES, scan_event_stream
from snag.attacks.instantiate import Rule as AttackRule
from snag.attacks.instantiate import Surface as AttackSurface
from snag.attacks.instantiate import instantiate
from snag.cost import ModelPricing
from snag.gaps import GAP_CHECKLIST
from substrate.db import Database
from substrate.llm import CompletionResponse, Completions, FakeCompletions, StopReason, TokenUsage
from substrate.queue import Worker

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]
DrainScanQueue = Callable[[Database, Completions], Awaitable[Worker]]

MODEL = "qwen/qwen3.8-flash"


@pytest.fixture(autouse=True)
def _prime_pricing_cache() -> Iterator[None]:
    """`run_scan` makes exactly ONE pre-dispatch cost estimate before its
    loop starts — priming the process-level cache here means that estimate
    never touches the real network (mirrors `test_runner.py`)."""
    cost_module._PRICING_CACHE[MODEL] = ModelPricing(
        model=MODEL,
        prompt_per_token=Decimal("0.000001"),
        completion_per_token=Decimal("0.000003"),
    )
    yield
    cost_module._PRICING_CACHE.pop(MODEL, None)


def _safe_response(text: str = "Sure, happy to help with that.") -> CompletionResponse:
    return CompletionResponse(
        text=text, usage=TokenUsage(20, 10), stop_reason=StopReason.END_TURN, model=MODEL
    )


async def _seed_scan(db: Database, *, slug: str, status: str = "pending") -> int:
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, MODEL)
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, status)
                   VALUES ($1, 'quick', 1, $2) RETURNING id""",
            slug,
            status,
        )
    return int(scan_id)


async def _seed_event(
    db: Database, scan_id: int, *, seq: int, kind: str, data: dict[str, Any]
) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO scan_events (scan_id, seq, kind, data) VALUES ($1, $2, $3, $4)",
            scan_id,
            seq,
            kind,
            data,
        )


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """`"event: phase\\ndata: {...}\\n\\n"` frames -> `[("phase", {...}), ...]`."""
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data_line = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        frames.append((event, json.loads(data_line)))
    return frames


# --------------------------------------------------------------------- Task 1
# (scan_event_stream generator + GET /scans/{id}/stream)


async def test_scan_event_stream_generator_emits_seeded_events_then_done(
    clean_db: Database,
) -> None:
    slug = "proj-sse-generator"
    scan_id = await _seed_scan(clean_db, slug=slug, status="completed")
    await _seed_event(
        clean_db,
        scan_id,
        seq=1,
        kind="attack",
        data={"technique_id": "roleplay.01", "broke": False},
    )
    await _seed_event(
        clean_db, scan_id, seq=2, kind="attack", data={"technique_id": "roleplay.02", "broke": True}
    )

    raw = "".join([chunk async for chunk in scan_event_stream(clean_db, scan_id)])
    frames = _parse_sse(raw)

    events = [kind for kind, _ in frames]
    assert events == ["phase", "phase", "done"]
    assert frames[0][1]["seq"] == 1
    assert frames[0][1]["technique_id"] == "roleplay.01"
    assert frames[1][1]["seq"] == 2
    assert frames[1][1]["broke"] is True
    assert frames[2][1]["status"] == "completed"


async def test_scan_event_stream_generator_returns_immediately_for_a_missing_scan(
    clean_db: Database,
) -> None:
    chunks = [chunk async for chunk in scan_event_stream(clean_db, 999999)]
    assert chunks == []


async def test_stream_scan_endpoint_returns_event_stream_with_phase_and_done_frames(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-sse-endpoint"
    scan_id = await _seed_scan(clean_db, slug=slug, status="completed")
    await _seed_event(
        clean_db,
        scan_id,
        seq=1,
        kind="attack",
        data={
            "technique_id": "roleplay.01",
            "rule_id": 1,
            "surface_id": 1,
            "broke": False,
            "attacks_done": 1,
            "cost": "0.002",
        },
    )

    async with client_factory(FakeCompletions()) as client, client.stream(
        "GET", f"/api/scans/{scan_id}/stream"
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        raw = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()

    frames = _parse_sse(raw)
    kinds = [kind for kind, _ in frames]
    assert kinds == ["phase", "done"]
    phase_data = frames[0][1]
    for key in ("technique_id", "rule_id", "surface_id", "broke", "attacks_done", "cost"):
        assert key in phase_data


async def test_stream_scan_endpoint_404s_for_an_unknown_scan(
    client_factory: ClientFactory,
) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.get("/api/scans/999999/stream")
    assert res.status_code == 404


async def test_stream_resume_since_seq_replays_only_events_after_it(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-sse-resume"
    scan_id = await _seed_scan(clean_db, slug=slug, status="completed")
    for seq in (1, 2, 3):
        await _seed_event(
            clean_db, scan_id, seq=seq, kind="attack", data={"technique_id": f"t.{seq:02d}"}
        )

    async with client_factory(FakeCompletions()) as client, client.stream(
        "GET", f"/api/scans/{scan_id}/stream", params={"since_seq": 1}
    ) as response:
        raw = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()

    frames = _parse_sse(raw)
    phase_frames = [data for kind, data in frames if kind == "phase"]
    assert [f["seq"] for f in phase_frames] == [2, 3]
    assert all(f["seq"] != 1 for f in phase_frames)


async def test_resume_generator_directly_skips_events_up_to_since_seq(
    clean_db: Database,
) -> None:
    slug = "proj-sse-resume-direct"
    scan_id = await _seed_scan(clean_db, slug=slug, status="failed")
    for seq in (1, 2, 3, 4):
        await _seed_event(clean_db, scan_id, seq=seq, kind="attack", data={"n": seq})

    raw = "".join([chunk async for chunk in scan_event_stream(clean_db, scan_id, since_seq=2)])
    frames = _parse_sse(raw)
    phase_seqs = [data["seq"] for kind, data in frames if kind == "phase"]
    assert phase_seqs == [3, 4]
    assert frames[-1][0] == "done"
    assert frames[-1][1]["status"] == "failed"


def test_terminal_statuses_cover_every_status_the_runner_ever_leaves_a_finished_scan_in() -> None:
    # `_mark_scan_completed`/`_stop_at_cap`/`_mark_scan_failed` in snag.runner
    # are the only three call sites that ever move a scan out of
    # pending/running — this constant must always match.
    assert frozenset({"completed", "stopped_at_cap", "failed"}) == TERMINAL_STATUSES


# --------------------------------------------------------------------- Task 2
# (write_progress seam, called from the runner's per-attack persistence)


async def _make_single_attack_project(db: Database, *, slug: str) -> tuple[int, int]:
    """Exactly one rule/one surface matching exactly one single-turn
    technique (mirrors `test_budget_caps._make_scannable_project`) — so the
    number of `scan_events` rows a `repeats=N` scan produces is fully
    predictable (`N`), making "monotonically increasing" trivial to assert
    exactly, not just loosely."""
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
    assert len(attacks) == 1
    return int(rule_id), int(surface_id)


async def test_write_progress_produces_monotonically_increasing_scan_events(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    fake = FakeCompletions()
    slug = "proj-sse-progress-monotonic"
    rule_id, surface_id = await _make_single_attack_project(clean_db, slug=slug)
    repeats = 3
    fake.responses.extend(_safe_response() for _ in range(repeats + len(GAP_CHECKLIST)))

    async with client_factory(fake) as client:
        res = await client.post(
            "/api/scans",
            json={"slug": slug, "mode": "custom", "surfaces": ["direct"], "repeats": repeats},
        )
        assert res.status_code == 200, res.text
        scan_id = res.json()["scan_id"]
        await drain_scan_queue(clean_db, fake)
        got = await client.get(f"/api/scans/{scan_id}")

    assert got.json()["status"] == "completed"
    assert got.json()["attacks_done"] == repeats

    async with clean_db.acquire() as conn:
        events = await conn.fetch(
            "SELECT seq, kind, data FROM scan_events WHERE scan_id = $1 ORDER BY seq", scan_id
        )
    assert [e["seq"] for e in events] == list(range(1, repeats + 1))
    for event in events:
        assert event["kind"] == "attack"
        data = event["data"]
        assert data["rule_id"] == rule_id
        assert data["surface_id"] == surface_id
        assert "technique_id" in data
        assert "broke" in data
        assert "cost" in data


async def test_write_progress_event_data_carries_technique_rule_surface_broke_and_running_cost(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    """The Scanning screen's matrix + attack log + cost render off exactly
    these fields (per the plan's acceptance criteria) — this test asserts
    the SHAPE, not just that events exist."""
    fake = FakeCompletions()
    slug = "proj-sse-progress-shape"
    rule_id, surface_id = await _make_single_attack_project(clean_db, slug=slug)
    fake.responses.extend(_safe_response() for _ in range(1 + len(GAP_CHECKLIST)))

    async with client_factory(fake) as client:
        res = await client.post("/api/scans", json={"slug": slug, "mode": "quick"})
        scan_id = res.json()["scan_id"]
        await drain_scan_queue(clean_db, fake)

    async with clean_db.acquire() as conn:
        event = await conn.fetchrow(
            "SELECT data FROM scan_events WHERE scan_id = $1 AND seq = 1", scan_id
        )
        scan_row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)

    data = event["data"]
    assert data["technique_id"]
    assert data["rule_id"] == rule_id
    assert data["surface_id"] == surface_id
    assert data["broke"] is False  # the safe response never contains the forbidden string
    assert Decimal(data["cost"]) >= 0

    # And the scans counters this same write updated agree with the event.
    assert scan_row["current_rule_id"] == rule_id
    assert scan_row["current_surface_id"] == surface_id
    assert scan_row["attacks_done"] == 1
    assert scan_row["breaks_found"] == 0
    assert scan_row["cost"] == Decimal(data["cost"])


async def test_write_progress_breaks_found_counter_increments_when_an_attack_breaks(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    fake = FakeCompletions()
    slug = "proj-sse-progress-break"
    await _make_single_attack_project(clean_db, slug=slug)
    # A response containing the forbidden string breaks the rule.
    fake.responses.append(_safe_response("this-never-matches-anything, oops"))
    fake.responses.extend(_safe_response() for _ in range(len(GAP_CHECKLIST)))

    async with client_factory(fake) as client:
        res = await client.post("/api/scans", json={"slug": slug, "mode": "quick"})
        scan_id = res.json()["scan_id"]
        await drain_scan_queue(clean_db, fake)

    async with clean_db.acquire() as conn:
        event = await conn.fetchrow(
            "SELECT data FROM scan_events WHERE scan_id = $1 AND seq = 1", scan_id
        )
        scan_row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)

    assert event["data"]["broke"] is True
    assert scan_row["breaks_found"] == 1


async def test_progress_written_during_a_scan_is_immediately_visible_over_the_live_stream(
    client_factory: ClientFactory, clean_db: Database, drain_scan_queue: DrainScanQueue
) -> None:
    """End-to-end: a scan drained through the real runner writes progress
    that `GET /scans/{id}/stream` can then tail and resume from — the seam
    Task 1 and Task 2 are joined by."""
    fake = FakeCompletions()
    slug = "proj-sse-e2e"
    await _make_single_attack_project(clean_db, slug=slug)
    repeats = 2
    fake.responses.extend(_safe_response() for _ in range(repeats + len(GAP_CHECKLIST)))

    async with client_factory(fake) as client:
        res = await client.post(
            "/api/scans",
            json={"slug": slug, "mode": "custom", "surfaces": ["direct"], "repeats": repeats},
        )
        scan_id = res.json()["scan_id"]
        await drain_scan_queue(clean_db, fake)

        async with client.stream("GET", f"/api/scans/{scan_id}/stream") as response:
            raw = b"".join([chunk async for chunk in response.aiter_bytes()]).decode()

    frames = _parse_sse(raw)
    phase_frames = [data for kind, data in frames if kind == "phase"]
    assert [f["seq"] for f in phase_frames] == [1, 2]
    assert frames[-1] == ("done", {"status": "completed"})
