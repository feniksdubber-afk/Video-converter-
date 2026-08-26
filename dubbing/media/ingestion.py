"""
dubbing/media/ingestion.py — "ingestion" bosqichi uchun WorkerLoop handler.

Oqim:
    validate_input_file
    -> probe_media (ffprobe)
    -> audio track yo'q yoki duration <= 0 bo'lsa -> hard-fail
    -> ffmpeg orqali downstream ishchi audio artifact ajratib olinadi:
       mono, 16kHz, PCM WAV (Whisper/diarization uchun qulay format)
    -> ArtifactStore.register(...) (stage='ingestion', parent yo'q)
    -> muvaffaqiyatli bo'lsa vaqtinchalik ish papkasi o'chiriladi
    -> xato bo'lsa vaqtinchalik ish papkasi DEBUGGING uchun SAQLANADI

MUHIM: original manba media fayl HECH QACHON o'chirilmaydi, ustiga
yozilmaydi yoki o'zgartirilmaydi. Bu yerda yaratiladigan 16kHz mono WAV —
faqat downstream ishchi artifact; original stereo/multi-channel audio
kelajakdagi bosqichlar (separation, mixing) uchun manba sifatida saqlanib
qoladi va bu modul unga hech qanday tarzda tegmaydi.

IZOLYATSIYA: faqat utils.ffmpeg_utils.run_ffmpeg (sof, sinxron) import
qilinadi, executor orqali chaqiriladi. Botning async ffmpeg runneri, uning
vaqtinchalik-fayl helperi, asosiy bot vaqtinchalik-papka sozlamasi, navbat/
task-boshqaruv modullari va handlers/ — bu yerda ISHLATILMAYDI.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from typing import Awaitable, Callable

from utils.ffmpeg_utils import run_ffmpeg

from dubbing.artifacts.store import ArtifactStore
from dubbing.config import DUBBING_INGEST_TIMEOUT_SECONDS
from dubbing.media.probe import probe_media
from dubbing.media.tempfiles import cleanup_job_work_dir, job_work_dir
from dubbing.media.validation import InvalidInputMediaError, validate_input_file
from dubbing.models.types import ArtifactRecord, JobRecord

logger = logging.getLogger("dubbing.media.ingestion")

STAGE = "ingestion"
WORKING_AUDIO_SAMPLE_RATE = 16000
WORKING_AUDIO_CHANNELS = 1
ENGINE_NAME = "ffmpeg_ingest"

InputPathResolver = Callable[[JobRecord], Awaitable[str]]


class NoAudioTrackError(ValueError):
    """Kirish faylida audio track topilmadi — dubbing uchun davom etib
    bo'lmaydi."""


class InvalidDurationError(ValueError):
    """ffprobe orqali aniqlangan davomiylik <= 0 — fayl buzilgan yoki
    o'qib bo'lmaydi."""


def _ffmpeg_version() -> str:
    try:
        import subprocess
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=15)
        first_line = result.stdout.splitlines()[0] if result.stdout else ""
        # Misol: "ffmpeg version 6.1.1-...". Faqat versiya tokenini olamiz.
        parts = first_line.split()
        return parts[2] if len(parts) >= 3 else "unknown"
    except Exception:
        return "unknown"


async def _extract_working_audio(input_path: str, output_path: str) -> None:
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
        None, functools.partial(run_ffmpeg, args, DUBBING_INGEST_TIMEOUT_SECONDS)
    )
    if not ok:
        if "Vaqt tugadi" in err:
            raise TimeoutError(f"Audio ekstraktsiya timeout: {err}")
        raise RuntimeError(f"Audio ekstraktsiya muvaffaqiyatsiz: {err}")


def make_ingestion_handler(
    pool,
    input_path_resolver: InputPathResolver,
) -> Callable[[JobRecord], Awaitable[None]]:
    """
    `input_path_resolver(job) -> str` — berilgan job uchun original manba
    media faylining mahalliy diskdagi yo'lini qaytaruvchi callable. Step 2
    doirasida R2/bot integratsiyasi yo'q, shuning uchun bu resolver'ni
    chaqiruvchi (masalan, test yoki keyingi bosqich) ta'minlaydi.

    Qaytarilgan handler WorkerLoop.register_handler("ingestion", handler)
    orqali ro'yxatdan o'tkaziladi.
    """
    store = ArtifactStore(pool)

    async def handler(job: JobRecord) -> None:
        input_path = await input_path_resolver(job)
        work_dir = job_work_dir(STAGE, job.id)

        try:
            validate_input_file(input_path)

            probe = await probe_media(input_path)

            if probe.duration_sec <= 0:
                raise InvalidDurationError(
                    f"Yaroqsiz yoki nol davomiylik: {probe.duration_sec}"
                )
            if not probe.audio_tracks:
                raise NoAudioTrackError("Kirish faylida audio track topilmadi")

            working_audio_path = os.path.join(work_dir, "working_audio_16k_mono.wav")
            await _extract_working_audio(input_path, working_audio_path)

            engine_version = _ffmpeg_version()
            params = {
                **probe.to_params_dict(),
                "sample_rate": WORKING_AUDIO_SAMPLE_RATE,
                "channels": WORKING_AUDIO_CHANNELS,
                "source_path": input_path,
            }
            content_hash = store.compute_hash([], ENGINE_NAME, engine_version, params)

            await store.register(
                episode_id=job.episode_id,
                stage=STAGE,
                content_hash=content_hash,
                engine_name=ENGINE_NAME,
                engine_version=engine_version,
                params=params,
                producing_job_id=job.id,
                storage_key=None,  # R2 yuklash Step 2 doirasidan tashqarida
            )

        except Exception:
            logger.warning(
                "Ingestion muvaffaqiyatsiz (job=%s) — ish papkasi debugging uchun saqlanadi: %s",
                job.id, work_dir,
            )
            raise
        else:
            cleanup_job_work_dir(STAGE, job.id)

    return handler
