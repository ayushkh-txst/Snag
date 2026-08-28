"""Request-scoped dependencies.

`get_completions` is the SEAM 01-01 opened and this plan (01-02) finishes:
per-request BYOK header (`X-OpenRouter-Key`) -> owner env key ->
examples-only (scans disabled, but the seeded read-only examples still
browse). `resolve_key` is the single place that precedence is decided;
`require_funding` turns "nothing resolved" into a 402 on scan-funding
endpoints only — read/report/examples endpoints never depend on it, so they
stay browsable key-free even with no owner key configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import asyncpg
from fastapi import HTTPException, Request

from snag.api.app import ctx
from snag.config import get_settings
from substrate.llm import Completions
from substrate.llm.factory import build_completions

_BYOK_HEADER = "X-OpenRouter-Key"

KeySource = Literal["byok", "owner", "none"]


@dataclass(frozen=True, slots=True)
class KeyResolution:
    """Which key funds this request's model calls, and where it came from.

    `key` is held only long enough to build this request's adapter — never
    logged, never persisted, never echoed in a response body (T-02-01)."""

    key: str | None
    source: KeySource
    owner_funded: bool


def resolve_key(request: Request) -> KeyResolution:
    """BYOK header > owner env key > none.

    A per-request `X-OpenRouter-Key` funds that request regardless of
    whether an owner key is configured. Absent it, the owner
    `OPENROUTER_API_KEY` funds the request (`owner_funded=True`, so
    `ratelimit.guard_owner_scans` can single those requests out — a BYOK
    request is never rate limited). Absent both, `source="none"`;
    `require_funding` is what turns that into a 402.
    """
    header_key = request.headers.get(_BYOK_HEADER)
    if header_key:
        return KeyResolution(key=header_key, source="byok", owner_funded=False)
    owner_key = ctx(request).settings.openrouter_api_key
    if owner_key:
        return KeyResolution(key=owner_key, source="owner", owner_funded=True)
    return KeyResolution(key=None, source="none", owner_funded=False)


def get_completions(request: Request) -> Completions:
    """Build a fresh, per-request Completions adapter funded by whichever
    key `resolve_key` resolves — never a startup-built shared client, since
    the funding key can differ on every single request."""
    state = ctx(request)
    resolution = resolve_key(request)
    return build_completions(
        provider=state.settings.llm_provider,
        api_key=resolution.key or "",
        ledger=state.ledger,
    )


def require_funding(request: Request) -> None:
    """Dependency for scan-funding endpoints only. Raises 402 when no key
    resolves at all; BYOK and owner-funded requests pass through untouched.
    Read/report/examples endpoints never depend on this, so they stay
    browsable key-free."""
    resolution = resolve_key(request)
    if resolution.source == "none":
        raise HTTPException(
            status_code=402,
            detail=(
                "no OpenRouter key available to fund this scan — "
                "set X-OpenRouter-Key or configure an owner OPENROUTER_API_KEY"
            ),
        )


def validate_model(model: str) -> None:
    """KEY-03: reject any model string outside `ACCEPTED_MODELS` before any
    `get_completions`/model call is made. A no-op whenever `accepted_models`
    is unset/empty — no restriction, for local/dev flexibility. Called at
    the top of `POST /projects` and `POST /scans`, before either touches a
    completions adapter."""
    accepted = get_settings().accepted_models
    if accepted and model not in accepted:
        raise HTTPException(status_code=400, detail=f"model {model!r} is not in the accepted list")


async def require_slug(request: Request, slug: str) -> asyncpg.Record:
    """Fetch a project row by slug or raise 404 — the one place that 404 is
    decided, so every router agrees on what "no such project" means."""
    state = ctx(request)
    async with state.db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM projects WHERE id = $1", slug)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such project: {slug}")
    return row


async def require_mutable_slug(request: Request, slug: str) -> asyncpg.Record:
    """Like `require_slug`, but 403s for a seeded example (T-15-01): the six
    01-15 examples are read-only fixtures served with no key, and must
    reject any mutation (delete, rule/surface edits, a new scan, applying a
    fix) — never a client's own state, since anonymous mutation of a shared,
    key-free example is a tampering and cost-abuse vector, not a per-user
    workspace edit."""
    row = await require_slug(request, slug)
    if row["seeded"]:
        raise HTTPException(status_code=403, detail="seeded example projects are read-only")
    return row
