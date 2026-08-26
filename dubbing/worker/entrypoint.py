"""
dubbing/worker/entrypoint.py — dubbing-worker process entrypoint.

`python -m dubbing.worker.entrypoint` sifatida alohida OS processi
tarzida ishga tushiriladi (supervisord orqali, `[program:dubbing-worker]`)
— botning asosiy `python bot.py` processidan MUSTAQIL. Ikkalasi bir xil
konteynerda, lekin alohida processlarda ishlaydi: biri crash bo'lsa,
ikkinchisiga ta'sir qilmaydi (supervisord har birini alohida
avtomatik qayta ishga tushiradi).

DUBBING_ENABLED=false bo'lsa: bu process HECH QANDAY DB pool yaratmaydi,
worker yoki reaper ishga tushirmaydi — darhol exit code 0 bilan chiqadi.
Bu supervisordga uni "muvaffaqiyatli tugagan" deb ko'rsatadi (xato emas),
shuning uchun `autorestart=true` bo'lsa ham restart-loop bo'lib
qolmaydi — supervisord `exitcodes=0` holatini kutilgan tugash deb qabul
qiladi, keyingi urinishda ham xuddi shu tarzda darhol chiqadi.

IZOLYATSIYA: bu modul faqat `dubbing.*` importlaridan foydalanadi.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import asyncpg

from dubbing.config import (
    DUBBING_CLAIM_POLL_INTERVAL_SECONDS,
    DUBBING_DATABASE_URL,
    DUBBING_ENABLED,
    DUBBING_LEASE_SECONDS,
    DUBBING_LOG_LEVEL,
    DUBBING_REAPER_INTERVAL_SECONDS,
    DUBBING_WORKER_CONCURRENCY,
)
from dubbing.media.ingestion import make_ingestion_handler
from dubbing.media.r2_resolver import make_r2_input_path_resolver
from dubbing.segmentation.segmenter import make_segmentation_handler
from dubbing.worker.base import WorkerLoop
from dubbing.worker.reaper import run_reaper_loop

logger = logging.getLogger("dubbing.worker.entrypoint")

# Step 4 doirasida ishlaydigan bosqichlar. Keyingi bosqichlar (transcription,
# diarization, ...) o'z handlerlari yozilganda shu ro'yxatga qo'shiladi —
# bu Step 5+ ishi, bu faylga tegishli emas.
STAGES = ["ingestion", "segmentation"]


async def _async_main() -> None:
    pool = await asyncpg.create_pool(
        DUBBING_DATABASE_URL,
        min_size=1,
        max_size=max(2, DUBBING_WORKER_CONCURRENCY + 1),
    )

    worker = WorkerLoop(
        pool,
        stages=STAGES,
        lease_seconds=DUBBING_LEASE_SECONDS,
        poll_interval_seconds=DUBBING_CLAIM_POLL_INTERVAL_SECONDS,
    )
    worker.register_handler(
        "ingestion", make_ingestion_handler(pool, make_r2_input_path_resolver(pool))
    )
    worker.register_handler("segmentation", make_segmentation_handler(pool))

    reaper_stop_event = asyncio.Event()
    reaper_task = asyncio.create_task(
        run_reaper_loop(pool, DUBBING_REAPER_INTERVAL_SECONDS, reaper_stop_event)
    )

    # Bitta signal handler ikkalasini ham to'xtatadi — WorkerLoop o'zining
    # ichki install_signal_handlers()'idan FOYDALANMAYMIZ, chunki reaper
    # loop'i alohida stop_event'ga ega va ikkalasi birga to'xtashi kerak.
    def _stop_all() -> None:
        logger.info("To'xtatish signali qabul qilindi — worker va reaper to'xtatilmoqda...")
        worker.request_stop()
        reaper_stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop_all)
        except NotImplementedError:
            # Ba'zi platformalarda (masalan Windows) qo'llab-quvvatlanmaydi.
            pass

    try:
        await worker.run()
    finally:
        reaper_stop_event.set()
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
        await pool.close()
        logger.info("dubbing-worker to'liq to'xtatildi (pool yopildi).")


def main() -> None:
    logging.basicConfig(
        level=DUBBING_LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not DUBBING_ENABLED:
        logger.info(
            "DUBBING_ENABLED=false — dubbing-worker ishga tushmaydi (DB pool "
            "yaratilmaydi, worker/reaper ishga tushirilmaydi), chiqilmoqda (0)."
        )
        return
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
