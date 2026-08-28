"""GET /api/examples: the six seeded, read-only example projects (01-15),
listed for the gallery — title/blurb/demonstrates from the authored
`snag.seed_prompts` metadata, `headline` computed the same way
`snag.report.aggregate_report` computes it for a live project (the first
line of the top break's checker output, or "No breaks found yet." for the
hardened example). No key is required for this endpoint or for any other
read endpoint serving a seeded project (EXAMPLE-01) — see
`snag.api.routers.fixes.get_fixes` for the one read endpoint that would
otherwise gate on funding.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from snag.api.app import ctx
from snag.report import aggregate_report
from snag.seed_prompts import SEED_PROMPTS

router = APIRouter()


@router.get("/examples")
async def list_examples(request: Request) -> list[dict[str, Any]]:
    state = ctx(request)
    out: list[dict[str, Any]] = []
    for n, spec in enumerate(SEED_PROMPTS, start=1):
        report = await aggregate_report(state.db, spec.slug)
        if report is None:
            # Not seeded yet in this environment (e.g. a fresh dev DB before
            # `snag seed` has run) — omit rather than 404 the whole list.
            continue
        out.append(
            {
                "slug": spec.slug,
                "n": n,
                "title": spec.title,
                "blurb": spec.blurb,
                "demonstrates": spec.demonstrates,
                "headline": report["headline"],
                "model": report["model"],
                "scan": report["scan"],
                "coverage": report["coverage"],
            }
        )
    return out
