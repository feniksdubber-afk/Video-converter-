"""
dubbing_bridge.py — bot.py va `dubbing/*` paketi orasidagi YAGONA ko'prik.

IZOLYATSIYA QOIDASI (Step 1-4 bilan bir xil): `dubbing/*` ichkarisidagi
hech qanday modul bu faylni yoki bot.py/utils.*ni import qilmaydi. Bog'lanish
faqat BIR TOMONLAMA: shu fayl ikkalasini biladi, ular bir-birini bilmaydi.

Bu fayl bot ildizida turadi (bot.py bilan bir papkada) — `dubbing/` paketi
ichida EMAS, aynan shuning uchun.
"""

from __future__ import annotations

import logging
import os
import uuid

from dubbing.config import DUBBING_ENABLED
from dubbing.database.connection import get_pool
from dubbing.manager.job_manager import JobManager
from dubbing.models.enums import JobStage

logger = logging.getLogger("dubbing_bridge")


class DubbingDisabledError(RuntimeError):
    """DUBBING_ENABLED=false bo'lganda /dub oqimi chaqirilsa ko'tariladi."""


async def start_dubbing_job(
    *,
    project_name: str,
    original_r2_key: str,
    created_by: int,
    duration_sec: float | None = None,
) -> tuple[int, int]:
    """
    Yangi episode yaratadi va birinchi (ingestion) job'ini navbatga qo'yadi.

    Qaytaradi: (episode_id, job_id)

    Bu funksiya faqat episode yaratadi + job navbatga qo'yadi — worker
    (alohida process, entrypoint.py) uni claim qilib qayta ishlaydi.
    Bu funksiya HECH QACHON media qayta ishlashni o'zi bajarmaydi.
    """
    if not DUBBING_ENABLED:
        raise DubbingDisabledError(
            "DUBBING_ENABLED=false — dubbing job yaratib bo'lmaydi."
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO episodes (project_name, original_r2_key, duration_sec, created_by)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            project_name, original_r2_key, duration_sec, created_by,
        )
    episode_id = row["id"]

    job_manager = JobManager(pool)
    job = await job_manager.create_job(
        episode_id=episode_id,
        stage=JobStage.INGESTION.value,
    )
    logger.info(
        "Dubbing job navbatga qo'yildi: episode_id=%s job_id=%s created_by=%s",
        episode_id, job.id, created_by,
    )
    return episode_id, job.id


async def _get_latest_artifact_hash(episode_id: int, stage: str) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT content_hash FROM artifacts
            WHERE episode_id = $1 AND stage = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            episode_id, stage,
        )
    return row["content_hash"] if row else None


async def advance_pipeline(episode_id: int) -> None:
    """
    Bosqichlar orasidagi zanjirlash (chaining).

    Hozirgi `dubbing/*` kodida (Step 1-4) bosqichlar orasida avtomatik
    o'tish yozilmagan — har bir bosqich tugagach KEYINGI job'ni kim
    navbatga qo'yishi ataylab bu ko'prikka (Step 5) qoldirilgan.

    Chaqirilganda: episode'ning joriy job holatlarini tekshiradi, agar
    bir bosqich 'completed' bo'lsa va keyingi bosqich uchun job hali
    yaratilmagan bo'lsa — uni yaratadi (input_hash = oldingi bosqich
    artifact'ining content_hash'i, xuddi segmenter.py docstringida
    ko'rsatilganidek).
    """
    pool = await get_pool()
    job_manager = JobManager(pool)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT stage, status FROM jobs WHERE episode_id = $1", episode_id,
        )
    existing = {r["stage"]: r["status"] for r in rows}

    # Hozircha yozilgan zanjir: ingestion -> segmentation -> transcription.
    # Keyingi Steplar (diarization, translation, ...) shu ro'yxatga
    # qo'shiladi.
    CHAIN = [
        (JobStage.INGESTION.value, JobStage.SEGMENTATION.value),
        (JobStage.SEGMENTATION.value, JobStage.TRANSCRIPTION.value),
    ]

    for prev_stage, next_stage in CHAIN:
        if existing.get(prev_stage) == "completed" and next_stage not in existing:
            content_hash = await _get_latest_artifact_hash(episode_id, prev_stage)
            await job_manager.create_job(
                episode_id=episode_id,
                stage=next_stage,
                input_hash=content_hash,
            )
            logger.info(
                "Zanjirlandi: episode_id=%s %s -> %s (input_hash=%s)",
                episode_id, prev_stage, next_stage, content_hash,
            )


LAST_IMPLEMENTED_STAGE = JobStage.TRANSCRIPTION.value
"""Hozircha yozilgan pipeline shu bosqichda tugaydi (Step 7+ qo'shilguncha).
Poll funksiyasi shu bosqich 'completed' bo'lguncha 'Tugallandi' demaydi."""


async def get_episode_progress(episode_id: int) -> list[dict]:
    """
    Berilgan episode uchun barcha job'larning joriy holatini qaytaradi
    (progress xabarini yangilash uchun ishlatiladi).

    Qaytadi: [{"stage": ..., "status": ..., "error": ...}, ...]
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT stage, status, error
            FROM jobs
            WHERE episode_id = $1
            ORDER BY created_at ASC
            """,
            episode_id,
        )
    return [dict(r) for r in rows]


def build_dub_r2_key(user_id: int, file_name: str) -> str:
    """
    Dubbing uchun yuklanadigan original faylning R2 kalitini quradi.
    Botning boshqa yuklash oqimlari bilan aralashmasligi uchun alohida
    prefiks: dubbing/<user_id>/<uuid>_<nom>
    """
    safe_name = os.path.basename(file_name)
    return f"dubbing/{user_id}/{uuid.uuid4().hex}_{safe_name}"
