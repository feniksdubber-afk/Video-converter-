"""
Step 3 segmentation — artifact registration/idempotency/lineage/hashing
testlari, WorkerLoop orqali to'liq end-to-end oqim bilan.
"""

import json
import os

import asyncpg
import pytest

from dubbing.artifacts.store import ArtifactStore
from dubbing.manager.job_manager import JobManager
from dubbing.media.ingestion import make_ingestion_handler
from dubbing.models.enums import JobErrorKind, JobStatus
from dubbing.segmentation import segmenter as segmenter_module
from dubbing.segmentation.segmenter import make_segmentation_handler
from dubbing.tests.fixtures.synthetic_audio import make_video_with_tone_then_silence
from dubbing.worker.base import WorkerLoop

pytestmark = pytest.mark.asyncio


async def _resolver_for(path: str):
    async def resolve(job):
        return path
    return resolve


async def _run_ingestion_and_get_artifact(pool, episode_id, video_path, input_hash):
    jm = JobManager(pool)
    ingestion_job = await jm.create_job(episode_id, "ingestion", input_hash=input_hash)
    worker = WorkerLoop(pool, stages=["ingestion"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("ingestion", make_ingestion_handler(pool, await _resolver_for(video_path)))
    found = await worker.run_once()
    assert found is True

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM artifacts WHERE episode_id = $1 AND stage = 'ingestion'", episode_id,
        )
    assert row is not None
    return ingestion_job, row


def test_segmentation_hash_is_deterministic_and_param_order_independent():
    params_a = {
        "segments": [{"index": 0, "start_sec": 0.0, "end_sec": 1.0, "kind": "speech"}],
        "segment_count": 1,
        "vad_params": {"silence_threshold_db": -35, "min_segment_sec": 0.3},
    }
    params_b = {
        "vad_params": {"min_segment_sec": 0.3, "silence_threshold_db": -35},
        "segment_count": 1,
        "segments": [{"index": 0, "start_sec": 0.0, "end_sec": 1.0, "kind": "speech"}],
    }
    h1 = ArtifactStore.compute_hash(["parent-hash"], "ffmpeg_silencedetect", "6.1.1", params_a)
    h2 = ArtifactStore.compute_hash(["parent-hash"], "ffmpeg_silencedetect", "6.1.1", params_b)
    assert h1 == h2


def test_segmentation_hash_changes_when_vad_params_change():
    base_params = {
        "segments": [{"index": 0, "start_sec": 0.0, "end_sec": 1.0, "kind": "speech"}],
        "segment_count": 1,
        "vad_params": {"silence_threshold_db": -35, "min_segment_sec": 0.3},
    }
    changed_params = json.loads(json.dumps(base_params))
    changed_params["vad_params"]["silence_threshold_db"] = -40

    h1 = ArtifactStore.compute_hash(["parent-hash"], "ffmpeg_silencedetect", "6.1.1", base_params)
    h2 = ArtifactStore.compute_hash(["parent-hash"], "ffmpeg_silencedetect", "6.1.1", changed_params)
    assert h1 != h2


async def test_segmentation_end_to_end_registers_artifact_with_expected_shape(
    pool: asyncpg.Pool, episode_id: int, tmp_path,
):
    video_path = str(tmp_path / "video.mp4")
    total_sec = make_video_with_tone_then_silence(video_path, tone_sec=1.0, silence_sec=2.0)

    ingestion_job, ingestion_row = await _run_ingestion_and_get_artifact(
        pool, episode_id, video_path, input_hash="seg-e2e-1",
    )

    jm = JobManager(pool)
    seg_job = await jm.create_job(
        episode_id, "segmentation",
        input_hash=ingestion_row["content_hash"],
        depends_on_job_id=ingestion_job.id,
    )

    worker = WorkerLoop(pool, stages=["segmentation"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("segmentation", make_segmentation_handler(pool))
    found = await worker.run_once()
    assert found is True

    jobs = await jm.get_jobs_for_episode(episode_id)
    seg_job_row = next(j for j in jobs if j.id == seg_job.id)
    assert seg_job_row.status == JobStatus.COMPLETED

    async with pool.acquire() as conn:
        seg_row = await conn.fetchrow(
            "SELECT * FROM artifacts WHERE episode_id = $1 AND stage = 'segmentation'", episode_id,
        )
    assert seg_row is not None
    assert seg_row["engine_name"] == "ffmpeg_silencedetect"

    params = seg_row["params"]
    if isinstance(params, str):
        params = json.loads(params)

    segments = params["segments"]
    assert params["segment_count"] == len(segments)
    assert segments[0]["start_sec"] == 0.0
    assert segments[-1]["end_sec"] == round(total_sec, 3)
    for i in range(len(segments) - 1):
        assert segments[i]["end_sec"] == segments[i + 1]["start_sec"]
        assert segments[i]["index"] == i


async def test_segmentation_artifact_has_ingestion_as_parent_lineage(
    pool: asyncpg.Pool, episode_id: int, tmp_path,
):
    video_path = str(tmp_path / "video.mp4")
    make_video_with_tone_then_silence(video_path, tone_sec=1.0, silence_sec=2.0)

    ingestion_job, ingestion_row = await _run_ingestion_and_get_artifact(
        pool, episode_id, video_path, input_hash="seg-lineage-1",
    )

    jm = JobManager(pool)
    seg_job = await jm.create_job(
        episode_id, "segmentation",
        input_hash=ingestion_row["content_hash"],
        depends_on_job_id=ingestion_job.id,
    )
    worker = WorkerLoop(pool, stages=["segmentation"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("segmentation", make_segmentation_handler(pool))
    await worker.run_once()

    store = ArtifactStore(pool)
    async with pool.acquire() as conn:
        seg_row = await conn.fetchrow(
            "SELECT * FROM artifacts WHERE episode_id = $1 AND stage = 'segmentation'", episode_id,
        )
    lineage = await store.get_lineage(seg_row["id"])
    assert len(lineage) == 1
    assert lineage[0].id == ingestion_row["id"]
    assert lineage[0].stage == "ingestion"


async def test_segmentation_idempotent_at_artifact_level(
    pool: asyncpg.Pool, episode_id: int, tmp_path,
):
    video_path = str(tmp_path / "video.mp4")
    make_video_with_tone_then_silence(video_path, tone_sec=1.0, silence_sec=2.0)

    ingestion_job, ingestion_row = await _run_ingestion_and_get_artifact(
        pool, episode_id, video_path, input_hash="seg-idempotent-1",
    )

    jm = JobManager(pool)
    seg_job = await jm.create_job(
        episode_id, "segmentation",
        input_hash=ingestion_row["content_hash"],
        depends_on_job_id=ingestion_job.id,
    )

    handler = make_segmentation_handler(pool)
    # Invoke the handler twice directly with identical inputs, simulating
    # a re-run (e.g. after a lease reap) that must not create a duplicate
    # artifact row.
    await handler(seg_job)
    await handler(seg_job)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM artifacts WHERE episode_id = $1 AND stage = 'segmentation'", episode_id,
        )
    assert len(rows) == 1


async def test_missing_ingestion_artifact_fails_with_exception_kind(
    pool: asyncpg.Pool, episode_id: int,
):
    jm = JobManager(pool)
    await jm.create_job(
        episode_id, "segmentation", input_hash="nonexistent-ingestion-hash", max_attempts=1,
    )

    worker = WorkerLoop(pool, stages=["segmentation"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("segmentation", make_segmentation_handler(pool))
    await worker.run_once()

    jobs = await jm.get_jobs_for_episode(episode_id)
    assert jobs[0].status == JobStatus.FAILED
    assert jobs[0].error_kind == JobErrorKind.EXCEPTION.value


async def test_segmentation_timeout_is_classified_correctly(
    pool: asyncpg.Pool, episode_id: int, tmp_path, monkeypatch,
):
    video_path = str(tmp_path / "video.mp4")
    make_video_with_tone_then_silence(video_path, tone_sec=1.0, silence_sec=2.0)

    ingestion_job, ingestion_row = await _run_ingestion_and_get_artifact(
        pool, episode_id, video_path, input_hash="seg-timeout-1",
    )

    jm = JobManager(pool)
    await jm.create_job(
        episode_id, "segmentation",
        input_hash=ingestion_row["content_hash"],
        depends_on_job_id=ingestion_job.id,
        max_attempts=1,
    )

    async def _raise_timeout(*args, **kwargs):
        raise TimeoutError("simulated segmentation timeout")

    monkeypatch.setattr(segmenter_module, "_extract_working_audio", _raise_timeout)

    worker = WorkerLoop(pool, stages=["segmentation"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("segmentation", make_segmentation_handler(pool))
    await worker.run_once()

    jobs = await jm.get_jobs_for_episode(episode_id)
    seg_jobs = [j for j in jobs if j.stage == "segmentation"]
    assert seg_jobs[0].status == JobStatus.FAILED
    assert seg_jobs[0].error_kind == JobErrorKind.TIMEOUT.value


async def test_failed_segmentation_preserves_temp_dir_for_debugging(
    pool: asyncpg.Pool, episode_id: int, tmp_path, monkeypatch,
):
    video_path = str(tmp_path / "video.mp4")
    make_video_with_tone_then_silence(video_path, tone_sec=1.0, silence_sec=2.0)

    ingestion_job, ingestion_row = await _run_ingestion_and_get_artifact(
        pool, episode_id, video_path, input_hash="seg-preserve-1",
    )

    jm = JobManager(pool)
    seg_job = await jm.create_job(
        episode_id, "segmentation",
        input_hash=ingestion_row["content_hash"],
        depends_on_job_id=ingestion_job.id,
        max_attempts=1,
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated silencedetect failure")

    monkeypatch.setattr(segmenter_module, "detect_silence_intervals", _raise)

    worker = WorkerLoop(pool, stages=["segmentation"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("segmentation", make_segmentation_handler(pool))
    await worker.run_once()

    from dubbing.config import DUBBING_TEMP_DIR
    work_dir = os.path.join(DUBBING_TEMP_DIR, "segmentation", str(seg_job.id))
    assert os.path.isdir(work_dir), "failed job's temp dir should be preserved for debugging"


async def test_successful_segmentation_cleans_up_temp_dir(
    pool: asyncpg.Pool, episode_id: int, tmp_path,
):
    video_path = str(tmp_path / "video.mp4")
    make_video_with_tone_then_silence(video_path, tone_sec=1.0, silence_sec=2.0)

    ingestion_job, ingestion_row = await _run_ingestion_and_get_artifact(
        pool, episode_id, video_path, input_hash="seg-cleanup-1",
    )

    jm = JobManager(pool)
    seg_job = await jm.create_job(
        episode_id, "segmentation",
        input_hash=ingestion_row["content_hash"],
        depends_on_job_id=ingestion_job.id,
    )

    worker = WorkerLoop(pool, stages=["segmentation"], lease_seconds=60, poll_interval_seconds=0.1)
    worker.register_handler("segmentation", make_segmentation_handler(pool))
    await worker.run_once()

    from dubbing.config import DUBBING_TEMP_DIR
    work_dir = os.path.join(DUBBING_TEMP_DIR, "segmentation", str(seg_job.id))
    assert not os.path.isdir(work_dir), "successful job's temp dir should be cleaned up"
