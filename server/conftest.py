"""Shared fixtures. Tests run against their OWN database, never the dev one.

Mirrors CiteDelta-RAG/conftest.py — same derive-a-`_test`-DSN, migrate-once,
`db`/`clean_db` fixture shape, adapted for Snag's own table set.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from substrate.db import Database

ROOT = Path(__file__).parent

DEV_DSN = os.environ.get("DATABASE_URL", "postgresql://snag:snag@localhost:5432/snag")

TABLES = (
    "technique_stats",
    "techniques",
    "fixes",
    "gaps",
    "scan_events",
    "attack_runs",
    "scans",
    "surfaces",
    "questions",
    "rules",
    "prompt_versions",
    "projects",
    "jobs",
)


def _derive_test_dsn(dsn: str) -> str:
    """postgresql://…/snag  →  postgresql://…/snag_test

    Deriving rather than configuring is deliberate: there is no second
    environment variable to forget to set, and no way for the suite to point
    at the dev database by accident.
    """
    parts = urlparse(dsn)
    name = parts.path.lstrip("/") or "snag"
    if name.endswith("_test"):
        return dsn
    return urlunparse(parts._replace(path=f"/{name}_test"))


TEST_DSN = _derive_test_dsn(DEV_DSN)


async def _ensure_test_database() -> None:
    """CREATE DATABASE if absent, connecting to the `postgres` maintenance db.

    CREATE DATABASE cannot run inside a transaction block, which is why this
    uses a bare connection rather than a pool with an implicit transaction.
    """
    parts = urlparse(TEST_DSN)
    target = parts.path.lstrip("/")
    admin = urlunparse(parts._replace(path="/postgres"))

    conn = await asyncpg.connect(admin)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", target)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{target}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def _migrated() -> Iterator[None]:
    """Create the test database and bring it to head, once per session.

    Deliberately SYNC: alembic's async env.py calls asyncio.run(), which
    explodes if an event loop is already running. A sync session fixture
    completes before pytest-asyncio starts one.
    """
    if TEST_DSN == DEV_DSN:
        msg = "refusing to run tests against the dev database"
        raise RuntimeError(msg)

    asyncio.run(_ensure_test_database())

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    # Alembic's env.py reads Settings, so point the whole process at the test
    # database for the duration of the run.
    os.environ["DATABASE_URL"] = TEST_DSN
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def dsn() -> str:
    return TEST_DSN


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    async with Database.open(TEST_DSN, min_size=1, max_size=5) as database:
        yield database


@pytest.fixture
async def clean_db(db: Database) -> AsyncIterator[Database]:
    """Empty the test corpus. Safe now — this is not the dev database."""

    async def _truncate() -> None:
        async with db.acquire() as conn:
            await conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")

    await _truncate()
    yield db
    await _truncate()
