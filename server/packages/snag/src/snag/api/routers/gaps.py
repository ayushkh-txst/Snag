"""GET /api/projects/{slug}/gaps: the eight-item checklist probed during
the same scan (GAP-01), each observation templated mechanically from what
actually happened rather than narrated by a model (GAP-02). `covered` is
a real boolean straight off the `gaps` table — no client-side parsing of
`verdict` text (the fragile `verdict.startswith("Covered")` prefix
contract the UI mockup used; see the schema migration's own docstring).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from snag.api.app import ctx
from snag.api.deps import require_slug

router = APIRouter()


@router.get("/projects/{slug}/gaps")
async def get_gaps(slug: str, request: Request) -> list[dict[str, Any]]:
    await require_slug(request, slug)
    state = ctx(request)

    async with state.db.acquire() as conn:
        scan_id = await conn.fetchval(
            """SELECT id FROM scans WHERE project_id = $1
               ORDER BY started_at DESC NULLS LAST, id DESC LIMIT 1""",
            slug,
        )
        if scan_id is None:
            return []
        rows = await conn.fetch("SELECT * FROM gaps WHERE scan_id = $1 ORDER BY id", scan_id)

    return [
        {
            "id": f"g{row['id']}",
            "item": row["checklist_item"],
            "probe": row["probe"] or "",
            "observed": row["observed"] or "",
            "verdict": row["verdict"] or "",
            "covered": row["covered"],
        }
        for row in rows
    ]
