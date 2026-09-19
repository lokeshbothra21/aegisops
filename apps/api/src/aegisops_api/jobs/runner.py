"""A small asyncio periodic-job runner for the API process.

Why not APScheduler yet: two jobs, both "run every N minutes, log, never crash the
process". Forty lines beat a dependency until the alert evaluator (E2.3) needs
cron-like scheduling; swap then if it earns it.

Each job runs in its own database session (commit on success, rollback on error)
and an exception is logged, never propagated: one bad tick must not stop the loop.
Cloud Run only runs this while an instance is alive (min-instances 0), which is
fine for retention/edges: the next request wakes an instance and the next tick runs.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aegisops_api.db import session_scope

log = structlog.get_logger()

type JobFn = Callable[[AsyncSession], Awaitable[object]]


@dataclass
class Job:
    name: str
    fn: JobFn
    interval_s: float
    run_at_start: bool = True


@dataclass
class JobRunner:
    factory: async_sessionmaker[AsyncSession]
    jobs: list[Job] = field(default_factory=list)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list, repr=False)

    async def run_once(self, job: Job) -> object | None:
        """Run one job in its own session; log and swallow errors. Returns the job's result."""
        try:
            async with session_scope(self.factory) as session:
                result = await job.fn(session)
            # the commit has happened by here, so "job.ok" means "persisted"
            log.info("job.ok", job=job.name, result=result)
            return result
        except Exception as exc:
            log.warning("job.failed", job=job.name, error=repr(exc))
        return None

    async def _loop(self, job: Job) -> None:
        if not job.run_at_start:
            await asyncio.sleep(job.interval_s)
        while True:
            await self.run_once(job)
            await asyncio.sleep(job.interval_s)

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._loop(job), name=f"job:{job.name}") for job in self.jobs
        ]
        log.info("jobs.start", jobs=[j.name for j in self.jobs])

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        log.info("jobs.stop")
