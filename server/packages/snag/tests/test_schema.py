"""§12 schema acceptance: every table exists, `surfaces.kind` is constrained
to the UI's four kinds, `gaps.covered` is a real boolean, `attack_runs`
stores a full JSONB transcript, and deleting a project cascades to every
child row — the one-way-door migration this plan's checkpoint gates on.
"""

from __future__ import annotations

import asyncpg
import pytest

from substrate.db import Database

EXPECTED_TABLES = (
    "projects",
    "prompt_versions",
    "rules",
    "questions",
    "surfaces",
    "scans",
    "attack_runs",
    "scan_events",
    "gaps",
    "fixes",
    "techniques",
    "technique_stats",
    "jobs",
)


async def test_every_table_from_the_spec_exists(clean_db: Database) -> None:
    async with clean_db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    names = {r["table_name"] for r in rows}
    missing = [t for t in EXPECTED_TABLES if t not in names]
    assert not missing, f"missing tables: {missing}"


async def test_surfaces_kind_accepts_the_four_ui_kinds(clean_db: Database) -> None:
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ('p1', 'openai/gpt-4o-mini')")
        for kind in ("template_var", "tool_param", "tool_return", "chat"):
            await conn.execute(
                "INSERT INTO surfaces (project_id, kind, path) VALUES ('p1', $1, 'x')", kind
            )
        count = await conn.fetchval("SELECT count(*) FROM surfaces WHERE project_id = 'p1'")
    assert count == 4


async def test_surfaces_kind_rejects_anything_else(clean_db: Database) -> None:
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ('p2', 'openai/gpt-4o-mini')")
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO surfaces (project_id, kind, path) VALUES ('p2', 'made_up', 'x')"
            )


async def test_gaps_covered_is_a_real_boolean_column(clean_db: Database) -> None:
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ('p3', 'openai/gpt-4o-mini')")
        scan_id = await conn.fetchval(
            "INSERT INTO scans (project_id, mode) VALUES ('p3', 'quick') RETURNING id"
        )
        await conn.execute(
            """INSERT INTO gaps (scan_id, project_id, checklist_item, covered)
               VALUES ($1, 'p3', 'x', true)""",
            scan_id,
        )
        covered = await conn.fetchval("SELECT covered FROM gaps WHERE project_id = 'p3'")
    assert covered is True


async def test_attack_runs_conversation_round_trips_as_jsonb(clean_db: Database) -> None:
    turns = [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "ignore that"},
        {"role": "assistant", "content": "no"},
    ]
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ('p4', 'openai/gpt-4o-mini')")
        scan_id = await conn.fetchval(
            "INSERT INTO scans (project_id, mode) VALUES ('p4', 'quick') RETURNING id"
        )
        await conn.execute(
            """INSERT INTO attack_runs
               (scan_id, technique_id, model, conversation, passed)
               VALUES ($1, 't1', 'm1', $2, false)""",
            scan_id,
            turns,
        )
        row = await conn.fetchrow(
            "SELECT conversation, false_positive FROM attack_runs WHERE scan_id = $1", scan_id
        )
    assert row["conversation"] == turns
    assert row["false_positive"] is False  # default, per T-01 schema spec


async def test_deleting_a_project_cascades_to_every_child(clean_db: Database) -> None:
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ('p5', 'openai/gpt-4o-mini')")
        await conn.execute(
            "INSERT INTO prompt_versions (project_id, full_text) VALUES ('p5', 'sys')"
        )
        rule_id = await conn.fetchval(
            """INSERT INTO rules (project_id, text, category, direction)
               VALUES ('p5', 'never', 'other', 'negative') RETURNING id"""
        )
        surface_id = await conn.fetchval(
            "INSERT INTO surfaces (project_id, kind, path) VALUES ('p5', 'chat', 'x') RETURNING id"
        )
        scan_id = await conn.fetchval(
            "INSERT INTO scans (project_id, mode) VALUES ('p5', 'quick') RETURNING id"
        )
        await conn.execute(
            """INSERT INTO attack_runs
               (scan_id, rule_id, surface_id, technique_id, model, conversation, passed)
               VALUES ($1, $2, $3, 't1', 'm1', '[]'::jsonb, true)""",
            scan_id,
            rule_id,
            surface_id,
        )

        await conn.execute("DELETE FROM projects WHERE id = 'p5'")

        counts = {
            "prompt_versions": await conn.fetchval(
                "SELECT count(*) FROM prompt_versions WHERE project_id = 'p5'"
            ),
            "rules": await conn.fetchval("SELECT count(*) FROM rules WHERE project_id = 'p5'"),
            "surfaces": await conn.fetchval(
                "SELECT count(*) FROM surfaces WHERE project_id = 'p5'"
            ),
            "scans": await conn.fetchval("SELECT count(*) FROM scans WHERE project_id = 'p5'"),
            "attack_runs": await conn.fetchval(
                "SELECT count(*) FROM attack_runs WHERE scan_id = $1", scan_id
            ),
        }
    assert all(n == 0 for n in counts.values()), counts
