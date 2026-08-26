"""
dubbing/segmentation/segmenter.py — "segmentation" bosqichi uchun
WorkerLoop handler.

Oqim:
    job.input_hash orqali ingestion artifact topiladi
    -> ingestion artifact params'idan original manba yo'li (source_path)
       va davomiylik (duration_sec) olinadi
    -> original manba fayldan mono/16kHz PCM WAV qayta hosil qilinadi
       (Step 2 ingestion bilan bir xil ffmpeg parametrlari — lekin
       mustaqil, Step 2 kodiga import orqali bog'liq emas)
    -> dubbing.segmentation.vad orqali ffmpeg silencedetect bilan sukunat
       oraliqlari aniqlanadi
    -> dubbing.segmentation.boundaries orqali segment ro'yxati quriladi
       (qisqalarni birlashtirish, uzunlarni bo'lish, yaxlitlash)
    -> ArtifactStore.register(...) (stage='segmentation',
       parent=ingestion artifact)
    -> muvaffaqiyatli bo'lsa vaqtinchalik ish papkasi o'chiriladi
    -> xato bo'lsa vaqtinchalik ish papkasi DEBUGGING uchun SAQLANADI

IZOLYATSIYA:
    - Bu modul `dubbing.media.ingestion`dan HECH NARSA import qilmaydi.
      WAV-ekstraktsiya va ffmpeg-versiya helperlari atayilab mustaqil
      ravishda takrorlangan (kichik, ~10-20 qatorlik funksiyalar) — bu
      Step 2'ga har qanday kelajakdagi o'zgartirish Step 3'ni kutilmaganda
      buzmasligini ta'minlaydi, va aksincha.
    - WAV qayta ekstraktsiyasi uchun faqat tasdiqlangan
      `utils.ffmpeg_utils.run_ffmpeg` (sof, sinxron) import qilinadi,
      executor orqali chaqiriladi — xuddi Step 2 qilganidek.
    - Sukunat aniqlash uchun `dubbing.segmentation.vad` ishlatiladi (u esa
      o'zining mustaqil, izolyatsiya qilingan subprocess chaqiruviga ega
      — sabab vad.py docstring'ida tushuntirilgan).
    - Botning async ffmpeg runneri, uning vaqtinchalik-fayl helperi,
      asosiy bot vaqtinchalik-papka sozlamasi, navbat/task-boshqaruv
      modullari va handlers/ — bu yerda ISHLATILMAYDI.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import subprocess
from typing import Awaitable, Callable

from utils.ffmpeg_utils import run_ffmpeg

from dubbing.artifacts.store import ArtifactStore
from dubbing.config import (
    DUBBING_MAX_SEGMENT_SEC,
    DUBBING_MIN_SEGMENT_SEC,
    DUBBING_SEGMENTATION_TIMEOUT_SECONDS,
    DUBBING_SILENCE_MIN_DURATION_SEC,
    DUBBING_SILENCE_THRESHOLD_DB,
)
from dubbing.media.tempfiles import cleanup_job_work_dir, job_work_dir
from dubbing.models.types import JobRecord
from dubbing.segmentation.boundaries import segment_audio
from dubbing.segmentation.vad import detect_silence_intervals

logger = logging.getLogger("dubbing.segmentation.segmenter")

STAGE = "segmentation"
WORKING_AUDIO_SAMPLE_RATE = 16000
WORKING_AUDIO_CHANNELS = 1
ENGINE_NAME = "ffmpeg_silencedetect"


class MissingIngestionArtifactError(RuntimeError):
    """Job uchun mos ingestion artifact topilmadi yoki uning params'i
    to'liq emas — segmentatsiya davom eta olmaydi."""


def _ffmpeg_version() -> str:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=15
        )
        first_line = result.stdout.splitlines()[0] if result.stdout else ""
        parts = first_line.split()
        return parts[2] if len(parts) >= 3 else "unknown"
    except Exception:
        return "unknown"


