"""snag.fixes.scan_delta / GET /api/projects/{slug}/history (01-14, FIX-03):
fixed/new/unchanged deltas between scans, with false-positive exclusion
carried the same way `snag.report`'s own aggregation does (CHECK-06), and a
brand new failure surfaced loudly via `newAttackKeys` rather than folded
silently into a bare `added` count.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx

from snag.fixes import scan_delta
from substrate.db import Database
from substrate.llm import FakeCompletions

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]

MODEL = "qwen/qwen3.8-flash"


# --------------------------------------------------------------- DB seeding


async def _make_project(db: Database, *, slug: str, model: str = MODEL) -> None:
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, model)
        await conn.execute(
            "INSERT INTO prompt_versions (project_id, full_text) VALUES ($1, $2)",
            slug,
            "Be safe. Never do X.",
        )


async def _add_rule(db: Database, slug: str, *, category: str = "content_prohibition") -> int:
    async with db.acquire() as conn:
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, checker_type, testable)
               VALUES ($1, $2, $3, 'negative', 'forbidden_text', true) RETURNING id""",
            slug,
            f"a rule about {category}",
            category,
        )
    return int(rule_id)


async def _add_surface(
    db: Database, slug: str, *, kind: str = "chat", path: str = "user message"
) -> int:
    async with db.acquire() as conn:
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, confirmed, user_controlled)
               VALUES ($1, $2, $3, true, true) RETURNING id""",
            slug,
            kind,
            path,
        )
    return int(surface_id)


async def _add_scan(db: Database, slug: str, *, mode: str = "quick", call_count: int = 0) -> int:
    async with db.acquire() as conn:
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, status, call_count,
                                   started_at, finished_at)
               VALUES ($1, $2, 1, 'completed', $3, now(), now()) RETURNING id""",
            slug,
            mode,
            call_count,
        )
    return int(scan_id)


async def _add_attack_run(
    db: Database,
    *,
    scan_id: int,
    rule_id: int,
    surface_id: int,
    technique_id: str,
    passed: bool,
    false_positive: bool = False,
) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """INSERT INTO attack_runs (scan_id, rule_id, surface_id, technique_id, family, model,
                                          conversation, passed, checker_output, false_positive)
               VALUES ($1, $2, $3, $4, 'roleplay', $5, $6, $7, $8, $9)""",
            scan_id,
            rule_id,
            surface_id,
            technique_id,
            MODEL,
            [{"role": "assistant", "content": "a reply"}],
            passed,
            "forbidden_text PASSED" if passed else "forbidden_text FAILED",
            false_positive,
        )


# ------------------------------------------------------------- scan_delta (pure)


def test_scan_delta_partitions_fixed_new_and_unchanged() -> None:
    prev = {"1:1:roleplay.01", "1:1:roleplay.02", "2:1:many_shot.01"}
    curr = {"1:1:roleplay.01", "3:1:context_switch.01"}

    delta = scan_delta(prev, curr)

    assert delta.unchanged == ["1:1:roleplay.01"]
    assert delta.fixed == ["1:1:roleplay.02", "2:1:many_shot.01"]
    assert delta.new == ["3:1:context_switch.01"]


def test_scan_delta_flags_new_failures_loudly() -> None:
    assert scan_delta({"a"}, {"a", "b"}).has_new_failures is True
    assert scan_delta({"a"}, {"a"}).has_new_failures is False


def test_scan_delta_treats_a_first_ever_break_as_new_against_an_empty_predecessor() -> None:
    delta = scan_delta(set(), {"1:1:roleplay.01"})
    assert delta.new == ["1:1:roleplay.01"]
    assert delta.fixed == []
    assert delta.unchanged == []


def test_scan_delta_everything_gone_is_all_fixed_and_nothing_new() -> None:
    delta = scan_delta({"1:1:roleplay.01", "1:1:roleplay.02"}, set())
    assert sorted(delta.fixed) == ["1:1:roleplay.01", "1:1:roleplay.02"]
    assert delta.new == []
    assert delta.unchanged == []


# --------------------------------------------------------- GET /history (HTTP)


