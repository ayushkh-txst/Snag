"""Shared fixtures for the snag package's tests.

Talks to the same test Postgres as the rest of the suite (server/conftest.py
creates and migrates it once per session, before any test here runs); only
the LLM is faked, via a FastAPI dependency override on `get_completions` —
the seam plan 01-02 extends for per-request BYOK. No network call happens
anywhere in this package's tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest

from snag.api.app import create_app
from snag.api.deps import get_completions
from snag.config import get_settings
from substrate.llm import FakeCompletions


@asynccontextmanager
async def running_app(fake: FakeCompletions) -> AsyncIterator[httpx.AsyncClient]:
    """A real app (real test-Postgres via lifespan) with the LLM faked.

    `get_settings()` is process-cached; server/conftest.py points
    DATABASE_URL at the test database before any Settings() is built, so
    `create_app()`'s lifespan connects there — never the dev database.
    `.cache_clear()` is defensive, not load-bearing.
    """
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_completions] = lambda: fake
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


@pytest.fixture
def client_factory() -> Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]:
    """`async with client_factory(fake) as client: ...` — see `running_app`."""
    return running_app
