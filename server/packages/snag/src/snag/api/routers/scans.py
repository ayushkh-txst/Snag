"""POST/GET/estimate /api/scans: the real substrate.queue-backed scan runner
(01-09), replacing the tracer's inline synchronous scan (01-01 Task 3).

`POST /scans` never dispatches a model call itself — it validates, inserts
one `scans` row, and enqueues exactly one job via `snag.runner.start_scan`.
A `snag work` worker (or `runner.run_scan` called directly, e.g. by tests)
claims that job and runs the real attack matrix.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from snag.api.app import ctx
from snag.api.deps import require_funding, require_mutable_slug, require_slug, validate_model
from snag.api.ratelimit import guard_owner_scans
from snag.api.sse import scan_event_stream
from snag.cost import estimate_scan_cost
from snag.runner import (
    DEFAULT_CALL_CAP,
    DEFAULT_SPEND_CAP,
    MODE_PRESETS,
    VALID_SURFACE_CATEGORIES,
    ScanStartConfig,
    start_scan,
)

log = structlog.get_logger(__name__)
router = APIRouter()

ScanMode = Literal["quick", "standard", "deep", "custom"]

_ESTIMATE_AVG_INPUT_TOKENS = 800
_ESTIMATE_AVG_OUTPUT_TOKENS = 400
_ESTIMATE_CALLS_PER_ATTACK = 1


def _require_custom_mode_shape(mode: str, surfaces: list[str] | None, repeats: int | None) -> None:
    """Shared by `StartScanRequest`/`EstimateRequest`'s own `model_validator`s:
    `mode == "custom"` must always carry an explicit, non-empty surfaces
    list and a repeats count (SCAN-02) — the three preset modes never need
    either from the client."""
    if mode == "custom":
        if not surfaces:
            raise ValueError("custom mode requires a non-empty surfaces list")
        if repeats is None:
            raise ValueError("custom mode requires repeats (1..10)")


class StartScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    mode: ScanMode = "standard"
    surfaces: list[str] | None = None
    """Required (non-empty) when `mode == "custom"`; ignored for the three
    preset modes, which always use their own documented surfaces (SCAN-02)."""

    repeats: int | None = Field(default=None, ge=1, le=10)
    """Required when `mode == "custom"`; ignored for the three preset modes.
    Bounded 1..10 by this field regardless of mode (SCAN-02)."""

    model: str | None = None
    call_cap: int = Field(default=DEFAULT_CALL_CAP, ge=1)
    spend_cap: Decimal = Field(default=DEFAULT_SPEND_CAP, gt=0)

    @model_validator(mode="after")
    def _validate_custom_mode_shape(self) -> StartScanRequest:
        _require_custom_mode_shape(self.mode, self.surfaces, self.repeats)
        return self


class StartScanResponse(BaseModel):
    scan_id: int


class ScanRecordResponse(BaseModel):
    id: int
    status: str
    mode: str
    repeats: int
    surfaces: list[str]
    models: list[str]
    call_count: int
    cost: Decimal
    call_cap: int | None
    spend_cap: Decimal | None
    skipped_count: int
    attacks_done: int
    breaks_found: int
    indicative_only: bool
    """SCAN-02: N=1 can't give you a rate, only a single data point — the
    report (and any client rendering this row) should say so."""


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    mode: ScanMode = "standard"
    surfaces: list[str] | None = None
    repeats: int | None = Field(default=None, ge=1, le=10)
    model: str | None = None

    @model_validator(mode="after")
    def _validate_custom_mode_shape(self) -> EstimateRequest:
        _require_custom_mode_shape(self.mode, self.surfaces, self.repeats)
        return self


class EstimateResponse(BaseModel):
    estimated_cost_usd: Decimal
    estimated_calls: int
    unknown_pricing: bool


def _resolve_mode_config(
    mode: str, surfaces: list[str] | None, repeats: int | None
) -> tuple[list[str], int]:
    """Server-side derivation of (surfaces, repeats) — the client's own
    `surfaces`/`repeats` are honoured ONLY for `mode == "custom"`. A client
    can't under-cut a "quick" scan's documented, cheap shape by also sending
    a bigger `surfaces`/`repeats` alongside it."""
    if mode == "custom":
        assert surfaces is not None and repeats is not None  # enforced by the request model
        unknown = sorted(set(surfaces) - VALID_SURFACE_CATEGORIES)
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown surface categories: {unknown}")
        return surfaces, repeats
    preset_surfaces, preset_repeats = MODE_PRESETS[mode]
    return list(preset_surfaces), preset_repeats


@router.post("/scans", response_model=StartScanResponse)
async def create_scan(
    body: StartScanRequest,
    request: Request,
    _funded: None = Depends(require_funding),
    _rate_limited: None = Depends(guard_owner_scans),
) -> StartScanResponse:
    project = await require_mutable_slug(request, body.slug)  # T-15-01
    state = ctx(request)

    model = body.model or project["model"]
    # KEY-03: revalidate before this scan is ever queued, even though the
    # project's model was already checked at creation — guards against
    # ACCEPTED_MODELS narrowing between project creation and scan start.
    validate_model(model)

    surfaces, repeats = _resolve_mode_config(body.mode, body.surfaces, body.repeats)

    async with state.db.acquire() as conn:
        prompt_version = await conn.fetchrow(
            """SELECT * FROM prompt_versions WHERE project_id = $1
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            body.slug,
        )

    scan_id = await start_scan(
        state.db,
        slug=body.slug,
        config=ScanStartConfig(
            mode=body.mode,
            surfaces=surfaces,
            repeats=repeats,
            call_cap=body.call_cap,
            spend_cap=body.spend_cap,
        ),
        model=model,
        prompt_version_id=prompt_version["id"] if prompt_version else None,
    )
    log.info("scan.enqueued", slug=body.slug, scan_id=scan_id, mode=body.mode, model=model)
    return StartScanResponse(scan_id=scan_id)


