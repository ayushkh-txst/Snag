"""Queue semantics against a real Postgres. Mocking would delete the point."""

from __future__ import annotations

import asyncio

import pytest

from substrate.db import Database
from substrate.queue import ClaimedJob, JobQueue, JobSpec, JobState, Worker

pytestmark = pytest.mark.integration


def _q(db: Database, **kw: float) -> JobQueue:
    return JobQueue(db, queue="test", visibility_timeout=kw.pop("vt", 30.0), **kw)


async def test_enqueue_and_claim_round_trip(clean_db: Database) -> None:
    q = _q(clean_db)
    job_id = await q.enqueue(JobSpec(kind="noop", payload={"n": 1}, queue="test"))
    assert job_id is not None

    claimed = await q.claim(worker="w1")
    assert len(claimed) == 1
    assert claimed[0].payload == {"n": 1}
    assert claimed[0].attempts == 1  # incremented AT CLAIM
    assert claimed[0].lease_epoch == 1

    assert await q.ack(claimed[0]) is True
    assert (await q.stats()).succeeded == 1


async def test_claimed_job_is_invisible_to_others(clean_db: Database) -> None:
    q = _q(clean_db)
    await q.enqueue(JobSpec(kind="noop", queue="test"))

    first = await q.claim(worker="w1")
    second = await q.claim(worker="w2")

    assert len(first) == 1
    assert second == []


async def test_idempotency_key_deduplicates(clean_db: Database) -> None:
    q = _q(clean_db)
    a = await q.enqueue(JobSpec(kind="fetch", queue="test", idempotency_key="2019-01-31"))
    b = await q.enqueue(JobSpec(kind="fetch", queue="test", idempotency_key="2019-01-31"))
    c = await q.enqueue(JobSpec(kind="fetch", queue="test", idempotency_key="2019-02-01"))

    assert a is not None
    assert b is None  # deduped
    assert c is not None
    assert (await q.stats()).pending == 2


async def test_expired_lease_is_reclaimed_and_the_zombie_is_fenced(
    clean_db: Database,
) -> None:
    """w1 claims then dies; w2 reclaims after the lease lapses; w1's ack is refused."""
    q = _q(clean_db, vt=0.5)
    await q.enqueue(JobSpec(kind="noop", queue="test", max_attempts=5))

    zombie = (await q.claim(worker="w1"))[0]
    assert zombie.attempts == 1

    assert await q.claim(worker="w2") == []  # lease still live
    await asyncio.sleep(0.7)  # ...now it isn't

    reclaimed = (await q.claim(worker="w2"))[0]
    assert reclaimed.id == zombie.id
    assert reclaimed.attempts == 2  # the crash burned an attempt
    assert reclaimed.lease_epoch == 2

    assert await q.ack(zombie) is False  # fenced out
    assert await q.ack(reclaimed) is True


async def test_heartbeat_holds_the_lease_and_fails_once_lost(clean_db: Database) -> None:
    q = _q(clean_db, vt=0.5)
    await q.enqueue(JobSpec(kind="noop", queue="test"))
    job = (await q.claim(worker="w1"))[0]

    await asyncio.sleep(0.3)
    assert await q.heartbeat(job) is True  # renewed
    await asyncio.sleep(0.3)
    assert await q.claim(worker="w2") == []  # still ours, thanks to the beat

    await asyncio.sleep(0.7)
    stolen = (await q.claim(worker="w2"))[0]
    assert stolen.lease_epoch == 2
    assert await q.heartbeat(job) is False  # old epoch, refused


async def test_failure_retries_then_dead_letters(clean_db: Database) -> None:
    q = _q(clean_db, retry_base=0.001, retry_cap=0.01)
    await q.enqueue(JobSpec(kind="boom", queue="test", max_attempts=3))

    states = []
    for _ in range(3):
        job = (await q.claim(worker="w1"))[0]
        states.append(await q.nack(job, error="kaboom"))
        await asyncio.sleep(0.02)

    assert states == [JobState.PENDING, JobState.PENDING, JobState.DEAD]
    assert await q.claim(worker="w1") == []  # exhausted, not retried
    assert (await q.stats()).dead == 1

    dead = await q.dead_letters()
    assert dead[0]["attempts"] == 3
    assert "kaboom" in dead[0]["last_error"]

    assert await q.requeue_dead() == 1
    assert (await q.stats()).pending == 1


async def test_a_job_that_kills_its_worker_still_reaches_the_dlq(
    clean_db: Database,
) -> None:
    """Poison-pill defence: attempts are counted at claim, so a hard crash
    still burns one, and the reaper sweeps the job to the DLQ."""
    q = _q(clean_db, vt=0.3)
    await q.enqueue(JobSpec(kind="poison", queue="test", max_attempts=2))

    for _ in range(2):
        assert len(await q.claim(worker="crasher")) == 1
        await asyncio.sleep(0.4)  # "process died"; lease lapses

    assert await q.claim(worker="w2") == []  # not handed out again
    assert (await q.stats()).dead == 1


# ---------------------------------------------------------------------------
# THE CHECKPOINT TEST
# ---------------------------------------------------------------------------


async def test_concurrent_workers_never_double_claim(clean_db: Database) -> None:
    """8 workers, 200 jobs. Every job claimed exactly once.

    This is the raison d'être of the queue: if SKIP LOCKED is wrong, or the
    lock and the UPDATE aren't one statement, two workers process the same
    job — which means the same snapshot ingested twice, silently. Real
    sleeping, real row locks, no mocking.
    """
    total_jobs = 200
    workers = 8

    q = _q(clean_db, vt=60.0)
    await q.enqueue_many(
        [JobSpec(kind="noop", payload={"i": i}, queue="test") for i in range(total_jobs)]
    )

    claimed_by_all: list[int] = []
    lock = asyncio.Lock()

    async def worker(wid: int) -> None:
        empty = 0
        while empty < 3:
            jobs = await q.claim(worker=f"w{wid}", limit=5)
            if not jobs:
                empty += 1
                await asyncio.sleep(0.01)
                continue
            empty = 0
            async with lock:
                claimed_by_all.extend(j.id for j in jobs)
            for j in jobs:
                await q.ack(j)

    await asyncio.gather(*(worker(i) for i in range(workers)))

    assert len(claimed_by_all) == total_jobs, "a job was lost or claimed twice"
    assert len(set(claimed_by_all)) == total_jobs, "a job was claimed by two workers"
    assert (await q.stats()).succeeded == total_jobs


async def test_worker_runs_handlers_and_drains(clean_db: Database) -> None:
    q = _q(clean_db)
    await q.enqueue_many([JobSpec(kind="add", payload={"n": i}, queue="test") for i in range(20)])
    await q.enqueue(JobSpec(kind="explode", queue="test", max_attempts=1))

    seen: list[int] = []

    async def add(job: ClaimedJob) -> None:
        seen.append(int(job.payload["n"]))

    async def explode(job: ClaimedJob) -> None:
        raise RuntimeError("expected")

    w = Worker(q, name="t", concurrency=4, poll_interval=0.01)
    w.register("add", add)
    w.register("explode", explode)
    await w.run_until_idle()

    assert sorted(seen) == list(range(20))
    assert w.processed == 20
    assert w.failed == 1
    stats = await q.stats()
    assert stats.succeeded == 20
    assert stats.dead == 1
