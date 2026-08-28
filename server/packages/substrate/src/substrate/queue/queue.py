"""A durable job queue on Postgres."""

from __future__ import annotations

from typing import Any

import asyncpg
import structlog

from substrate.db import Database
from substrate.queue.models import ClaimedJob, JobSpec, JobState, QueueStats
from substrate.resilience import full_jitter_delay

log = structlog.get_logger(__name__)

_SECONDS = "* interval '1 second'"

# The claim. FOR UPDATE locks the row, SKIP LOCKED steps past rows others
# hold (that's what lets N workers scale), and the CTE + UPDATE...FROM does
# lock + mutate in ONE statement so a crash cannot fall between them.
_CLAIM_SQL = f"""
WITH claimable AS (
    SELECT id
    FROM jobs
    WHERE queue = $1
      AND state IN ('pending', 'running')
      AND available_at <= now()
      AND attempts < max_attempts
    ORDER BY priority DESC, available_at, id
    LIMIT $2
    FOR UPDATE SKIP LOCKED
)
UPDATE jobs j
SET state        = 'running',
    attempts     = j.attempts + 1,
    lease_epoch  = j.lease_epoch + 1,
    claimed_by   = $3,
    available_at = now() + ($4::double precision {_SECONDS}),
    updated_at   = now()
FROM claimable c
WHERE j.id = c.id
RETURNING j.id, j.queue, j.kind, j.payload, j.attempts, j.max_attempts, j.lease_epoch
"""  # noqa: S608 - only a constant interval is interpolated

# A job whose lease expired with no attempts left has killed a worker
# `max_attempts` times without ever reporting failure, and the claim query
# would filter it out forever. Sweep it into the DLQ instead.
_REAP_SQL = """
UPDATE jobs
SET state       = 'dead',
    claimed_by  = NULL,
    finished_at = now(),
    updated_at  = now(),
    last_error  = coalesce(last_error, 'lease expired with no attempts remaining')
WHERE queue = $1
  AND state = 'running'
  AND available_at <= now()
  AND attempts >= max_attempts
"""


