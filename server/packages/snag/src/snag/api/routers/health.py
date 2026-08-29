"""Liveness AND readiness — it touches the database, because an API that
reports healthy while its pool is dead is worse than one that reports
nothing (same reasoning as CiteDelta's /healthz).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from snag.api.app import ctx

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    state = ctx(request)
    async with state.db.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}
