"""GET /api/healthz touches the database and reports ok."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx

from substrate.llm import FakeCompletions

ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]


async def test_healthz_returns_ok_and_touches_the_database(client_factory: ClientFactory) -> None:
    async with client_factory(FakeCompletions()) as client:
        res = await client.get("/api/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
