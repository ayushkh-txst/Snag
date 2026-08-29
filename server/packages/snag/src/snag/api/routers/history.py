"""GET /api/projects/{slug}/history: every saved scan for a project, in the
`HistoryRun` shape (src/data/types.ts), with fixed/new/unchanged deltas
against the immediately preceding scan (FIX-03). "Every scan is saved" —
`snag.runner.start_scan` inserts one `scans` row per scan and nothing here
ever deletes one, so this endpoint is a straight read over that history.
A brand new failure is called out loudly via the additive `newAttackKeys`
field, not just folded into an `added` count a caller has to notice.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Request

from snag.api.app import ctx
from snag.api.deps import require_slug
from snag.fixes import Delta, scan_delta
from substrate.db import Database

router = APIRouter()


def _attack_key(row: Any) -> str:
    return f"{row['rule_id']}:{row['surface_id']}:{row['technique_id']}"


async def _build_history(db: Database, slug: str) -> list[dict[str, Any]]:
    async with db.acquire() as conn:
        scans = await conn.fetch(
            "SELECT * FROM scans WHERE project_id = $1 ORDER BY started_at NULLS LAST, id",
            slug,
        )
        run_rows = await conn.fetch(
            """SELECT ar.* FROM attack_runs ar JOIN scans s ON s.id = ar.scan_id
               WHERE s.project_id = $1""",
            slug,
        )

    # Project-wide false-positive exclusion (CHECK-06/mirrors snag.report):
    # a break marked a false positive against ANY scan still excludes every
    # future rescan's break of the same identity from the delta.
    excluded = {_attack_key(r) for r in run_rows if r["false_positive"]}

    runs_by_scan: dict[int, list[Any]] = defaultdict(list)
    for run in run_rows:
        runs_by_scan[run["scan_id"]].append(run)

    history: list[dict[str, Any]] = []
    prev_broken: set[str] | None = None
    for i, scan in enumerate(scans):
        runs = runs_by_scan.get(scan["id"], [])
        broken = {
            _attack_key(r) for r in runs if not r["passed"] and _attack_key(r) not in excluded
        }

        if prev_broken is None:
            # No predecessor to diff against — every current break is new,
            # matching snag.report's own "first scan" convention.
            delta = Delta(fixed=[], new=sorted(broken), unchanged=[])
        else:
            delta = scan_delta(prev_broken, broken)

        date = scan["finished_at"] or scan["started_at"]
        label = scan["label"] or ("first scan" if i == 0 else f"rescan {i}")
        history.append(
            {
                "id": f"h{scan['id']}",
                "date": date.isoformat() if date is not None else "",
                "label": label,
                "mode": scan["mode"],
                "breaks": len(broken),
                "fixed": len(delta.fixed),
                "added": len(delta.new),
                "unchanged": len(delta.unchanged),
                "calls": scan["call_count"],
                "cost": float(scan["cost"]),
                # Additive (not part of HistoryRun in src/data/types.ts) —
                # FIX-03's "new failures are called out loudly": the exact
                # identities, not just a count a caller could scroll past.
                "newAttackKeys": delta.new,
                "fixedAttackKeys": delta.fixed,
            }
        )
        prev_broken = broken
    return history


@router.get("/projects/{slug}/history")
async def get_history(slug: str, request: Request) -> list[dict[str, Any]]:
    await require_slug(request, slug)
    state = ctx(request)
    return await _build_history(state.db, slug)
