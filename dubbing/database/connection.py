"""
dubbing/database/connection.py — Dubbing PostgreSQL bazasiga lazy ulanish.

IZOLYATSIYA: bu modul faqat dubbing.config'dan DUBBING_DATABASE_URL'ni
o'qiydi. Mavjud botning aiosqlite/utils.db bilan hech qanday aloqasi yo'q va
ularni import qilmaydi.

FAIL-SOFT: ulanish "lazy" — modul import qilinganda hech qanday tarmoq
so'rovi yubormaydi. Ulanish faqat birinchi haqiqiy chaqiruvda (get_pool())
sodir bo'ladi, shuning uchun Postgres mavjud bo'lmagan holatda ham bu
modulni import qilish xavfsiz (masalan, kelajakda bot startup paytida
tasodifan import qilinsa ham, ulanish urinilmaguncha xato chiqarmaydi).
"""

import asyncio
import logging
from typing import Optional

import asyncpg

from dubbing.config import DUBBING_DATABASE_URL

logger = logging.getLogger("dubbing.database")

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


async def get_pool(dsn: Optional[str] = None) -> asyncpg.Pool:
    """
    Global connection pool'ni lazy tarzda yaratadi va qaytaradi.

    `dsn` parametri asosan testlar uchun — production kodida chaqirilganda
    har doim `dubbing.config.DUBBING_DATABASE_URL` ishlatiladi.
    """
    global _pool
    async with _pool_lock:
        if _pool is None:
            target = dsn or DUBBING_DATABASE_URL
            logger.info("Dubbing Postgres pool ochilmoqda...")
            _pool = await asyncpg.create_pool(target, min_size=1, max_size=10)
        return _pool


async def close_pool() -> None:
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None