class JobQueue:
    def __init__(
        self,
        db: Database,
        *,
        queue: str = "default",
        visibility_timeout: float = 30.0,
        retry_base: float = 0.5,
        retry_cap: float = 60.0,
    ) -> None:
        self._db = db
        self._queue = queue
        self._vt = visibility_timeout
        self._retry_base = retry_base
        self._retry_cap = retry_cap

    @property
    def name(self) -> str:
        return self._queue

    # ------------------------------------------------------------------ write

    async def enqueue(
        self, spec: JobSpec, *, conn: asyncpg.Connection[Any] | None = None
    ) -> int | None:
        """Submit a job. Returns its id, or None if deduped away.

        `conn` lets a caller enqueue inside their own transaction, so
        'write these rows AND schedule the follow-up work' is atomic. That is
        the whole reason this queue lives in the same database as the data.
        """
        if conn is not None:
            return await self._enqueue(conn, spec)
        async with self._db.acquire() as c:
            return await self._enqueue(c, spec)

    async def _enqueue(self, conn: asyncpg.Connection[Any], spec: JobSpec) -> int | None:
        row = await conn.fetchrow(
            """
            INSERT INTO jobs
                (queue, kind, payload, idempotency_key, priority,
                 max_attempts, available_at)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6, coalesce($7, now()))
            ON CONFLICT (queue, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                DO NOTHING
            RETURNING id
            """,
            spec.queue or self._queue,
            spec.kind,
            spec.payload,
            spec.idempotency_key,
            spec.priority,
            spec.max_attempts,
            spec.available_at,
        )
        return int(row["id"]) if row is not None else None

    async def enqueue_many(self, specs: list[JobSpec]) -> int:
        """All-or-nothing fan-out, in one transaction."""
        created = 0
        async with self._db.acquire() as conn, conn.transaction():
            for spec in specs:
                if await self._enqueue(conn, spec) is not None:
                    created += 1
        return created

    # ------------------------------------------------------------------ claim

    async def claim(self, *, worker: str, limit: int = 1) -> list[ClaimedJob]:
        async with self._db.acquire() as conn:
            await conn.execute(_REAP_SQL, self._queue)
            rows = await conn.fetch(_CLAIM_SQL, self._queue, limit, worker, self._vt)
        return [ClaimedJob.model_validate(dict(r)) for r in rows]

    async def heartbeat(self, job: ClaimedJob) -> bool:
        """Extend the lease. False means it was lost — stop working.

        The `lease_epoch` check is a fencing token. If this job was reclaimed
        while we were busy, the epoch moved and this UPDATE matches nothing.
        """
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE jobs
                SET available_at = now() + ($3::double precision {_SECONDS}),
                    updated_at = now()
                WHERE id = $1 AND lease_epoch = $2 AND state = 'running'
                RETURNING id
                """,  # noqa: S608 - only a constant interval is interpolated
                job.id,
                job.lease_epoch,
                self._vt,
            )
        return row is not None

    # --------------------------------------------------------------- complete

    async def ack(self, job: ClaimedJob) -> bool:
        """Mark succeeded. Fenced, so a zombie cannot ack someone else's job."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE jobs SET state = 'succeeded', claimed_by = NULL,
                    last_error = NULL, finished_at = now(), updated_at = now()
                WHERE id = $1 AND lease_epoch = $2 AND state = 'running'
                RETURNING id
                """,
                job.id,
                job.lease_epoch,
            )
        if row is None:
            log.warning("queue.ack_rejected", job_id=job.id, epoch=job.lease_epoch)
        return row is not None

    async def nack(self, job: ClaimedJob, *, error: str) -> JobState | None:
        """Report failure: schedule a retry, or dead-letter if out of attempts.

        `attempts` was already incremented at claim time, so attempts >=
        max_attempts means 'that was the last one'.
        """
        delay = full_jitter_delay(job.attempts, base=self._retry_base, cap=self._retry_cap)
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE jobs SET
                    state = CASE WHEN attempts >= max_attempts
                                 THEN 'dead'::job_state
                                 ELSE 'pending'::job_state END,
                    available_at = CASE WHEN attempts >= max_attempts
                                        THEN available_at
                                        ELSE now() + ($3::double precision {_SECONDS})
                                   END,
                    finished_at = CASE WHEN attempts >= max_attempts
                                       THEN now() ELSE NULL END,
                    last_error = $4,
                    claimed_by = NULL,
                    updated_at = now()
                WHERE id = $1 AND lease_epoch = $2 AND state = 'running'
                RETURNING state
                """,  # noqa: S608 - only a constant interval is interpolated
                job.id,
                job.lease_epoch,
                delay,
                error[:4000],
            )
        if row is None:
            log.warning("queue.nack_rejected", job_id=job.id)
            return None
        state = JobState(row["state"])
        log.info(
            "queue.nack",
            job_id=job.id,
            state=state,
            attempt=job.attempts,
            retry_in=round(delay, 2),
            error=error[:200],
        )
        return state

    # ---------------------------------------------------------------- inspect

    async def stats(self) -> QueueStats:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT state, count(*) AS n FROM jobs WHERE queue = $1 GROUP BY state",
                self._queue,
            )
        return QueueStats(**{r["state"]: r["n"] for r in rows})

    async def dead_letters(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, kind, payload, attempts, last_error, finished_at
                FROM jobs WHERE queue = $1 AND state = 'dead'
                ORDER BY finished_at DESC LIMIT $2
                """,
                self._queue,
                limit,
            )
        return [dict(r) for r in rows]

    async def requeue_dead(self, *, limit: int = 100) -> int:
        """Send dead letters back for another go, after you've fixed the bug."""
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE jobs SET state = 'pending', attempts = 0,
                    available_at = now(), finished_at = NULL, last_error = NULL,
                    updated_at = now()
                WHERE id IN (
                    SELECT id FROM jobs WHERE queue = $1 AND state = 'dead'
                    ORDER BY finished_at LIMIT $2
                )
                RETURNING id
                """,
                self._queue,
                limit,
            )
        return len(rows)