async def test_history_endpoint_reports_the_first_scan_as_all_new(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-history-first"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(clean_db, slug)
    surface_id = await _add_surface(clean_db, slug)
    scan_id = await _add_scan(clean_db, slug, call_count=1)
    await _add_attack_run(
        clean_db, scan_id=scan_id, rule_id=rule_id, surface_id=surface_id,
        technique_id="roleplay.01", passed=False,
    )

    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/history")
    assert res.status_code == 200, res.text
    history = res.json()
    assert len(history) == 1
    assert history[0]["breaks"] == 1
    assert history[0]["added"] == 1
    assert history[0]["fixed"] == 0
    assert history[0]["unchanged"] == 0
    assert history[0]["newAttackKeys"] == [f"{rule_id}:{surface_id}:roleplay.01"]


async def test_history_endpoint_reports_fixed_new_and_unchanged_across_a_rescan(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-history-rescan"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(clean_db, slug)
    surface_id = await _add_surface(clean_db, slug)

    scan1 = await _add_scan(clean_db, slug, call_count=2)
    await _add_attack_run(
        clean_db, scan_id=scan1, rule_id=rule_id, surface_id=surface_id,
        technique_id="roleplay.01", passed=False,
    )
    await _add_attack_run(
        clean_db, scan_id=scan1, rule_id=rule_id, surface_id=surface_id,
        technique_id="roleplay.02", passed=False,
    )

    scan2 = await _add_scan(clean_db, slug, call_count=3)
    # roleplay.01 held this time (fixed); roleplay.02 still breaks
    # (unchanged); many_shot.01 is a brand new failure (new).
    await _add_attack_run(
        clean_db, scan_id=scan2, rule_id=rule_id, surface_id=surface_id,
        technique_id="roleplay.01", passed=True,
    )
    await _add_attack_run(
        clean_db, scan_id=scan2, rule_id=rule_id, surface_id=surface_id,
        technique_id="roleplay.02", passed=False,
    )
    await _add_attack_run(
        clean_db, scan_id=scan2, rule_id=rule_id, surface_id=surface_id,
        technique_id="many_shot.01", passed=False,
    )

    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/history")
    assert res.status_code == 200, res.text
    history = res.json()
    assert len(history) == 2

    first, second = history
    assert first["breaks"] == 2
    assert second["breaks"] == 2
    assert second["fixed"] == 1
    assert second["added"] == 1
    assert second["unchanged"] == 1
    assert second["newAttackKeys"] == [f"{rule_id}:{surface_id}:many_shot.01"]
    assert second["fixedAttackKeys"] == [f"{rule_id}:{surface_id}:roleplay.01"]


async def test_history_endpoint_respects_false_positive_exclusion(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-history-fp"
    await _make_project(clean_db, slug=slug)
    rule_id = await _add_rule(clean_db, slug)
    surface_id = await _add_surface(clean_db, slug)
    scan1 = await _add_scan(clean_db, slug, call_count=1)
    await _add_attack_run(
        clean_db, scan_id=scan1, rule_id=rule_id, surface_id=surface_id,
        technique_id="roleplay.01", passed=False, false_positive=True,
    )

    scan2 = await _add_scan(clean_db, slug, call_count=1)
    # A fresh rescan row never inherits false_positive=true on its own
    # (runner.py, out of scope here, always inserts false) — the exclusion
    # must still survive because the SAME identity was dismissed before.
    await _add_attack_run(
        clean_db, scan_id=scan2, rule_id=rule_id, surface_id=surface_id,
        technique_id="roleplay.01", passed=False, false_positive=False,
    )

    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/history")
    history = res.json()
    assert len(history) == 2
    for row in history:
        assert row["breaks"] == 0
        assert row["added"] == 0


async def test_history_endpoint_for_a_project_with_no_scans_returns_an_empty_list(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-history-empty"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/history")
    assert res.status_code == 200
    assert res.json() == []


async def test_history_endpoint_for_unknown_slug_is_404(client_factory: ClientFactory) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.get("/api/projects/does-not-exist/history")
    assert res.status_code == 404