class ActiveScanResponse(BaseModel):
    """The scan a project currently has in flight, if any. `scanId` is null
    when nothing is running."""

    scanId: int | None = None
    status: str | None = None


@router.get("/projects/{slug}/active-scan", response_model=ActiveScanResponse)
async def get_active_scan(slug: str, request: Request) -> ActiveScanResponse:
    """Which scan, if any, this project has in flight.

    Resuming a run used to depend on a localStorage key written by the config
    screen, so a typed URL, a second tab, or another device had nothing to
    reconnect to and the scanning screen reported a finished run while the
    worker was still going. The project row is the durable answer, and it is
    the same one for every client.

    `pending` counts as active on purpose: a queued job not yet claimed is
    exactly the window the UI spends saying "queuing attacks", and it is the
    one most in need of being resumable."""
    await require_slug(request, slug)
    state = ctx(request)
    async with state.db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, status FROM scans
                   WHERE project_id = $1 AND status NOT IN ('completed', 'stopped_at_cap', 'failed')
                   ORDER BY id DESC LIMIT 1""",
            slug,
        )
    if row is None:
        return ActiveScanResponse()
    return ActiveScanResponse(scanId=row["id"], status=row["status"])


@router.get("/scans/{scan_id}", response_model=ScanRecordResponse)
async def get_scan(scan_id: int, request: Request) -> ScanRecordResponse:
    state = ctx(request)
    async with state.db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM scans WHERE id = $1", scan_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such scan: {scan_id}")
    return ScanRecordResponse(
        id=row["id"],
        status=row["status"],
        mode=row["mode"],
        repeats=row["repeats"],
        surfaces=list(row["surfaces"] or []),
        models=list(row["models"] or []),
        call_count=row["call_count"],
        cost=row["cost"],
        call_cap=row["call_cap"],
        spend_cap=row["spend_cap"],
        skipped_count=row["skipped_count"],
        attacks_done=row["attacks_done"],
        breaks_found=row["breaks_found"],
        indicative_only=row["repeats"] == 1,
    )


@router.get("/scans/{scan_id}/stream")
async def stream_scan(
    scan_id: int,
    request: Request,
    since_seq: int = Query(default=0, ge=0),
) -> StreamingResponse:
    """PROGRESS-01: live scan progress over SSE, resumable on reconnect via
    `?since_seq=`. Same lookup shape as `GET /scans/{scan_id}` — a 404 for an
    unknown scan before ever opening the stream, rather than a stream that
    opens and then silently hangs."""
    state = ctx(request)
    async with state.db.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM scans WHERE id = $1", scan_id)
    if not exists:
        raise HTTPException(status_code=404, detail=f"no such scan: {scan_id}")
    return StreamingResponse(
        scan_event_stream(state.db, scan_id, since_seq=since_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/scans/estimate", response_model=EstimateResponse)
async def estimate(body: EstimateRequest, request: Request) -> EstimateResponse:
    project = await require_slug(request, body.slug)
    model = body.model or project["model"]
    validate_model(model)

    surfaces, repeats = _resolve_mode_config(body.mode, body.surfaces, body.repeats)
    calls = _estimate_call_count(surfaces, repeats)
    cost, unknown_pricing = await estimate_scan_cost(
        model,
        calls=calls,
        avg_input_tokens=_ESTIMATE_AVG_INPUT_TOKENS,
        avg_output_tokens=_ESTIMATE_AVG_OUTPUT_TOKENS,
    )
    return EstimateResponse(
        estimated_cost_usd=cost, estimated_calls=calls, unknown_pricing=unknown_pricing
    )


def _estimate_call_count(surfaces: list[str], repeats: int) -> int:
    """A simple, explainable upper bound: one call per (surface category,
    repeat) — deliberately rough, same spirit as ScanConfig.tsx's own
    client-side estimate. A precise count would require loading and
    instantiating the real attack matrix just to show a number before the
    user has even confirmed surfaces."""
    per_surface_techniques = 8
    return max(len(surfaces), 1) * per_surface_techniques * repeats * _ESTIMATE_CALLS_PER_ATTACK
