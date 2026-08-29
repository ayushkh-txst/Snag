"""Aggregate real `attack_runs` into the exact `Example` shape the finished
UI reads (`src/data/types.ts`), replacing the tracer's minimal report
(01-01) and the UI's three data-inventing helpers
(`runOutcomes`/`runTurns`/`checkerOutputFor` in `src/data/index.ts`) with
real per-run fields.

Key idea: the DB stores one `attack_runs` ROW per (rule, surface, technique,
repeat) — one row per actual dispatch. The UI's `Break` type is one entry
per (rule, surface, technique), carrying `hits`/`repeats` counts plus a
`variants[]` array with one real `{broke, reply, evidence}` per repeat
(README: "repeats of the same attack differ only in what the model
replied, so runs are stored as alternative final turns rather than whole
duplicate conversations"). `_build_breaks` is the collapse from the
former into the latter.

Scoping: `rules[].attacks/breaks`, `surfaces[].tests`, `breaks[]`, and
`bySurface` describe the LATEST scan only (matching the `scan` meta block
this payload also carries) — so `sum(rule.attacks) <= scan.calls` holds
even after a project has been scanned more than once. False-positive
EXCLUSION, by contrast, is computed from every `attack_runs` row this
project has EVER produced for a given (rule, surface, technique) identity,
regardless of which scan it's in — a mark made against an old scan's break
must still suppress a brand new scan's break of the same identity
(CHECK-06's "excluded ... from every future rescan"), and `runner.py`
itself is out of scope for this plan, so a freshly-inserted rescan row
always starts with its OWN `false_positive = false` — this module, not the
row, is what remembers the exclusion.

Honest coverage (01-18): a stored run with `applicable = false` tested
nothing — a canary checker handed an attack that planted no canary, or a
reply that came back empty/truncated — and `_counted` drops it before ANY
rate here is computed. It is neither a break nor an attack the rule
survived, so it inflates neither `rule.breaks` nor `rule.attacks`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, cast

import asyncpg
import structlog

from substrate.db import Database

log = structlog.get_logger(__name__)

# A rule this plan's extractor marked untestable never gets a checker — this
# is the one and only reason (`snag.extract` never records `checkerType ==
# "none"` for any other cause).
UNTESTABLE_REASON = "Snag's extractor could not derive a mechanical checker for this rule."

# `Break.id`/the `{break_id}` path param are both `f"b{attack_run.id}"` —
# the smallest attack_run id in a (rule, surface, technique) group, chosen
# deterministically so the same group always resolves to the same id
# across repeated report reads.
_BREAK_ID_PREFIX = "b"

BreakKey = tuple[int | None, int | None, str]
"""(rule_id, surface_id, technique_id) — the identity a `Break` groups
`attack_runs` rows by, and the identity false-positive exclusion is keyed
on. Deliberately excludes `scan_id`: exclusion must survive a rescan."""


def _break_key(run: asyncpg.Record) -> BreakKey:
    return (run["rule_id"], run["surface_id"], run["technique_id"])


def _counted(runs: list[asyncpg.Record]) -> list[asyncpg.Record]:
    """Only the runs that actually TESTED something (01-18). A run stored
    with `applicable = false` — a canary checker handed an attack that
    planted no canary, a reply that came back empty or truncated — exercised
    nothing, so it belongs in neither the numerator nor the denominator of
    any rate this module computes: not `rule.attacks`, not `rule.breaks`,
    not `surface.tests`, not `bySurface`, not a `Break`'s `repeats`. Rows
    stay in the DB (the transcript is real and inspectable); they are
    simply not evidence about whether a rule held."""
    return [r for r in runs if r["applicable"]]


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


def _last_assistant_text(conversation: list[dict[str, Any]] | None) -> str:
    """The model's own last reply. A `forged` turn is the prefill attack's
    fabricated assistant opener (`runner._execute_attack`) — attacker text,
    never a reply, so it can never be surfaced as one."""
    for turn in reversed(conversation or []):
        if turn.get("role") == "assistant" and not turn.get("forged"):
            return str(turn.get("content", ""))
    return ""


def _variant_for_run(run: asyncpg.Record) -> dict[str, Any]:
    """One real repeat -> one `Break.variants[]` entry. `reply`/`evidence`
    match the `{broke, reply, evidence}` shape `src/data/types.ts` already
    declares; `turns`/`checkerOutput`/`repeatIndex`/`runId` are additive —
    BREAK-02 asks for "real transcript + checker output" for every one of
    the N repeats, which a bare final-reply string can't carry, and JSON
    callers ignore keys they don't know about. 01-16 is free to consume
    either the narrow or the wide shape when it retires the UI's three
    faking helpers."""
    broke = not run["passed"]
    variant: dict[str, Any] = {
        "broke": broke,
        "reply": _last_assistant_text(run["conversation"]),
        "turns": run["conversation"] or [],
        "checkerOutput": run["checker_output"] or "",
        "repeatIndex": run["repeat_index"],
        "runId": run["id"],
    }
    if broke and run["evidence"]:
        variant["evidence"] = run["evidence"]
    return variant


def _break_id(run_ids: list[int]) -> str:
    return f"{_BREAK_ID_PREFIX}{min(run_ids)}"


def _parse_break_id(break_id: str) -> int | None:
    if not break_id.startswith(_BREAK_ID_PREFIX):
        return None
    rest = break_id[len(_BREAK_ID_PREFIX) :]
    return int(rest) if rest.isdigit() else None


def _build_break_entry(
    key: BreakKey, runs: list[asyncpg.Record], *, excluded: bool
) -> dict[str, Any]:
    runs_sorted = sorted(runs, key=lambda r: (r["repeat_index"], r["id"]))
    hits = sum(1 for r in runs_sorted if not r["passed"])
    broken_runs = [r for r in runs_sorted if not r["passed"]]
    representative = broken_runs[0] if broken_runs else runs_sorted[0]
    rule_id, surface_id, technique_id = key
    return {
        "id": _break_id([r["id"] for r in runs_sorted]),
        "ruleId": str(rule_id) if rule_id is not None else "",
        "surfaceId": str(surface_id) if surface_id is not None else "",
        "techniqueId": technique_id,
        "family": representative["family"] or "",
        "hits": hits,
        "repeats": len(runs_sorted),
        "turns": representative["conversation"] or [],
        "checkerOutput": representative["checker_output"] or "",
        "falsePositive": excluded,
        "variants": [_variant_for_run(r) for r in runs_sorted],
    }


def _build_breaks(
    run_rows: list[asyncpg.Record], excluded_keys: set[BreakKey]
) -> list[dict[str, Any]]:
    """Collapse one scan's `attack_runs` rows into `Break` entries — one per
    (rule, surface, technique) group that has at least one failing repeat.
    A group with zero failures never broke anything and is not a `Break`
    (README: "every rule with breaks > 0 has at least one stored attack
    run" — the converse also holds: no failing run, no `Break` entry)."""
    groups: dict[BreakKey, list[asyncpg.Record]] = defaultdict(list)
    for run in run_rows:
        groups[_break_key(run)].append(run)

    breaks = [
        _build_break_entry(key, runs, excluded=key in excluded_keys)
        for key, runs in groups.items()
        if any(not r["passed"] for r in runs)
    ]
    breaks.sort(key=lambda b: (-b["hits"], b["id"]))
    return breaks


async def _fetch_all_runs(conn: Any, slug: str) -> list[asyncpg.Record]:
    rows = await conn.fetch(
        """SELECT ar.* FROM attack_runs ar
           JOIN scans s ON s.id = ar.scan_id
           WHERE s.project_id = $1
           ORDER BY ar.rule_id, ar.surface_id, ar.technique_id, ar.repeat_index, ar.id""",
        slug,
    )
    return cast(list[asyncpg.Record], rows)


async def aggregate_report(db: Database, slug: str) -> dict[str, Any] | None:
    """The full `Example`-shaped payload for `slug`, or `None` if no such
    project exists (the router turns that into a 404 via `require_slug`
    before this is ever called, but this stays self-contained/unit-testable
    without a request)."""
    async with db.acquire() as conn:
        project = await conn.fetchrow("SELECT * FROM projects WHERE id = $1", slug)
        if project is None:
            return None
        prompt_version = await conn.fetchrow(
            """SELECT * FROM prompt_versions WHERE project_id = $1
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            slug,
        )
        rule_rows = await conn.fetch("SELECT * FROM rules WHERE project_id = $1 ORDER BY id", slug)
        surface_rows = await conn.fetch(
            "SELECT * FROM surfaces WHERE project_id = $1 ORDER BY id", slug
        )
        all_run_rows = await _fetch_all_runs(conn, slug)
        scan_row = await conn.fetchrow(
            """SELECT * FROM scans WHERE project_id = $1
               ORDER BY started_at DESC NULLS LAST, id DESC LIMIT 1""",
            slug,
        )

    # False-positive exclusion is project-wide across every scan ever run
    # (CHECK-06) — everything else below is scoped to the latest scan only.
    excluded_keys = {_break_key(r) for r in all_run_rows if r["false_positive"]}
    latest_scan_id = scan_row["id"] if scan_row is not None else None
    run_rows = (
        _counted([r for r in all_run_rows if r["scan_id"] == latest_scan_id])
        if latest_scan_id
        else []
    )

    breaks = _build_breaks(run_rows, excluded_keys)

    attacks_by_rule: dict[int, int] = defaultdict(int)
    attacks_by_surface: dict[int, int] = defaultdict(int)
    for run in run_rows:
        if run["rule_id"] is not None:
            attacks_by_rule[run["rule_id"]] += 1
        if run["surface_id"] is not None:
            attacks_by_surface[run["surface_id"]] += 1

    breaks_by_rule: dict[int, int] = defaultdict(int)
    hits_by_surface: dict[int, int] = defaultdict(int)
    for break_entry in breaks:
        if break_entry["falsePositive"]:
            continue
        if break_entry["ruleId"]:
            breaks_by_rule[int(break_entry["ruleId"])] += break_entry["hits"]
        if break_entry["surfaceId"]:
            hits_by_surface[int(break_entry["surfaceId"])] += break_entry["hits"]

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
            entry["untestableReason"] = UNTESTABLE_REASON
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

    # REPORT-03: "where the attacks got in" — breaks aggregated by surface,
    # false positives excluded (mirrors the UI's own client-side `bySurface`
    # calc in Report.tsx, now computed once, server-side, from real hits).
    by_surface = sorted(
        (
            {
                "surfaceId": str(row["id"]),
                "path": row["path"],
                "hits": hits_by_surface[row["id"]],
            }
            for row in surface_rows
            if hits_by_surface.get(row["id"], 0) > 0
        ),
        key=lambda x: -x["hits"],
    )

    calls = scan_row["call_count"] if scan_row else 0
    cost = float(scan_row["cost"]) if scan_row and scan_row["cost"] is not None else 0.0
    mode = (scan_row["mode"] if scan_row else None) or "-"
    repeats = scan_row["repeats"] if scan_row else 0
    duration = _duration(
        scan_row["started_at"] if scan_row else None, scan_row["finished_at"] if scan_row else None
    )

    total_rules = len(rules)
    testable_rules = sum(1 for r in rules if r["testable"])
    # REPORT-01: the report leads with this — rules found / testable
    # automatically / need your eyes. SIM-02: when the latest scan skipped
    # tool-surface attacks for a tool-less model, that note rides along here
    # too, so the UI can say honestly that those attacks were never run.
    coverage = {
        "total": total_rules,
        "testable": testable_rules,
        "eyes": total_rules - testable_rules,
        "toolSupportNote": (scan_row["tool_support_note"] if scan_row else None) or None,
        # SCAN-02/mirrors `ScanRecordResponse.indicative_only`: N=1 gives you
        # a single data point, not a rate.
        "indicativeOnly": bool(scan_row) and (repeats or 0) <= 1,
    }

    breaks_found = sum(1 for r in run_rows if not r["passed"])

    # Report.tsx unconditionally reads history[0] — always give it one row,
    # even before any scan has run, so the existing JSX never crashes on
    # real data (Rule 2: the UI has no optional-chaining guard here).
    if scan_row is not None:
        history_date = scan_row["finished_at"] or scan_row["started_at"] or project["created_at"]
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
    top_output = top_break["checkerOutput"] if top_break else ""
    broke_line = top_output.splitlines()[0] if top_output else ""
    walkthrough = {"intent": "", "broke": broke_line, "why": "", "fix": ""}

    # PRIV-02: the purge clock starts here, and ONLY here — the one place
    # that has both `project` and `scan_row` already in scope. A genuinely
    # completed scan (`scan_row is not None and scan_row["finished_at"] is
    # not None`) must exist before the clock starts, so the pre-scan
    # Rules/Questions/Surfaces/ScanConfig/Scanning screens — which all share
    # `useProject` -> this same `GET .../report` call, long before any scan
    # finishes — are structurally incapable of stamping it.
    if (
        project["ephemeral"]
        and not project["seeded"]
        and scan_row is not None
        and scan_row["finished_at"] is not None
    ):
        await mark_report_served(db, slug)

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
        # Additive — not part of `Example` in src/data/types.ts, but REPORT-01
        # /REPORT-03/SIM-02 all name payload fields the UI's own client-side
        # helpers (`coverage()`, Report.tsx's `bySurface` calc) already
        # compute from `rules`/`surfaces`/`breaks` alone; exposing the
        # server's own computation here keeps that logic in one place and
        # gives 01-16 a ready-made value to render straight from the wire.
        "coverage": coverage,
        "bySurface": by_surface,
    }


async def _resolve_break_identity(conn: Any, slug: str, break_id: str) -> asyncpg.Record | None:
    """The one `attack_runs` row `break_id` names, scoped to `slug`
    (T-12-03: a report never leaks another project's runs) — callers use
    its `(rule_id, surface_id, technique_id)` as the group identity and its
    `scan_id` to know which scan's repeats to show."""
    run_id = _parse_break_id(break_id)
    if run_id is None:
        return None
    return await conn.fetchrow(
        """SELECT ar.* FROM attack_runs ar
           JOIN scans s ON s.id = ar.scan_id
           WHERE ar.id = $1 AND s.project_id = $2""",
        run_id,
        slug,
    )


async def break_detail(db: Database, slug: str, break_id: str) -> dict[str, Any] | None:
    """The full `Break` for `break_id`, `variants[]` built from every stored
    `attack_run` of that rule/surface/technique across ALL repeat_index
    values IN THE SAME SCAN `break_id` itself came from (BREAK-02) — `None`
    if `break_id` doesn't resolve to a run in this project."""
    async with db.acquire() as conn:
        anchor = await _resolve_break_identity(conn, slug, break_id)
        if anchor is None:
            return None
        runs = await conn.fetch(
            """SELECT * FROM attack_runs
               WHERE scan_id = $1 AND technique_id = $2
                 AND rule_id IS NOT DISTINCT FROM $3
                 AND surface_id IS NOT DISTINCT FROM $4
               ORDER BY repeat_index, id""",
            anchor["scan_id"],
            anchor["technique_id"],
            anchor["rule_id"],
            anchor["surface_id"],
        )
        all_run_rows = await _fetch_all_runs(conn, slug)

    counted = _counted(list(runs))
    if not counted:
        return None
    key = _break_key(anchor)
    excluded = key in {_break_key(r) for r in all_run_rows if r["false_positive"]}
    return _build_break_entry(key, counted, excluded=excluded)


async def set_false_positive(db: Database, slug: str, break_id: str, value: bool) -> bool:
    """Set `false_positive = value` on every `attack_runs` row sharing
    `break_id`'s (rule, surface, technique) identity, across every scan this
    project has ever run — the write that makes the CURRENT report exclude
    it immediately. `aggregate_report`/`break_detail` ALSO recompute
    exclusion from scratch on every read (checking whether ANY row for that
    identity is flagged, not just the row `break_id` happened to name), so a
    brand new rescan's freshly-inserted row — which `runner.py` (out of
    scope here) always inserts with `false_positive = false` — still reads
    as excluded (CHECK-06's "every future rescan"). Returns `False` if
    `break_id` doesn't resolve to a run in this project."""
    async with db.acquire() as conn:
        anchor = await _resolve_break_identity(conn, slug, break_id)
        if anchor is None:
            return False
        await conn.execute(
            """UPDATE attack_runs ar SET false_positive = $1
               FROM scans s
               WHERE ar.scan_id = s.id AND s.project_id = $2
                 AND ar.technique_id = $3
                 AND ar.rule_id IS NOT DISTINCT FROM $4
                 AND ar.surface_id IS NOT DISTINCT FROM $5""",
            value,
            slug,
            anchor["technique_id"],
            anchor["rule_id"],
            anchor["surface_id"],
        )
    return True


async def mark_report_served(db: Database, slug: str) -> None:
    """PRIV-02: stamp the purge clock the first time — idempotent. A second
    call for the same slug leaves the original timestamp untouched, so
    re-viewing a report during the grace window (open a break, apply a fix,
    rescan, view the report again) never resets or restarts the clock.
    Callers decide WHEN this is safe to call (`aggregate_report` is the only
    caller — see its own docstring/call site for the completed-scan gate);
    this function itself only guards against overwriting an existing
    timestamp."""
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE projects SET report_served_at = now() "
            "WHERE id = $1 AND report_served_at IS NULL",
            slug,
        )


async def purge_expired_ephemeral(db: Database, *, grace_seconds: int) -> list[str]:
    """PRIV-02: hard-delete every ephemeral, non-seeded project whose
    `report_served_at` clock (see `mark_report_served`) is older than
    `grace_seconds` — a single parameterized DELETE relying on the same
    `ON DELETE CASCADE` chain PRIV-01's `DELETE /projects/{slug}` already
    uses, so rules/surfaces/scans/attack_runs all go with it. `seeded = false`
    is structural in this WHERE clause, not a post-hoc filter: a seeded
    example is never a match no matter what its `ephemeral`/
    `report_served_at` columns happen to hold (T-18-01). A project with
    `report_served_at` still NULL (never served, or served but not yet past
    a completed scan) or within the grace window is left completely alone,
    as is any non-ephemeral project regardless of any other column value.
    Returns the deleted slugs, for logging/tests."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """DELETE FROM projects
               WHERE ephemeral = true AND seeded = false
                 AND report_served_at IS NOT NULL
                 AND report_served_at < now() - ($1::int * INTERVAL '1 second')
               RETURNING id""",
            grace_seconds,
        )
    deleted = [row["id"] for row in rows]
    if deleted:
        log.info("ephemeral_projects.purged", count=len(deleted), slugs=deleted)
    return deleted
