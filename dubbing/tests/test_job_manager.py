import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from dubbing.manager.job_manager import JobManager
from dubbing.models.enums import JobErrorKind, JobStatus

pytestmark = pytest.mark.asyncio


async def test_create_job_idempotent(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    j1 = await jm.create_job(episode_id, "placeholder", input_hash="abc")
    j2 = await jm.create_job(episode_id, "placeholder", input_hash="abc")
    assert j1.id == j2.id
    rows = await jm.get_jobs_for_episode(episode_id)
    assert len(rows) == 1


async def test_create_job_dedup_different_input_hash_creates_new(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    j1 = await jm.create_job(episode_id, "placeholder", input_hash="abc")
    j2 = await jm.create_job(episode_id, "placeholder", input_hash="xyz")
    assert j1.id != j2.id


async def test_claim_next_job_atomic_skip_locked(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder", input_hash="only-one")

    results = await asyncio.gather(
        jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30),
        jm.claim_next_job("worker-b", ["placeholder"], lease_seconds=30),
    )
    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1
    assert claimed[0].status == JobStatus.PROCESSING


async def test_claim_sets_status_and_lease(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder")
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=60)
    assert job is not None
    assert job.status == JobStatus.PROCESSING
    assert job.leased_by == "worker-a"
    assert job.lease_expires_at is not None
    assert job.started_at is not None


async def test_renew_lease_only_by_owner(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder")
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30)

    assert await jm.renew_lease(job.id, "worker-a", 60) is True
    assert await jm.renew_lease(job.id, "worker-b", 60) is False


async def test_complete_job_success(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder")
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30)

    ok = await jm.complete_job(job.id, "worker-a")
    assert ok is True
    fetched = await jm.get_job(job.id)
    assert fetched.status == JobStatus.COMPLETED
    assert fetched.finished_at is not None


async def test_zombie_worker_cannot_complete_after_reap(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder")
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30)

    # Simulate reap: another worker takes over after lease expiry.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE jobs SET leased_by = 'worker-b', status = 'processing' WHERE id = $1",
            job.id,
        )

    ok = await jm.complete_job(job.id, "worker-a")
    assert ok is False
    fetched = await jm.get_job(job.id)
    assert fetched.leased_by == "worker-b"


async def test_fail_job_retries_when_attempts_remain(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder", max_attempts=3)
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30)

    updated = await jm.fail_job(job.id, "worker-a", "boom", JobErrorKind.EXCEPTION.value)
    assert updated.status == JobStatus.QUEUED
    assert updated.attempts == 1
    assert updated.leased_by is None


async def test_fail_job_terminal_after_max_attempts(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder", max_attempts=1)
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30)

    updated = await jm.fail_job(job.id, "worker-a", "boom", JobErrorKind.EXCEPTION.value)
    assert updated.status == JobStatus.FAILED
    assert updated.attempts == 1


async def test_fail_job_oom_fails_immediately_regardless_of_attempts(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "placeholder", max_attempts=5)
    job = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30)

    updated = await jm.fail_job(job.id, "worker-a", "out of memory", JobErrorKind.OOM.value)
    assert updated.status == JobStatus.FAILED
    assert updated.attempts == 1


async def test_cancel_job_cascades_to_direct_dependents(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    parent = await jm.create_job(episode_id, "ingestion")
    child = await jm.create_job(episode_id, "transcription", depends_on_job_id=parent.id)

    await jm.cancel_job(parent.id)

    parent_after = await jm.get_job(parent.id)
    child_after = await jm.get_job(child.id)
    assert parent_after.status == JobStatus.CANCELLED
    assert child_after.status == JobStatus.CANCELLED


async def test_cancel_job_cascades_through_multi_level_chain(pool: asyncpg.Pool, episode_id: int):
    """ingestion -> transcription -> diarization -> analysis -> mixing -> QC
    Cancelling ingestion must cascade all the way to QC (5 levels deep)."""
    jm = JobManager(pool)
    ingestion = await jm.create_job(episode_id, "ingestion")
    transcription = await jm.create_job(episode_id, "transcription", depends_on_job_id=ingestion.id)
    diarization = await jm.create_job(episode_id, "diarization", depends_on_job_id=transcription.id)
    analysis = await jm.create_job(episode_id, "analysis", depends_on_job_id=diarization.id)
    mixing = await jm.create_job(episode_id, "mixing", depends_on_job_id=analysis.id)
    qc = await jm.create_job(episode_id, "qc", depends_on_job_id=mixing.id)

    result = await jm.cancel_job(ingestion.id)
    assert result.status == JobStatus.CANCELLED

    for job in (ingestion, transcription, diarization, analysis, mixing, qc):
        fetched = await jm.get_job(job.id)
        assert fetched.status == JobStatus.CANCELLED, f"{fetched.stage} was not cancelled"


async def test_cancel_job_chain_leaves_completed_downstream_job_untouched(pool: asyncpg.Pool, episode_id: int):
    """A downstream job that already completed must never be flipped to
    cancelled, even though it's reachable through the dependency chain."""
    jm = JobManager(pool)
    ingestion = await jm.create_job(episode_id, "ingestion")
    transcription = await jm.create_job(episode_id, "transcription", depends_on_job_id=ingestion.id)
    diarization = await jm.create_job(episode_id, "diarization", depends_on_job_id=transcription.id)

    # transcription finishes before ingestion (its ancestor) gets cancelled.
    claimed = await jm.claim_next_job("worker-a", ["transcription"], lease_seconds=30)
    assert claimed.id == transcription.id
    await jm.complete_job(transcription.id, "worker-a")

    await jm.cancel_job(ingestion.id)

    ingestion_after = await jm.get_job(ingestion.id)
    transcription_after = await jm.get_job(transcription.id)
    diarization_after = await jm.get_job(diarization.id)

    assert ingestion_after.status == JobStatus.CANCELLED
    assert transcription_after.status == JobStatus.COMPLETED  # untouched
    assert diarization_after.status == JobStatus.CANCELLED  # still reachable, still queued


async def test_cancel_job_does_not_touch_terminal_jobs(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    job = await jm.create_job(episode_id, "placeholder")
    claimed = await jm.claim_next_job("worker-a", ["placeholder"], lease_seconds=30)
    await jm.complete_job(claimed.id, "worker-a")

    result = await jm.cancel_job(job.id)
    assert result.status == JobStatus.COMPLETED


async def test_get_status_returns_all_jobs_for_episode(pool: asyncpg.Pool, episode_id: int):
    jm = JobManager(pool)
    await jm.create_job(episode_id, "ingestion")
    await jm.create_job(episode_id, "transcription")

    jobs = await jm.get_jobs_for_episode(episode_id)
    assert len(jobs) == 2
