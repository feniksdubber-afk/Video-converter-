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
