"""A scan in progress has to be findable from the project, not just from the
browser tab that started it.

Reported live: a scan was started, the user moved to another step and came
back, and the scanning screen said the run had already stopped while the
worker was still going. Resuming depended entirely on a localStorage key
written by the config screen, so a typed URL, a refresh in another tab, or a
different device had nothing to reconnect to.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx

from substrate.db import Database
from substrate.llm import FakeCompletions

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]


async def _project(db: Database, slug: str) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO projects (id, model, seeded) VALUES ($1, 'qwen/qwen3.8-flash', false)",
            slug,
        )


async def _scan(db: Database, slug: str, status: str) -> int:
    async with db.acquire() as conn:
        return int(
            await conn.fetchval(
                """INSERT INTO scans (project_id, mode, repeats, surfaces, models, status)
                   VALUES ($1, 'standard', 2, '["direct"]'::jsonb,
                           '["qwen/qwen3.8-flash"]'::jsonb, $2)
                   RETURNING id""",
                slug,
                status,
            )
        )


async def test_a_running_scan_is_reported_as_active(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-active"
    await _project(clean_db, slug)
    scan_id = await _scan(clean_db, slug, "running")

    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/active-scan")

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scanId"] == scan_id
    assert body["status"] == "running"


async def test_a_pending_scan_counts_as_active_too(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    """Queued but not yet claimed is exactly the window the UI showed
    "Queuing attacks" in — it must still be resumable."""
    slug = "proj-pending"
    await _project(clean_db, slug)
    scan_id = await _scan(clean_db, slug, "pending")

    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/active-scan")

    assert res.json()["scanId"] == scan_id


async def test_a_finished_scan_is_not_active(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-done"
    await _project(clean_db, slug)
    await _scan(clean_db, slug, "completed")

    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/active-scan")

    assert res.status_code == 200
    assert res.json()["scanId"] is None


async def test_the_newest_unfinished_scan_wins(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-many"
    await _project(clean_db, slug)
    await _scan(clean_db, slug, "completed")
    newest = await _scan(clean_db, slug, "running")

    async with client_factory(FakeCompletions()) as client:
        res = await client.get(f"/api/projects/{slug}/active-scan")

    assert res.json()["scanId"] == newest


async def test_unknown_project_404s(client_factory: ClientFactory) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.get("/api/projects/nope/active-scan")
    assert res.status_code == 404
