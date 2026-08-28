"""GET /api/projects/{slug}/report and GET .../report/{break_id}: the real
report surface (01-12), built entirely on `snag.report`'s aggregation over
`attack_runs` — replacing the tracer's inline aggregation (01-01) that used
to live in this file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from snag.api.app import ctx
from snag.api.deps import require_slug
from snag.report import aggregate_report, break_detail

router = APIRouter()


@router.get("/projects/{slug}/report")
async def get_report(slug: str, request: Request) -> dict[str, Any]:
    await require_slug(request, slug)
    state = ctx(request)
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
