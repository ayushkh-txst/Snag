"""asyncpg connection pooling with an explicit, stated size."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any, Self

import asyncpg


async def _init_connection(conn: asyncpg.Connection[Any]) -> None:
    """Make jsonb round-trip as dict instead of str.

    Also pin search_path explicitly. Every query in this codebase uses
    unqualified table names (`SELECT ... FROM chunks`, etc.), which
    resolves against whatever search_path the backend happens to have. A
    transaction-mode PgBouncer/Supavisor (Supabase's pooler, among others)
    can hand the same physical backend to a different logical client
    between transactions — if any of them ever runs `SET search_path=''`
    (which is exactly what pg_dump's restore output does, as a hijacking
    guard), that setting can persist for whoever gets the backend next,
    turning every unqualified reference into UndefinedTableError. Confirmed
    live: a `psql`-driven corpus restore against Supabase's transaction
    pooler broke this pool's own queries afterward. Setting search_path on
    every new connection this pool creates makes it resilient regardless of
    what any other client did to a shared backend first.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.execute("SET search_path TO public")


class Database:
    """A pool with a size you chose on purpose."""

    def __init__(self, dsn: str, *, min_size: int = 2, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool[Any] | None = None

    @property
    def pool(self) -> asyncpg.Pool[Any]:
        if self._pool is None:
            msg = "Database.connect() has not been awaited"
            raise RuntimeError(msg)
        return self._pool

    async def connect(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            init=_init_connection,
            command_timeout=60,
            # asyncpg caches prepared statements per physical connection by
            # default. Under a transaction/statement-mode PgBouncer (Supabase's
            # pooler, among others) a physical connection is handed to a
            # different logical client between statements, so a cached
            # statement from one client collides with another's — asyncpg
            # raises DuplicatePreparedStatementError. Disabling the cache
            # costs a bit of re-planning per query; that's cheaper than a
            # deployment-specific footgun, and correct against a direct
            # connection too, just without the (usually negligible) caching win.
            statement_cache_size=0,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection[Any]]:
        async with self.pool.acquire() as conn:
            yield conn

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @classmethod
    @asynccontextmanager
    async def open(cls, dsn: str, **kw: int) -> AsyncIterator[Database]:
        db = cls(dsn, **kw)
        await db.connect()
        try:
            yield db
        finally:
            await db.close()
