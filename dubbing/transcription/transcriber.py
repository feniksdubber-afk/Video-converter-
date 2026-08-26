"""
dubbing/transcription/transcriber.py — "transcription" bosqichi uchun
WorkerLoop handler.

Oqim:
    job.input_hash orqali segmentation artifact topiladi
    -> segmentation artifact params'idan segment ro'yxati olinadi
       (faqat vaqt oralig'lari — segmentation audio faylni saqlamaydi)
    -> segmentation'ning parent (ingestion) artifact'idan original manba
       yo'li (source_path) olinadi
    -> mono/16kHz PCM WAV mustaqil qayta hosil qilinadi (Step 2/3 bilan
       bir xil ffmpeg parametrlari, mustaqil nusxa — izolyatsiya qoidasi)
    -> faster-whisper orqali TO'LIQ audio bir marta transkripsiya qilinadi
       (Whisper o'zining ichki VAD/segmentatsiyasidan foydalanadi — bu
       Step 3'ning silencedetect segmentlaridan MUSTAQIL, chunki Whisper
       so'z darajasidagi aniq vaqt belgilarini o'zi hisoblaydi)
    -> natija: [{start, end, text}, ...] ro'yxati artifact sifatida
       saqlanadi (stage='transcription', parent=segmentation artifact)
    -> muvaffaqiyatli bo'lsa vaqtinchalik ish papkasi o'chiriladi
    -> xato bo'lsa vaqtinchalik ish papkasi DEBUGGING uchun SAQLANADI

IZOLYATSIYA:
    - Bu modul `dubbing.segmentation.*` yoki `dubbing.media.ingestion`dan
      HECH NARSA import qilmaydi — faqat `dubbing.artifacts.store` orqali
      ularning natijalarini (artifact) o'qiydi.
    - `faster-whisper` modeli shu modul ichida mustaqil yuklanadi va
      keshlanadi (worker process ichida bir marta, qayta ishlatiladi).
    - Botning mavjud hech qanday transkripsiya/AI kodiga bog'liq emas
      (bot hozircha bunday funksiyaga ega emas).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import subprocess
from typing import Awaitable, Callable, Optional

from utils.ffmpeg_utils import run_ffmpeg

from dubbing.artifacts.store import ArtifactStore
from dubbing.config import (
    DUBBING_TRANSCRIPTION_TIMEOUT_SECONDS,
    DUBBING_WHISPER_COMPUTE_TYPE,
    DUBBING_WHISPER_LANGUAGE,
    DUBBING_WHISPER_MODEL_SIZE,
)
from dubbing.media.tempfiles import cleanup_job_work_dir, job_work_dir
from dubbing.models.types import JobRecord

logger = logging.getLogger("dubbing.transcription.transcriber")

STAGE = "transcription"
WORKING_AUDIO_SAMPLE_RATE = 16000
WORKING_AUDIO_CHANNELS = 1
ENGINE_NAME = "faster_whisper"

# Model — worker process ichida LAZY va BIR MARTA yuklanadi (yuklash
# bir necha soniya-daqiqa olishi mumkin, har job uchun qayta yuklamaslik
# uchun modul darajasida keshlanadi).
_model = None
_model_lock = asyncio.Lock()


class MissingSegmentationArtifactError(RuntimeError):
    """Job uchun mos segmentation (yoki uning parent ingestion) artifact
    topilmadi — transkripsiya davom eta olmaydi."""


async def _get_model():
    global _model
    async with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            logger.info(
                "Whisper modeli yuklanmoqda: size=%s compute_type=%s (CPU)",
                DUBBING_WHISPER_MODEL_SIZE, DUBBING_WHISPER_COMPUTE_TYPE,
            )
            loop = asyncio.get_running_loop()
            _model = await loop.run_in_executor(
                None,
                functools.partial(
                    WhisperModel,
                    DUBBING_WHISPER_MODEL_SIZE,
                    device="cpu",
                    compute_type=DUBBING_WHISPER_COMPUTE_TYPE,
                ),
            )
            logger.info("Whisper modeli tayyor.")
    return _model


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
    """Ingestion/segmentation bilan bir xil ffmpeg parametrlari — mustaqil
    nusxa, ularning koduga bog'liq emas."""
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
        functools.partial(run_ffmpeg, args, DUBBING_TRANSCRIPTION_TIMEOUT_SECONDS),
    )
    if not ok:
        if "Vaqt tugadi" in err:
            raise TimeoutError(f"Audio qayta ekstraktsiya timeout: {err}")
        raise RuntimeError(f"Audio qayta ekstraktsiya muvaffaqiyatsiz: {err}")


