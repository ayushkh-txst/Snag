"""POST/GET/PATCH /api/projects/{slug}/surfaces: generate, list, and edit
the surface map (SURFACE-01/02/03) produced by `snag.surfaces`.

`POST` (re)generates the map from the project's latest `prompt_versions`
row and persists it — wiping and re-inserting rather than diffing, since
the map is meant to be regenerated whenever the prompt/tools change and
re-confirmed from scratch. `GET` returns the current map in the UI's
`Surface` shape (`src/data/types.ts`) plus `confirmed`. `PATCH` edits one
row's `userControlled`/`confirmed`/`tests`/`note`.

Contract for 01-09 (the runner): a scan reads only surfaces where BOTH
`confirmed` and `user_controlled` are true — everything else is excluded
from instantiation regardless of `risk`.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from snag.api.app import ctx
from snag.api.deps import require_mutable_slug, require_slug
from snag.surfaces import build_surface_map

log = structlog.get_logger(__name__)
router = APIRouter()

# Whitelisted, fixed column names only — never built from request input —
# so interpolating them into the UPDATE's SET clause below carries no
# injection risk; every value is still bound as a parameter.
_PATCHABLE_COLUMNS = ("user_controlled", "confirmed", "tests", "note")


def _row_to_ui(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "path": row["path"],
        "kind": row["kind"],
        "source": row["source"] or "",
        "risk": row["risk"] or "medium",
        "tests": row["tests"],
        "userControlled": row["user_controlled"],
        "confirmed": row["confirmed"],
        "note": row["note"] or "",
    }


class SurfacesResponse(BaseModel):
    surfaces: list[dict[str, Any]]


class PatchSurfaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_controlled: bool | None = None
    confirmed: bool | None = None
    tests: int | None = None
    note: str | None = None


async def _latest_prompt_version(
    conn: asyncpg.Connection[Any], slug: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """SELECT * FROM prompt_versions WHERE project_id = $1
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        slug,
    )


@router.post("/projects/{slug}/surfaces")
async def generate_surfaces(slug: str, request: Request) -> SurfacesResponse:
    await require_mutable_slug(request, slug)  # T-15-01
    state = ctx(request)

    async with state.db.acquire() as conn:
        prompt_version = await _latest_prompt_version(conn, slug)

    prompt_text = prompt_version["full_text"] if prompt_version else ""
    tools_json = prompt_version["tools_json"] if prompt_version else None
    specs = build_surface_map(prompt_text, tools_json)

    async with state.db.acquire() as conn, conn.transaction():
        # Regenerating replaces the whole map — it's meant to be re-run (and
        # re-confirmed) whenever the prompt or tools change, not merged with
        # whatever confirmations existed for a previous prompt version.
        await conn.execute("DELETE FROM surfaces WHERE project_id = $1", slug)
        rows = [
            await conn.fetchrow(
                """INSERT INTO surfaces
                       (project_id, kind, path, source, risk, user_controlled, note, tests)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   RETURNING *""",
                slug,
                spec.kind,
                spec.path,
                spec.source,
                spec.risk,
                spec.user_controlled,
                spec.note,
                spec.tests,
            )
            for spec in specs
        ]

    log.info("surfaces.generated", slug=slug, count=len(rows))
    return SurfacesResponse(surfaces=[_row_to_ui(r) for r in rows if r is not None])


@router.get("/projects/{slug}/surfaces")
async def get_surfaces(slug: str, request: Request) -> SurfacesResponse:
    await require_slug(request, slug)
    state = ctx(request)
    async with state.db.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM surfaces WHERE project_id = $1 ORDER BY id", slug)
    return SurfacesResponse(surfaces=[_row_to_ui(row) for row in rows])


@router.patch("/projects/{slug}/surfaces/{surface_id}")
async def patch_surface(
    slug: str, surface_id: int, body: PatchSurfaceRequest, request: Request
) -> dict[str, Any]:
    await require_mutable_slug(request, slug)  # T-15-01
    state = ctx(request)

    values: dict[str, Any] = {
        "user_controlled": body.user_controlled,
        "confirmed": body.confirmed,
        "tests": body.tests,
        "note": body.note,
    }
    updates = {col: values[col] for col in _PATCHABLE_COLUMNS if values[col] is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    set_clause = ", ".join(f"{col} = ${i + 3}" for i, col in enumerate(updates))
    params: list[Any] = [slug, surface_id, *updates.values()]

    async with state.db.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE surfaces SET {set_clause} WHERE project_id = $1 AND id = $2 RETURNING *",  # noqa: S608 - set_clause built only from _PATCHABLE_COLUMNS, values bound as params
            *params,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such surface: {surface_id}")

    return _row_to_ui(row)
