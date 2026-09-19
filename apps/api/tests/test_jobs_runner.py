"""The periodic runner keeps ticking through failures and stops cleanly."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.jobs.runner import Job, JobRunner
from aegisops_api.settings import Settings


async def test_runner_ticks_and_survives_exceptions(settings: Settings) -> None:
    engine = create_engine(settings)
    calls = {"ok": 0, "bad": 0}

    async def ok(_: AsyncSession) -> str:
        calls["ok"] += 1
        return "fine"

    async def bad(_: AsyncSession) -> None:
        calls["bad"] += 1
        raise RuntimeError("boom")

    runner = JobRunner(
        factory=create_session_factory(engine),
        jobs=[
            Job("ok", ok, 0.05),
            Job("bad", bad, 0.05),
            Job("late", ok, 0.05, run_at_start=False),
        ],
    )
    try:
        runner.start()
        await asyncio.sleep(0.3)
        await runner.stop()
    finally:
        await engine.dispose()
    assert calls["ok"] >= 4  # "ok" immediately + ticks, plus "late" after its first sleep
    assert calls["bad"] >= 2  # kept ticking after raising
    assert runner._tasks == []


async def test_run_once_returns_result_or_none(settings: Settings) -> None:
    engine = create_engine(settings)

    async def ok(_: AsyncSession) -> int:
        return 7

    async def bad(_: AsyncSession) -> int:
        raise ValueError("no")

    runner = JobRunner(factory=create_session_factory(engine))
    try:
        assert await runner.run_once(Job("ok", ok, 1)) == 7
        assert await runner.run_once(Job("bad", bad, 1)) is None
    finally:
        await engine.dispose()
