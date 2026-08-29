"""Shared fixtures for the snag package's tests.

Talks to the same test Postgres as the rest of the suite (server/conftest.py
creates and migrates it once per session, before any test here runs); only
the LLM is faked, via a FastAPI dependency override on `get_completions` —
the seam plan 01-02 extends for per-request BYOK. No network call happens
anywhere in this package's tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest

from snag.api import ratelimit
from snag.api.app import create_app
from snag.api.deps import get_completions
from snag.config import get_settings
from snag.runner import KIND_SCAN, QUEUE_NAME, make_scan_handler
from substrate.db import Database
from substrate.llm import Completions, FakeCompletions
from substrate.queue import JobQueue, Worker


@asynccontextmanager
async def running_app(fake: FakeCompletions) -> AsyncIterator[httpx.AsyncClient]:
    """A real app (real test-Postgres via lifespan) with the LLM faked.

    `get_settings()` is process-cached; server/conftest.py points
    DATABASE_URL at the test database before any Settings() is built, so
    `create_app()`'s lifespan connects there — never the dev database.
    `.cache_clear()` is defensive, not load-bearing.

    `ratelimit._WINDOWS` is cleared too: it's a module-level, process-
    lifetime dict by design (01-02's single-process limiter), but every
    test in this suite drives requests through the SAME ASGI test-client IP
    — without clearing it here, owner-funded `/api/scans` calls across
    unrelated tests would all count against one shared per-IP window and
    the whole suite would start 429ing itself once enough tests ran.
    """
    get_settings.cache_clear()
    ratelimit._WINDOWS.clear()
    app = create_app()
    app.dependency_overrides[get_completions] = lambda: fake
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield client


ClientFactory = Callable[[FakeCompletions], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def client_factory() -> ClientFactory:
    """`async with client_factory(fake) as client: ...` — see `running_app`."""
    return running_app


async def _drain_scan_queue(db: Database, completions: Completions) -> Worker:
    """01-09: `POST /api/scans` only enqueues a job — a test that wants the
    scan to actually run drains the queue itself, exactly like a `snag
    work` worker would, using the SAME `FakeCompletions` double the test
    already scripted (the app's own DI override only reaches requests, not
    an out-of-band worker loop)."""
    queue = JobQueue(db, queue=QUEUE_NAME)
    worker = Worker(queue, concurrency=1, poll_interval=0.01)
    worker.register(KIND_SCAN, make_scan_handler(db, completions))
    await worker.run_until_idle()
    return worker


DrainScanQueue = Callable[[Database, Completions], Awaitable[Worker]]


@pytest.fixture
def drain_scan_queue() -> DrainScanQueue:
    """`worker = await drain_scan_queue(db, fake)` — see `_drain_scan_queue`."""
    return _drain_scan_queue
