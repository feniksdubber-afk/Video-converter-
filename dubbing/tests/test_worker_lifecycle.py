import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from dubbing.manager.job_manager import JobManager
from dubbing.models.enums import JobStatus
from dubbing.worker.base import WorkerLoop
from dubbing.worker.reaper import reap_expired_leases

pytestmark = pytest.mark.asyncio


async def test_run_once_returns_false_when_queue_empty(pool: asyncpg.Pool):
    worker = WorkerLoop(pool, stages=["placeholder"], lease_seconds=30, poll_interval_seconds=0.1)
    found = await worker.run_once()
    assert found is False


async def test_run_once_success_path(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder")

    worker = WorkerLoop(pool, stages=["placeholder"], lease_seconds=30, poll_interval_seconds=0.1)

    calls = []

    async def handler(job):
        calls.append(job.id)

    worker.register_handler("placeholder", handler)
    found = await worker.run_once()

    assert found is True
    assert len(calls) == 1
    jobs = await jm.get_jobs_for_episode(episode_id)
    assert jobs[0].status == JobStatus.COMPLETED


async def test_run_once_handler_exception_fails_job(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder", max_attempts=1)

    worker = WorkerLoop(pool, stages=["placeholder"], lease_seconds=30, poll_interval_seconds=0.1)

    async def handler(job):
        raise ValueError("simulated engine failure")

    worker.register_handler("placeholder", handler)
    found = await worker.run_once()

    assert found is True
    jobs = await jm.get_jobs_for_episode(episode_id)
    assert jobs[0].status == JobStatus.FAILED
    assert "simulated engine failure" in jobs[0].error


async def test_run_once_no_handler_registered_fails_job(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "unhandled_stage", max_attempts=1)

    worker = WorkerLoop(pool, stages=["unhandled_stage"], lease_seconds=30, poll_interval_seconds=0.1)
    found = await worker.run_once()

    assert found is True
    jobs = await jm.get_jobs_for_episode(episode_id)
    assert jobs[0].status == JobStatus.FAILED


async def test_graceful_shutdown_stops_run_loop(pool: asyncpg.Pool):
    worker = WorkerLoop(pool, stages=["placeholder"], lease_seconds=30, poll_interval_seconds=0.05)
    run_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)
    worker.request_stop()
    await asyncio.wait_for(run_task, timeout=2)
    assert run_task.done()


async def test_reaper_requeues_expired_leases(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder")
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30)

    # Force the lease into the past to simulate an expired/crashed worker.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET lease_expires_at = $2 WHERE id = $1",
            job.id, datetime.now(timezone.utc) - timedelta(seconds=5),
        )

    reaped_count = await reap_expired_leases(pool)
    assert reaped_count == 1

    fetched = await jm.get_job(job.id)
    assert fetched.status == JobStatus.QUEUED
    assert fetched.leased_by is None
    assert fetched.lease_expires_at is None


async def test_reaper_ignores_active_leases(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder")
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=300)

    reaped_count = await reap_expired_leases(pool)
    assert reaped_count == 0

    fetched = await jm.get_job(job.id)
    assert fetched.status == JobStatus.PROCESSING
