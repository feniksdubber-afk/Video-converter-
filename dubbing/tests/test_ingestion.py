import hashlib
import os

import asyncpg
import pytest

from dubbing.manager.job_manager import JobManager
from dubbing.media.ingestion import make_ingestion_handler
from dubbing.models.enums import JobErrorKind, JobStatus
from dubbing.worker.base import WorkerLoop
from dubbing.tests.fixtures.synthetic_media import (
    make_corrupt_file,
    make_video_without_audio,
    make_valid_video_with_audio,
)

pytestmark = pytest.mark.asyncio


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


async def _resolver_for(path: str):
    async def resolve(job):
        return path
    return resolve


async def test_valid_ingestion_completes_and_registers_artifact(pool: asyncpg.Pool, episode_id: int, tmp_path):
    video_path = str(tmp_path / "valid.mp4")
    make_valid_video_with_audio(video_path)

    jm = JobManager(pool)
    await jm.create_job(episode_id, "ingestion", input_hash="valid-1")

    worker = WorkerLoop(pool, stages=["ingestion"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("ingestion", make_ingestion_handler(pool, await _resolver_for(video_path)))

    found = await worker.run_once()
    assert found is True

    jobs = await jm.get_jobs_for_episode(episode_id)
    assert jobs[0].status == JobStatus.COMPLETED

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM artifacts WHERE episode_id = $1 AND stage = 'ingestion'", episode_id,
        )
    assert row is not None
    assert row["engine_name"] == "ffmpeg_ingest"


async def test_original_source_file_untouched_after_ingestion(pool: asyncpg.Pool, episode_id: int, tmp_path):
    video_path = str(tmp_path / "valid.mp4")
    make_valid_video_with_audio(video_path)
    original_hash = _file_sha256(video_path)
    original_mtime = os.path.getmtime(video_path)

    jm = JobManager(pool)
    await jm.create_job(episode_id, "ingestion", input_hash="untouched-check")
    worker = WorkerLoop(pool, stages=["ingestion"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("ingestion", make_ingestion_handler(pool, await _resolver_for(video_path)))
    await worker.run_once()

    assert _file_sha256(video_path) == original_hash
    assert os.path.getmtime(video_path) == original_mtime


async def test_missing_audio_track_hard_fails(pool: asyncpg.Pool, episode_id: int, tmp_path):
    video_path = str(tmp_path / "no_audio.mp4")
    make_video_without_audio(video_path)

    jm = JobManager(pool)
    await jm.create_job(episode_id, "ingestion", input_hash="no-audio", max_attempts=1)
    worker = WorkerLoop(pool, stages=["ingestion"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("ingestion", make_ingestion_handler(pool, await _resolver_for(video_path)))

    await worker.run_once()

    jobs = await jm.get_jobs_for_episode(episode_id)
    assert jobs[0].status == JobStatus.FAILED
    assert jobs[0].error_kind == JobErrorKind.EXCEPTION.value
    assert "audio" in jobs[0].error.lower() or "Audio" in jobs[0].error


async def test_corrupt_file_hard_fails_before_ffprobe(pool: asyncpg.Pool, episode_id: int, tmp_path):
    bad_path = str(tmp_path / "corrupt.mp4")
    make_corrupt_file(bad_path)

    jm = JobManager(pool)
    await jm.create_job(episode_id, "ingestion", input_hash="corrupt", max_attempts=1)
    worker = WorkerLoop(pool, stages=["ingestion"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("ingestion", make_ingestion_handler(pool, await _resolver_for(bad_path)))

    await worker.run_once()

    jobs = await jm.get_jobs_for_episode(episode_id)
    assert jobs[0].status == JobStatus.FAILED
    assert jobs[0].error_kind == JobErrorKind.EXCEPTION.value


async def test_failed_ingestion_preserves_temp_dir_for_debugging(pool: asyncpg.Pool, episode_id: int, tmp_path):
    video_path = str(tmp_path / "no_audio.mp4")
    make_video_without_audio(video_path)

    jm = JobManager(pool)
    job = await jm.create_job(episode_id, "ingestion", input_hash="preserve-temp", max_attempts=1)
    worker = WorkerLoop(pool, stages=["ingestion"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("ingestion", make_ingestion_handler(pool, await _resolver_for(video_path)))
    await worker.run_once()

    from dubbing.config import DUBBING_TEMP_DIR
    work_dir = os.path.join(DUBBING_TEMP_DIR, "ingestion", str(job.id))
    assert os.path.isdir(work_dir), "failed job's temp dir should be preserved for debugging"


async def test_successful_ingestion_cleans_up_temp_dir(pool: asyncpg.Pool, episode_id: int, tmp_path):
    video_path = str(tmp_path / "valid.mp4")
    make_valid_video_with_audio(video_path)

    jm = JobManager(pool)
    job = await jm.create_job(episode_id, "ingestion", input_hash="cleanup-check")
    worker = WorkerLoop(pool, stages=["ingestion"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("ingestion", make_ingestion_handler(pool, await _resolver_for(video_path)))
    await worker.run_once()

    from dubbing.config import DUBBING_TEMP_DIR
    work_dir = os.path.join(DUBBING_TEMP_DIR, "ingestion", str(job.id))
    assert not os.path.isdir(work_dir), "successful job's temp dir should be cleaned up"


async def test_repeated_ingestion_of_same_input_is_idempotent_at_artifact_level(
    pool: asyncpg.Pool, episode_id: int, tmp_path,
):
    video_path = str(tmp_path / "valid.mp4")
    make_valid_video_with_audio(video_path)

    jm = JobManager(pool)
    await jm.create_job(episode_id, "ingestion", input_hash="idempotent-1")
    await jm.create_job(episode_id, "ingestion", input_hash="idempotent-2")

    worker = WorkerLoop(pool, stages=["ingestion"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("ingestion", make_ingestion_handler(pool, await _resolver_for(video_path)))

    await worker.run_once()
    await worker.run_once()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM artifacts WHERE episode_id = $1 AND stage = 'ingestion'", episode_id,
        )
    # Same source file + same engine/params -> same content_hash -> single artifact row.
    assert len(rows) == 1
