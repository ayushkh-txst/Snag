"""GET/POST /api/projects/{slug}/fixes[...]: FIX-01/FIX-02 — for each rule
still breaking in the latest scan, an LLM proposes a concrete edit shown as
a diff (`GET .../fixes`, backed by `snag.fixes.ensure_fixes_proposed`), and
applying one (`POST .../fixes/{id}/apply`) reruns only the attacks that
broke it against the edited prompt, reporting before/after (T-14-01: never
a silent rewrite — the diff is only ever written to a new `prompt_versions`
row because the user asked this endpoint to apply it).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from snag.api.app import ctx
from snag.api.deps import get_completions, require_funding, require_slug
from snag.api.ratelimit import guard_owner_scans
from snag.fixes import apply_and_verify, ensure_fixes_proposed
from substrate.llm import Completions

router = APIRouter()

_FIX_ID_PREFIX = "f"


def _parse_fix_id(fix_id: str) -> int | None:
    if not fix_id.startswith(_FIX_ID_PREFIX):
        return None
    rest = fix_id[len(_FIX_ID_PREFIX) :]
    return int(rest) if rest.isdigit() else None


def _fix_out(row: Any) -> dict[str, Any]:
    return {
        "id": f"{_FIX_ID_PREFIX}{row['id']}",
        "ruleId": str(row["rule_id"]) if row["rule_id"] is not None else "",
        "removed": list(row["removed"] or []),
        "added": list(row["added"] or []),
        "rationale": row["rationale"] or "",
        "before": row["before"] or "",
        "after": row["after"] or "",
        "applied": row["applied"],
        "verifyScanId": row["verify_scan_id"],
    }


@router.get("/projects/{slug}/fixes")
async def get_fixes(
    slug: str,
    request: Request,
    _funded: None = Depends(require_funding),
    _rate_limited: None = Depends(guard_owner_scans),
    completions: Completions = Depends(get_completions),  # noqa: B008 - FastAPI DI idiom
) -> list[dict[str, Any]]:
    """Reading this endpoint may itself dispatch a (funded, rate-limited)
    proposer call per still-breaking rule that has none on file yet
    (`ensure_fixes_proposed`) — hence the same `require_funding`/
    `guard_owner_scans` guards `POST /scans` uses, even though this is a
    GET. A rule already covered for the latest scan costs nothing on a
    repeat call."""
    await require_slug(request, slug)
    state = ctx(request)
    rows = await ensure_fixes_proposed(state.db, slug, completions=completions)
    return [_fix_out(r) for r in rows]


class ApplyFixResponse(BaseModel):
    verify_scan_id: int
    before_breaks: int
    after_breaks: int


@router.post("/projects/{slug}/fixes/{fix_id}/apply", response_model=ApplyFixResponse)
async def apply_fix(
    slug: str,
    fix_id: str,
    request: Request,
    _funded: None = Depends(require_funding),
    _rate_limited: None = Depends(guard_owner_scans),
    completions: Completions = Depends(get_completions),  # noqa: B008 - FastAPI DI idiom
) -> ApplyFixResponse:
    await require_slug(request, slug)
    state = ctx(request)
    db_id = _parse_fix_id(fix_id)
    if db_id is None:
        raise HTTPException(status_code=404, detail=f"no such fix: {fix_id}")

    result = await apply_and_verify(state.db, slug=slug, fix_id=db_id, completions=completions)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no such fix: {fix_id}")
    return ApplyFixResponse(
        verify_scan_id=result.verify_scan_id,
        before_breaks=result.before_breaks,
        after_breaks=result.after_breaks,
    )
