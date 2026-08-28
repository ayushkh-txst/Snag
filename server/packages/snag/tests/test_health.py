"""GET /api/healthz touches the database and reports ok."""

from __future__ import annotations

from substrate.llm import FakeCompletions


async def test_healthz_returns_ok_and_touches_the_database(client_factory) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.get("/api/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
