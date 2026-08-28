"""Request-scoped dependencies.

`get_completions` is the SEAM plan 01-02 extends: per-request BYOK header
(`X-OpenRouter-Key`) -> owner env key -> examples-only (scans disabled, but
the seeded read-only examples still browse). This plan (01-01) wires the
owner-key path only — the tracer's `<human-check>` needs `OPENROUTER_API_KEY`
set precisely because that fallback chain doesn't exist yet.
"""

from __future__ import annotations

import asyncpg
from fastapi import HTTPException, Request

from snag.api.app import ctx
from substrate.llm import Completions
from substrate.llm.factory import build_completions


def get_completions(request: Request) -> Completions:
    state = ctx(request)
    return build_completions(
        provider=state.settings.llm_provider,
        api_key=state.settings.openrouter_api_key,
        ledger=state.ledger,
    )


async def require_slug(request: Request, slug: str) -> asyncpg.Record:
    """Fetch a project row by slug or raise 404 — the one place that 404 is
    decided, so every router agrees on what "no such project" means."""
    state = ctx(request)
    async with state.db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM projects WHERE id = $1", slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such project: {slug}")
    return row