async def _extract_working_audio(input_path: str, output_path: str) -> None:
    """
    Ingestion'dagi `_extract_working_audio` bilan bir xil ffmpeg
    parametrlari (mono, 16kHz, PCM s16le) — mustaqil nusxa, ingestion
    kodiga bog'liq emas.
    """
    loop = asyncio.get_running_loop()
    args = [
        "-i", input_path,
        "-vn",
        "-ac", str(WORKING_AUDIO_CHANNELS),
        "-ar", str(WORKING_AUDIO_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        output_path,
    ]
    ok, err = await loop.run_in_executor(
        None,
        functools.partial(run_ffmpeg, args, DUBBING_SEGMENTATION_TIMEOUT_SECONDS),
    )
    if not ok:
        if "Vaqt tugadi" in err:
            raise TimeoutError(f"Audio qayta ekstraktsiya timeout: {err}")
        raise RuntimeError(f"Audio qayta ekstraktsiya muvaffaqiyatsiz: {err}")


def make_segmentation_handler(pool) -> Callable[[JobRecord], Awaitable[None]]:
    """
    Qaytarilgan handler `WorkerLoop.register_handler("segmentation", handler)`
    orqali ro'yxatdan o'tkaziladi.

    `job.input_hash` ingestion artifact'ning `content_hash`'iga teng
    bo'lishi kutiladi — job yaratilganda
    `create_job(..., input_hash=ingestion_artifact.content_hash, ...)`
    orqali shunday o'rnatiladi.
    """
    store = ArtifactStore(pool)

    async def handler(job: JobRecord) -> None:
        if not job.input_hash:
            raise MissingIngestionArtifactError(
                f"Job {job.id}: input_hash yo'q — ingestion artifact aniqlab bo'lmaydi"
            )

        ingestion_artifact = await store.lookup(job.episode_id, "ingestion", job.input_hash)
        if ingestion_artifact is None:
            raise MissingIngestionArtifactError(
                f"Job {job.id}: ingestion artifact topilmadi "
                f"(episode_id={job.episode_id}, input_hash={job.input_hash})"
            )

        source_path = ingestion_artifact.params.get("source_path")
        duration_sec = ingestion_artifact.params.get("duration_sec")
        if not source_path or duration_sec is None or duration_sec <= 0:
            raise MissingIngestionArtifactError(
                f"Job {job.id}: ingestion artifact params to'liq emas yoki yaroqsiz "
                f"(source_path={source_path!r}, duration_sec={duration_sec!r})"
            )

        work_dir = job_work_dir(STAGE, job.id)

        try:
            working_audio_path = os.path.join(work_dir, "working_audio_16k_mono.wav")
            await _extract_working_audio(source_path, working_audio_path)

            loop = asyncio.get_running_loop()
            silence_intervals = await loop.run_in_executor(
                None,
                functools.partial(
                    detect_silence_intervals,
                    working_audio_path,
                    duration_sec,
                    DUBBING_SILENCE_THRESHOLD_DB,
                    DUBBING_SILENCE_MIN_DURATION_SEC,
                    DUBBING_SEGMENTATION_TIMEOUT_SECONDS,
                ),
            )

            segments = segment_audio(
                duration_sec,
                silence_intervals,
                DUBBING_MIN_SEGMENT_SEC,
                DUBBING_MAX_SEGMENT_SEC,
            )

            engine_version = _ffmpeg_version()
            vad_params = {
                "silence_threshold_db": DUBBING_SILENCE_THRESHOLD_DB,
                "silence_min_duration_sec": DUBBING_SILENCE_MIN_DURATION_SEC,
                "min_segment_sec": DUBBING_MIN_SEGMENT_SEC,
                "max_segment_sec": DUBBING_MAX_SEGMENT_SEC,
            }
            params = {
                "segments": segments,
                "segment_count": len(segments),
                "vad_params": vad_params,
                "duration_sec": duration_sec,
                "sample_rate": WORKING_AUDIO_SAMPLE_RATE,
                "channels": WORKING_AUDIO_CHANNELS,
            }

            content_hash = store.compute_hash(
                [ingestion_artifact.content_hash], ENGINE_NAME, engine_version, params
            )

            await store.register(
                episode_id=job.episode_id,
                stage=STAGE,
                content_hash=content_hash,
                engine_name=ENGINE_NAME,
                engine_version=engine_version,
                params=params,
                producing_job_id=job.id,
                parent_artifact_ids=[ingestion_artifact.id],
                storage_key=None,
            )

        except Exception:
            logger.warning(
                "Segmentation muvaffaqiyatsiz (job=%s) — ish papkasi debugging uchun saqlanadi: %s",
                job.id, work_dir,
            )
            raise
        else:
            cleanup_job_work_dir(STAGE, job.id)

    return handler
