"""A scan re-claimed after its worker died must not start over.

A queued scan is a durable job with `max_attempts = 5`. When the container
is replaced mid-run — a deploy, an OOM, a host recycling — the lease expires
and the queue hands the same job to the next worker. That worker rebuilt the
whole attack matrix and dispatched it again from the top.

Measured on the deployed service (scan 9, job 7 at `attempts = 3`, claimed by
three different pods): 421 attack_runs stored for 331 distinct attacks, 90 of
them re-run, some three times. Every duplicate is a paid model call, and the
break count is inflated by however many of them broke.
"""

from __future__ import annotations

from snag.runner import already_run_keys, unit_key


class _Unit:
    """Just the four fields `unit_key` reads."""

    def __init__(self, rule_id: int, surface_id: int, technique_id: str, repeat_index: int) -> None:
        self.rule = {"id": rule_id}
        self.surface = {"id": surface_id}
        self.attack = type("A", (), {"technique_id": technique_id})()
        self.repeat_index = repeat_index


def test_a_unit_is_keyed_by_what_makes_it_the_same_attack() -> None:
    a = unit_key(_Unit(1, 2, "direct_request.01", 0))
    b = unit_key(_Unit(1, 2, "direct_request.01", 0))
    assert a == b, "the same attack twice is the same key"


def test_a_different_repeat_is_a_different_attack() -> None:
    """Repeats are the point of repeats — they are not duplicates."""
    assert unit_key(_Unit(1, 2, "direct_request.01", 0)) != unit_key(
        _Unit(1, 2, "direct_request.01", 1)
    )


def test_rule_surface_and_technique_all_distinguish() -> None:
    base = _Unit(1, 2, "direct_request.01", 0)
    assert unit_key(base) != unit_key(_Unit(9, 2, "direct_request.01", 0))
    assert unit_key(base) != unit_key(_Unit(1, 9, "direct_request.01", 0))
    assert unit_key(base) != unit_key(_Unit(1, 2, "past_tense.01", 0))


async def test_already_run_keys_reads_what_the_scan_recorded(clean_db) -> None:  # type: ignore[no-untyped-def]
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO projects (id, model, seeded) VALUES ('p-resume', 'm', false)"
        )
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction, source_line,
                                  checker_type, checker_config, testable, confidence)
               VALUES ('p-resume','r','identity','negative','r','forbidden_text',
                       '{}'::jsonb, true, 0.9) RETURNING id"""
        )
        surface_id = await conn.fetchval(
            """INSERT INTO surfaces (project_id, kind, path, source, user_controlled)
               VALUES ('p-resume','chat','user message','chat', true) RETURNING id"""
        )
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, surfaces, models, status)
               VALUES ('p-resume','standard',2,'["direct"]'::jsonb,'["m"]'::jsonb,'running')
               RETURNING id"""
        )
        await conn.execute(
            """INSERT INTO attack_runs
                   (scan_id, rule_id, surface_id, technique_id, model, repeat_index,
                    conversation, passed)
               VALUES ($1,$2,$3,'direct_request.01','m',0,'[]'::jsonb,true)""",
            scan_id,
            rule_id,
            surface_id,
        )

    done = await already_run_keys(clean_db, scan_id)
    assert (str(rule_id), str(surface_id), "direct_request.01", 0) in done
    still_to_do = (str(rule_id), str(surface_id), "direct_request.01", 1)
    assert still_to_do not in done, "repeat 1 has not run yet"
    assert len(done) == 1


async def test_a_fresh_scan_has_nothing_to_skip(clean_db) -> None:  # type: ignore[no-untyped-def]
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model, seeded) VALUES ('p-new','m',false)")
        scan_id = await conn.fetchval(
            """INSERT INTO scans (project_id, mode, repeats, surfaces, models, status)
               VALUES ('p-new','standard',1,'["direct"]'::jsonb,'["m"]'::jsonb,'pending')
               RETURNING id"""
        )
    assert await already_run_keys(clean_db, scan_id) == set()
