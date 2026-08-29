"""POST /api/projects/{slug}/compare (01-14, FIX-04): the same
configuration run across several models. This router owns the
multi-model fan-out — one ordinary single-model scan per requested model
via `snag.runner.start_scan` (the same single-scan start site `POST
/api/scans` calls), each under its own pre-dispatch budget caps (T-14-02).
`start_scan` only enqueues; nothing here dispatches to a model, so
`FakeCompletions()` never needs any scripted responses.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx

from substrate.db import Database
from substrate.llm import FakeCompletions

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]

MODELS = ["qwen/qwen3.8-flash", "deepseek/deepseek-v4-flash-0731", "openai/gpt-5.6-luna"]


async def _make_project(db: Database, *, slug: str, model: str = MODELS[0]) -> None:
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO projects (id, model) VALUES ($1, $2)", slug, model)
        await conn.execute(
            "INSERT INTO prompt_versions (project_id, full_text) VALUES ($1, $2)",
            slug,
            "Be safe. Never do X.",
        )


async def test_compare_endpoint_starts_one_scan_per_model_via_start_scan(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-compare"
    await _make_project(clean_db, slug=slug)

    async with client_factory(FakeCompletions()) as client:
        res = await client.post(
            f"/api/projects/{slug}/compare", json={"mode": "quick", "models": MODELS}
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["scans"]) == len(MODELS)
    assert {row["model"] for row in body["scans"]} == set(MODELS)
    assert len({row["scan_id"] for row in body["scans"]}) == len(MODELS)  # all distinct

    async with clean_db.acquire() as conn:
        scans = await conn.fetch("SELECT * FROM scans WHERE project_id = $1", slug)
        jobs = await conn.fetch("SELECT * FROM jobs WHERE kind = 'scan'")
    assert len(scans) == len(MODELS)
    assert {s["models"][0] for s in scans} == set(MODELS)
    assert len(jobs) == len(MODELS)


async def test_compare_endpoint_applies_documented_mode_presets_per_scan(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-compare-modes"
    await _make_project(clean_db, slug=slug)

    async with client_factory(FakeCompletions()) as client:
        res = await client.post(
            f"/api/projects/{slug}/compare", json={"mode": "standard", "models": MODELS[:2]}
        )
        assert res.status_code == 200, res.text
        for row in res.json()["scans"]:
            got = await client.get(f"/api/scans/{row['scan_id']}")
            assert got.json()["surfaces"] == ["direct", "tool"]
            assert got.json()["repeats"] == 3
            assert got.json()["models"] == [row["model"]]


async def test_compare_endpoint_rejects_a_model_outside_the_allowlist_before_starting_any_scan(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-compare-badmodel"
    await _make_project(clean_db, slug=slug)
    fake = FakeCompletions()  # no scripted responses — a dispatch would raise

    async with client_factory(fake) as client:
        res = await client.post(
            f"/api/projects/{slug}/compare",
            json={"mode": "quick", "models": [MODELS[0], "not/an-accepted-model"]},
        )
    assert res.status_code == 400
    assert fake.calls == []

    async with clean_db.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM scans WHERE project_id = $1", slug)
    assert count == 0  # rejecting model #2 must not have already started model #1's scan


async def test_compare_endpoint_requires_at_least_one_model(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-compare-empty"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        res = await client.post(
            f"/api/projects/{slug}/compare", json={"mode": "quick", "models": []}
        )
    assert res.status_code == 422


async def test_compare_endpoint_custom_mode_requires_surfaces_and_repeats(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-compare-custom"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        res = await client.post(
            f"/api/projects/{slug}/compare", json={"mode": "custom", "models": [MODELS[0]]}
        )
    assert res.status_code == 422


async def test_compare_endpoint_custom_mode_rejects_unknown_surface_categories(
    client_factory: ClientFactory, clean_db: Database
) -> None:
    slug = "proj-compare-customsurf"
    await _make_project(clean_db, slug=slug)
    async with client_factory(FakeCompletions()) as client:
        res = await client.post(
            f"/api/projects/{slug}/compare",
            json={
                "mode": "custom",
                "surfaces": ["telepathy"],
                "repeats": 2,
                "models": [MODELS[0]],
            },
        )
    assert res.status_code == 400


async def test_compare_endpoint_for_unknown_slug_is_404(client_factory: ClientFactory) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.post(
            "/api/projects/does-not-exist/compare", json={"mode": "quick", "models": ["a"]}
        )
    assert res.status_code == 404
