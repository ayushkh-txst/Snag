"""Hand-rolled SSE progress streaming for scans (PROGRESS-01).

Scans run in a separate worker process from the API (`snag work`, or a test's
`drain_scan_queue`), so the CiteDelta-RAG in-process `asyncio.Queue` pattern
(`citedelta.api.app`'s `turn_stream`/`_PENDING`) cannot span processes here —
there is no shared memory between the worker that runs `snag.runner._run_scan`
and the request handling `GET /scans/{id}/stream`. Progress is written to the
database instead (`scan_events` + the `scans` counters, both already part of
the 01-09 schema) by `write_progress`, and `scan_event_stream` tails those
rows by `seq`. A reconnecting client passes `?since_seq=` to replay only what
it hasn't seen — nothing is lost on refresh, and nothing is replayed twice.

Framing mirrors CiteDelta's `turn_stream`: one JSON object per line
(`event: phase\\ndata: {json}\\n\\n`), `text/event-stream`, no-cache +
`X-Accel-Buffering: no` headers so nothing between here and the browser
buffers the stream (T-11-03: `json.dumps`/asyncpg's jsonb codec never emit an
embedded newline, so a frame is always exactly one `data:` line).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from substrate.db import Database

# The three terminal `scans.status` values this module knows about (the
# runner never leaves a scan in any other state once `_run_scan` returns —
# see `_mark_scan_completed`/`_stop_at_cap`/`_mark_scan_failed`). A scan not
# in this set is still `pending` or `running`.
TERMINAL_STATUSES = frozenset({"completed", "stopped_at_cap", "failed"})

# T-11-01: bounds how long an open connection polls an idle scan between
# rows — cheap enough for this single-process demo scope (documented in the
# plan's threat register), short enough that a live scan still feels live.
POLL_INTERVAL_SECONDS = 0.25


async def scan_event_stream(
    db: Database, scan_id: int, *, since_seq: int = 0
) -> AsyncIterator[str]:
    """Tail `scan_events` for `scan_id`, yielding one SSE `phase` frame per
    row with `seq > since_seq` (so a reconnect resumes instead of replaying
    from the start), then an SSE `done` frame — and closes the generator —
    the moment `scans.status` reaches a terminal value. Any already-persisted
    rows the caller hasn't seen are always flushed before `done`, even if the
    scan finished between polls."""
    last_seq = since_seq
    while True:
        async with db.acquire() as conn:
            scan = await conn.fetchrow("SELECT status FROM scans WHERE id = $1", scan_id)
            if scan is None:
                return
            rows = await conn.fetch(
                """SELECT seq, kind, data FROM scan_events
                       WHERE scan_id = $1 AND seq > $2
                       ORDER BY seq""",
                scan_id,
                last_seq,
            )

        for row in rows:
            last_seq = row["seq"]
            frame = {"seq": row["seq"], "kind": row["kind"], **(row["data"] or {})}
            # One line — `json.dumps` never emits a bare newline, keeping
            # each frame well-formed regardless of what the event data holds
            # (T-11-03).
            yield f"event: phase\ndata: {json.dumps(frame)}\n\n"

        if scan["status"] in TERMINAL_STATUSES:
            yield f"event: done\ndata: {json.dumps({'status': scan['status']})}\n\n"
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def write_progress(
    conn: Any,
    scan_id: int,
    *,
    kind: str,
    data: dict[str, Any],
    rule_id: int,
    surface_id: int,
    call_count: int,
    cost: Decimal,
    attacks_done: int,
    broke: bool,
) -> int:
    """Append one `scan_events` row (seq assigned atomically as this scan's
    current max + 1, in the same statement — the runner only ever has one
    in-flight writer per `scan_id`, so no separate lock is needed) and update
    the `scans` progress counters this event reports, on the SAME connection
    the caller already holds (the runner's per-attack `async with
    db.acquire()` block, right after `_persist_attack_run`). This is the
    runner's one DB-write seam for progress — additive, not a rewrite of its
    loop. Returns the assigned seq."""
    seq = await conn.fetchval(
        """INSERT INTO scan_events (scan_id, seq, kind, data)
               SELECT $1, COALESCE(MAX(seq), 0) + 1, $2, $3
               FROM scan_events WHERE scan_id = $1
               RETURNING seq""",
        scan_id,
        kind,
        data,
    )
    await conn.execute(
        """UPDATE scans SET call_count = $2, cost = $3,
                   attacks_done = $4,
                   breaks_found = breaks_found + $5,
                   current_rule_id = $6, current_surface_id = $7
               WHERE id = $1""",
        scan_id,
        call_count,
        cost,
        attacks_done,
        1 if broke else 0,
        rule_id,
        surface_id,
    )
    return int(seq)
