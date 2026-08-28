"""A worker: claim, run, ack. With heartbeats and a graceful shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
from collections.abc import Awaitable, Callable

import structlog

from substrate.queue.models import ClaimedJob
from substrate.queue.queue import JobQueue

log = structlog.get_logger(__name__)

Handler = Callable[[ClaimedJob], Awaitable[None]]


def default_worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    """Runs `concurrency` claim-run-ack loops against one queue.

    Concurrency is bounded by construction: each loop holds at most one job,
    so the worker never has more than `concurrency` jobs in flight.
    """

    def __init__(
        self,
        queue: JobQueue,
        *,
        name: str | None = None,
        concurrency: int = 1,
        poll_interval: float = 0.25,
        heartbeat_interval: float = 10.0,
    ) -> None:
        self._queue = queue
        self._name = name or default_worker_name()
        self._concurrency = concurrency
        self._poll_interval = poll_interval
        self._heartbeat_interval = heartbeat_interval
        self._handlers: dict[str, Handler] = {}
        self._stopping = asyncio.Event()
        self.processed = 0
        self.failed = 0

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def stop(self) -> None:
        self._stopping.set()

    def install_signal_handlers(self) -> None:
        """SIGTERM stops claiming NEW work but lets in-flight jobs finish.

        Docker sends SIGTERM then waits before SIGKILL. Draining inside that
        window means a deploy costs zero retries.
        """
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.stop)

    async def run_forever(self) -> None:
        await self._run(drain=False)

    async def run_until_idle(self) -> None:
        """Process everything available, then return. No sleeping or timeouts."""
        await self._run(drain=True)

    async def _run(self, *, drain: bool) -> None:
        log.info(
            "worker.start",
            name=self._name,
            concurrency=self._concurrency,
            queue=self._queue.name,
            drain=drain,
        )
        async with asyncio.TaskGroup() as tg:
            for i in range(self._concurrency):
                tg.create_task(self._loop(i, drain=drain))
        log.info("worker.stop", name=self._name, processed=self.processed, failed=self.failed)

    async def _loop(self, slot: int, *, drain: bool) -> None:
        idle_polls = 0
        while not self._stopping.is_set():
            jobs = await self._queue.claim(worker=f"{self._name}#{slot}", limit=1)
            if not jobs:
                # Two consecutive empty polls means the queue is genuinely
                # empty — one is not enough, a sibling slot may be mid-enqueue.
                idle_polls += 1
                if drain and idle_polls >= 2:
                    return
                await asyncio.sleep(self._poll_interval)
                continue
            idle_polls = 0
            await self._execute(jobs[0])

    async def _execute(self, job: ClaimedJob) -> None:
        handler = self._handlers.get(job.kind)
        if handler is None:
            await self._queue.nack(job, error=f"no handler registered for kind={job.kind!r}")
            self.failed += 1
            return

        beat = asyncio.create_task(self._heartbeat(job))
        try:
            await handler(job)
        except Exception as exc:
            log.warning("job.failed", job_id=job.id, kind=job.kind, error=repr(exc))
            await self._queue.nack(job, error=repr(exc))
            self.failed += 1
        else:
            await self._queue.ack(job)
            self.processed += 1
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat

    async def _heartbeat(self, job: ClaimedJob) -> None:
        """Renew the lease while the handler runs.

        Renewing lets the visibility timeout be tight instead of having to
        exceed the slowest job. Losing the lease is not an exception here: the
        fence on ack/nack makes this worker's completion a no-op, so the job
        is safely someone else's.
        """
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            if not await self._queue.heartbeat(job):
                log.warning("job.lease_lost", job_id=job.id, epoch=job.lease_epoch)
                return
