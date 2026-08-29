"""POST /api/projects/{slug}/compare: FIX-04 — the same configuration run
across several models for side-by-side comparison, nearly free through
OpenRouter (project-3-spec.md §15). This router OWNS the multi-model
fan-out: it calls `snag.runner.start_scan` (the single-scan start site
01-09 built, and the same helper `POST /api/scans` calls) once per
requested model, so N models means N ordinary single-model scans, each
under its own pre-dispatch budget caps (T-14-02) — never a shared,
uncapped multi-model dispatch loop of its own.

No dedicated "group" endpoint: the returned `scan_id`s are the read handle
a comparison view needs, exactly as project-3-spec.md's own compare
walkthrough allows ("a companion endpoint, or the returned ids") — each
one already has its own `GET /api/scans/{scan_id}` (01-09).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from snag.api.app import ctx
from snag.api.deps import require_funding, require_slug, validate_model
from snag.api.ratelimit import guard_owner_scans
from snag.runner import (
    DEFAULT_CALL_CAP,
    DEFAULT_SPEND_CAP,
    MODE_PRESETS,
    VALID_SURFACE_CATEGORIES,
    ScanStartConfig,
    start_scan,
)

router = APIRouter()

CompareMode = Literal["quick", "standard", "deep", "custom"]


def _require_custom_mode_shape(mode: str, surfaces: list[str] | None, repeats: int | None) -> None:
    """Duplicated from `snag.api.routers.scans` rather than imported: that
    module's own version is private (leading underscore) and this is the
    only other place that needs the same "custom mode must carry an
    explicit shape" rule — same reasoning `snag.runner` gives for its own
    small duplicated helpers."""
    if mode == "custom":
        if not surfaces:
            raise ValueError("custom mode requires a non-empty surfaces list")
        if repeats is None:
            raise ValueError("custom mode requires repeats (1..10)")


def _resolve_mode_config(
    mode: str, surfaces: list[str] | None, repeats: int | None
) -> tuple[list[str], int]:
    """Server-side derivation of (surfaces, repeats) for the compare fan-out
    — same rule `POST /scans` enforces: a client can't smuggle a bigger
    shape past a preset mode's documented, cheap surfaces+repeats."""
    if mode == "custom":
        assert surfaces is not None and repeats is not None  # enforced by CompareRequest
        unknown = sorted(set(surfaces) - VALID_SURFACE_CATEGORIES)
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown surface categories: {unknown}")
        return surfaces, repeats
    preset_surfaces, preset_repeats = MODE_PRESETS[mode]
    return list(preset_surfaces), preset_repeats


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: CompareMode = "standard"
    surfaces: list[str] | None = None
    repeats: int | None = Field(default=None, ge=1, le=10)
    models: list[str] = Field(..., min_length=1)
    """The models to compare — each dispatched to via its OWN single-model
    scan (FIX-04). Every entry must be in `ACCEPTED_MODELS` (KEY-03),
    checked before any of them starts."""

    call_cap: int = Field(default=DEFAULT_CALL_CAP, ge=1)
    spend_cap: Decimal = Field(default=DEFAULT_SPEND_CAP, gt=0)

    @model_validator(mode="after")
    def _validate_custom_mode_shape(self) -> CompareRequest:
        _require_custom_mode_shape(self.mode, self.surfaces, self.repeats)
        return self


class CompareRunOut(BaseModel):
    model: str
    scan_id: int


class CompareResponse(BaseModel):
    scans: list[CompareRunOut]


@router.post("/projects/{slug}/compare", response_model=CompareResponse)
async def compare(
    slug: str,
    body: CompareRequest,
    request: Request,
    _funded: None = Depends(require_funding),
    _rate_limited: None = Depends(guard_owner_scans),
) -> CompareResponse:
    await require_slug(request, slug)
    state = ctx(request)

    for model in body.models:
        validate_model(model)  # KEY-03: every model checked before ANY scan starts

    surfaces, repeats = _resolve_mode_config(body.mode, body.surfaces, body.repeats)

    async with state.db.acquire() as conn:
        prompt_version = await conn.fetchrow(
            """SELECT * FROM prompt_versions WHERE project_id = $1
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            slug,
        )
    prompt_version_id = prompt_version["id"] if prompt_version else None

    runs: list[CompareRunOut] = []
    for model in body.models:
        scan_id = await start_scan(
            state.db,
            slug=slug,
            config=ScanStartConfig(
                mode=body.mode,
                surfaces=surfaces,
                repeats=repeats,
                call_cap=body.call_cap,
                spend_cap=body.spend_cap,
            ),
            model=model,
            prompt_version_id=prompt_version_id,
        )
        runs.append(CompareRunOut(model=model, scan_id=scan_id))

    return CompareResponse(scans=runs)