def _run_whisper_sync(model, audio_path: str, language: Optional[str]) -> list[dict]:
    """SINXRON, BLOKLOVCHI — executor orqali chaqiriladi."""
    segments_iter, info = model.transcribe(
        audio_path,
        language=language,
        vad_filter=True,
    )
    result = []
    for seg in segments_iter:
        result.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })
    return result, info.language


def make_transcription_handler(pool) -> Callable[[JobRecord], Awaitable[None]]:
    """
    Qaytarilgan handler `WorkerLoop.register_handler("transcription", handler)`
    orqali ro'yxatdan o'tkaziladi.

    `job.input_hash` segmentation artifact'ning `content_hash`'iga teng
    bo'lishi kutiladi (dubbing_bridge.advance_pipeline() shunday qilib
    job yaratadi).
    """
    store = ArtifactStore(pool)

    async def handler(job: JobRecord) -> None:
        if not job.input_hash:
            raise MissingSegmentationArtifactError(
                f"Job {job.id}: input_hash yo'q — segmentation artifact aniqlab bo'lmaydi"
            )

        segmentation_artifact = await store.lookup(job.episode_id, "segmentation", job.input_hash)
        if segmentation_artifact is None:
            raise MissingSegmentationArtifactError(
                f"Job {job.id}: segmentation artifact topilmadi "
                f"(episode_id={job.episode_id}, input_hash={job.input_hash})"
            )
        if not segmentation_artifact.producing_job_id:
            raise MissingSegmentationArtifactError(
                f"Job {job.id}: segmentation artifact producing_job_id yo'q"
            )

        # Ingestion artifact — segmentation'ning parent'i (lineage orqali).
        parents = await store.get_lineage(segmentation_artifact.id)
        ingestion_artifact = next((p for p in parents if p.stage == "ingestion"), None)
        if ingestion_artifact is None:
            raise MissingSegmentationArtifactError(
                f"Job {job.id}: segmentation uchun ingestion parent artifact topilmadi"
            )

        source_path = ingestion_artifact.params.get("source_path")
        if not source_path:
            raise MissingSegmentationArtifactError(
                f"Job {job.id}: ingestion artifact'da source_path yo'q"
            )

        work_dir = job_work_dir(STAGE, job.id)

        try:
            working_audio_path = os.path.join(work_dir, "working_audio_16k_mono.wav")
            await _extract_working_audio(source_path, working_audio_path)

            model = await _get_model()
            loop = asyncio.get_running_loop()
            segments, detected_language = await loop.run_in_executor(
                None,
                functools.partial(
                    _run_whisper_sync, model, working_audio_path, DUBBING_WHISPER_LANGUAGE,
                ),
            )

            params = {
                "segments": segments,
                "segment_count": len(segments),
                "language": DUBBING_WHISPER_LANGUAGE or detected_language,
                "model_size": DUBBING_WHISPER_MODEL_SIZE,
                "compute_type": DUBBING_WHISPER_COMPUTE_TYPE,
            }

            content_hash = store.compute_hash(
                [segmentation_artifact.content_hash], ENGINE_NAME, DUBBING_WHISPER_MODEL_SIZE, params
            )

            await store.register(
                episode_id=job.episode_id,
                stage=STAGE,
                content_hash=content_hash,
                engine_name=ENGINE_NAME,
                engine_version=DUBBING_WHISPER_MODEL_SIZE,
                params=params,
                producing_job_id=job.id,
                parent_artifact_ids=[segmentation_artifact.id],
                storage_key=None,
            )

        except Exception:
            logger.warning(
                "Transkripsiya muvaffaqiyatsiz (job=%s) — ish papkasi debugging uchun saqlanadi: %s",
                job.id, work_dir,
            )
            raise
        else:
            cleanup_job_work_dir(STAGE, job.id)

    return handler
