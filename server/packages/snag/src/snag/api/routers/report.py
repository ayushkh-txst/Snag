"""GET /api/projects/{slug}/report, GET .../report/{break_id}, and
POST .../report/{break_id}/false-positive: the real report surface (01-12),
built entirely on `snag.report`'s aggregation over `attack_runs` —
replacing the tracer's inline aggregation (01-01) that used to live in
this file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from snag.api.app import ctx
from snag.api.deps import require_mutable_slug, require_slug
from snag.report import aggregate_report, break_detail, purge_expired_ephemeral, set_false_positive

router = APIRouter()


class FalsePositiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: bool = True


class FalsePositiveResponse(BaseModel):
    ok: bool


@router.get("/projects/{slug}/report")
async def get_report(slug: str, request: Request) -> dict[str, Any]:
    """PRIV-02: every call to this endpoint first sweeps globally-expired
    ephemeral projects — not just the one named by `slug` — so a request for
    an already-expired project's own report correctly 404s instead of
    returning a report for a project that should already be gone, and any
    OTHER caller's `GET .../report` also advances everyone else's cleanup."""
    state = ctx(request)
    await purge_expired_ephemeral(state.db, grace_seconds=state.settings.ephemeral_grace_seconds)
    await require_slug(request, slug)
    report = await aggregate_report(state.db, slug)
    if report is None:
        raise HTTPException(status_code=404, detail=f"no such project: {slug}")
    return report


@router.get("/projects/{slug}/report/{break_id}")
async def get_break_detail(slug: str, break_id: str, request: Request) -> dict[str, Any]:
    await require_slug(request, slug)
    state = ctx(request)
    detail = await break_detail(state.db, slug, break_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no such break: {break_id}")
    return detail


@router.post(
    "/projects/{slug}/report/{break_id}/false-positive", response_model=FalsePositiveResponse
)
async def post_false_positive(
    slug: str, break_id: str, body: FalsePositiveRequest, request: Request
) -> FalsePositiveResponse:
    await require_mutable_slug(request, slug)  # T-15-01: a seeded example's breaks are read-only
    state = ctx(request)
    found = await set_false_positive(state.db, slug, break_id, body.value)
    if not found:
        raise HTTPException(status_code=404, detail=f"no such break: {break_id}")
    return FalsePositiveResponse(ok=True)
