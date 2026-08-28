"""Database's pool construction — the one thing worth a unit test here is
what gets passed to asyncpg.create_pool and what runs on each new physical
connection, since a wrong flag/missing setup there passes silently against
a direct connection and only breaks in production against a transaction-mode
PgBouncer (Supabase's pooler, among others). Everything else about Database
is exercised transitively by the @pytest.mark.integration suite against a
real Postgres.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from substrate.db import Database, _init_connection


@pytest.mark.asyncio
async def test_connect_disables_the_statement_cache() -> None:
    """asyncpg caches prepared statements per-connection by default. Under a
    transaction/statement-mode PgBouncer, physical connections are shared
    across logical clients mid-session, so one client's cached statement can
    collide with another's — verified live against Supabase's transaction
    pooler, which raises DuplicatePreparedStatementError without this."""
    with patch("substrate.db.asyncpg.create_pool", new_callable=AsyncMock) as create_pool:
        db = Database("postgresql://u:p@host/db")
        await db.connect()

    assert create_pool.call_args.kwargs["statement_cache_size"] == 0


@pytest.mark.asyncio
async def test_new_connections_get_an_explicit_search_path() -> None:
    """A transaction-mode PgBouncer/Supavisor can hand a shared physical
    backend to a different logical client between transactions. If ANY
    client on that backend ever runs `SET search_path=''` — which is
    exactly what pg_dump's standard restore preamble does, as a security
    measure against operator/function hijacking — the setting can persist
    for whoever gets that backend next, turning every unqualified table
    reference into UndefinedTableError. Confirmed live: a `psql`-driven
    corpus restore against Supabase's transaction pooler broke this app's
    own connection pool's unqualified queries afterward. Setting
    search_path explicitly on every new connection this pool creates makes
    the app's own queries resilient to that, regardless of what any other
    client did to a shared backend before it."""
    conn = AsyncMock()
    await _init_connection(conn)
    conn.execute.assert_any_call("SET search_path TO public")
