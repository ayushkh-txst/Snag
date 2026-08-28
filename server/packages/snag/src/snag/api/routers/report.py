"""GET /api/projects/{slug}/report: aggregate real rules + attack_runs into
the UI's Example-shaped JSON (src/data/types.ts) — the same shape the six
fixture reports use, now built from stored rows instead of hand-written data.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Request

from snag.api.app import ctx
from snag.api.deps import require_slug

router = APIRouter()


def _duration(started: datetime | None, finished: datetime | None) -> str:
    if started is None or finished is None:
        return "-"
    seconds = max((finished - started).total_seconds(), 0.0)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest:02d}s"


def _tools_text(prompt_version: asyncpg.Record | None) -> str:
    if prompt_version is None or prompt_version["tools_json"] is None:
        return ""
    return json.dumps(prompt_version["tools_json"])


@router.get("/projects/{slug}/report")
async def get_report(slug: str, request: Request) -> dict[str, Any]:
    project = await require_slug(request, slug)
    state = ctx(request)

    async with state.db.acquire() as conn:
        prompt_version = await conn.fetchrow(
            """SELECT * FROM prompt_versions WHERE project_id = $1
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            slug,
        )
        rule_rows = await conn.fetch("SELECT * FROM rules WHERE project_id = $1 ORDER BY id", slug)
        surface_rows = await conn.fetch(
            "SELECT * FROM surfaces WHERE project_id = $1 ORDER BY id", slug
        )
        run_rows = await conn.fetch(
            """SELECT ar.* FROM attack_runs ar
               JOIN scans s ON s.id = ar.scan_id
               WHERE s.project_id = $1
               ORDER BY ar.id""",
            slug,
        )
        scan_row = await conn.fetchrow(
            """SELECT * FROM scans WHERE project_id = $1
               ORDER BY started_at DESC NULLS LAST, id DESC LIMIT 1""",
            slug,
        )

    attacks_by_rule: dict[int, int] = {}
    breaks_by_rule: dict[int, int] = {}
    attacks_by_surface: dict[int, int] = {}
    for run in run_rows:
        rid, sid = run["rule_id"], run["surface_id"]
        if rid is not None:
            attacks_by_rule[rid] = attacks_by_rule.get(rid, 0) + 1
            if not run["passed"]:
                breaks_by_rule[rid] = breaks_by_rule.get(rid, 0) + 1
        if sid is not None:
            attacks_by_surface[sid] = attacks_by_surface.get(sid, 0) + 1

    rules = []
    for row in rule_rows:
        rid = row["id"]
        entry: dict[str, Any] = {
            "id": str(rid),
            "text": row["text"],
            "category": row["category"],
            "direction": row["direction"],
            "sourceLine": row["source_line"] or "",
            "checkerType": row["checker_type"],
            "checkerConfig": row["checker_config"] or {},
            "testable": row["testable"],
            "confidence": float(row["confidence"] or 0.0),
            "attacks": attacks_by_rule.get(rid, 0),
            "breaks": breaks_by_rule.get(rid, 0),
        }
        if not row["testable"]:
            entry["untestableReason"] = (
                "Snag's extractor could not derive a mechanical checker for this rule."
            )
        rules.append(entry)

    surfaces = [
        {
            "id": str(row["id"]),
            "path": row["path"],
            "kind": row["kind"],
            "source": row["source"] or "",
            "risk": row["risk"] or "medium",
            "tests": attacks_by_surface.get(row["id"], 0),
            "userControlled": row["user_controlled"],
            "note": row["note"] or "",
        }
        for row in surface_rows
    ]

    breaks = [
        {
            "id": f"b{run['id']}",
            "ruleId": str(run["rule_id"]),
            "surfaceId": str(run["surface_id"]),
            "techniqueId": run["technique_id"],
            "family": run["family"] or "",
            "hits": 1,
            "repeats": 1,
            "turns": run["conversation"] or [],
            "checkerOutput": run["checker_output"] or "",
            "falsePositive": run["false_positive"],
        }
        for run in run_rows
        if not run["passed"]
    ]

    calls = scan_row["call_count"] if scan_row else 0
    cost = float(scan_row["cost"]) if scan_row and scan_row["cost"] is not None else 0.0
    mode = (scan_row["mode"] if scan_row else None) or "-"
    repeats = scan_row["repeats"] if scan_row else 0
    duration = _duration(
        scan_row["started_at"] if scan_row else None, scan_row["finished_at"] if scan_row else None
    )
    breaks_found = sum(1 for r in run_rows if not r["passed"])

    # Report.tsx unconditionally reads history[0] — always give it one row,
    # even before any scan has run, so the existing JSX never crashes on
    # real data (Rule 2: the UI has no optional-chaining guard here).
    if scan_row is not None:
        history_date = (scan_row["finished_at"] or scan_row["started_at"] or project["created_at"])
        history = [
            {
                "id": f"h{scan_row['id']}",
                "date": history_date.isoformat(),
                "label": "first scan",
                "mode": mode,
                "breaks": breaks_found,
                "fixed": 0,
                "added": breaks_found,
                "unchanged": 0,
                "calls": calls,
                "cost": cost,
            }
        ]
    else:
        history = [
            {
                "id": "h0",
                "date": project["created_at"].isoformat(),
                "label": "not yet scanned",
                "mode": "-",
                "breaks": 0,
                "fixed": 0,
                "added": 0,
                "unchanged": 0,
                "calls": 0,
                "cost": 0,
            }
        ]

    top_break = breaks[0] if breaks else None
    broke_line = top_break["checkerOutput"].splitlines()[0] if top_break else ""
    walkthrough = {"intent": "", "broke": broke_line, "why": "", "fix": ""}

    return {
        "slug": project["id"],
        "n": 0,
        "title": project["name"] or project["id"],
        "blurb": "",
        "demonstrates": "",
        "headline": broke_line or "No breaks found yet.",
        "model": project["model"],
        "systemPrompt": prompt_version["full_text"] if prompt_version else "",
        "tools": _tools_text(prompt_version),
        "rules": rules,
        "surfaces": surfaces,
        "questions": [],
        "breaks": breaks,
        "gaps": [],
        "fixes": [],
        "history": history,
        "scan": {
            "mode": mode,
            "repeats": repeats,
            "calls": calls,
            "cost": cost,
            "duration": duration,
        },
        "walkthrough": walkthrough,
    }
