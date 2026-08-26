"""
dubbing/worker/reaper.py — Muddati o'tgan lease'larni tozalash.

Worker crash bo'lsa yoki server restart bo'lsa, `processing` holatida
qolib ketgan, lekin lease muddati o'tgan joblarni avtomatik ravishda
qaytadan `queued` holatiga o'tkazadi — hech qanday job abadiy
"osilib qolmaydi".
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

logger = logging.getLogger("dubbing.reaper")


async def reap_expired_leases(pool: asyncpg.Pool) -> int:
    """
    Bir marta ishga tushib, lease muddati o'tgan barcha jobларни
    `queued` holatiga qaytaradi. Qaytadan tiklangan joblar sonini
    qaytaradi (kuzatuv/log uchun).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE jobs
            SET status = 'queued', leased_by = NULL, lease_expires_at = NULL
            WHERE status = 'processing' AND lease_expires_at < now()
            RETURNING id, leased_by
            """
        )
        for row in rows:
            logger.warning(
                "Reap qilindi: job=%s (avvalgi worker muddati tugadi)", row["id"],
            )
        return len(rows)


async def run_reaper_loop(pool: asyncpg.Pool, interval_seconds: int, stop_event: asyncio.Event) -> None:
    """
    `stop_event` o'rnatilguncha davriy ravishda `reap_expired_leases`ni
    chaqirib turadi. Graceful shutdown uchun mo'ljallangan.
    """
    logger.info("Reaper loop boshlandi (interval=%ss)", interval_seconds)
    while not stop_event.is_set():
        try:
            reaped = await reap_expired_leases(pool)
            if reaped:
                logger.info("Reaper: %d ta job qaytadan navbatga qo'yildi", reaped)
        except Exception:
            logger.exception("Reaper tsiklida xato")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass
    logger.info("Reaper loop to'xtatildi")
