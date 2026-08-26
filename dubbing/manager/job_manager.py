"""
dubbing/manager/job_manager.py — Job state machine.

IZOLYATSIYA: bu modul mavjud botning utils/task_queue.py bilan HECH QANDAY
aloqasi yo'q — uni import qilmaydi, undan hech narsa meros olmaydi. Bu
butunlay yangi, Postgres-backed, restart-safe job tizimi.

Holatlar: queued -> processing -> completed
                          |
                          +--(fail, attempts < max)--> queued
                          +--(fail, attempts >= max yoki oom)--> failed
          har qanday non-terminal holat --(cancel)--> cancelled
          processing --(lease muddati tugasa, reaper)--> queued
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import asyncpg

from dubbing.models.enums import JobErrorKind, JobStatus
from dubbing.models.types import JobRecord

logger = logging.getLogger("dubbing.job_manager")


class JobManager:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # ── Yaratish ──────────────────────────────────────────────────────────

    async def create_job(
        self,
        episode_id: int,
        stage: str,
        input_hash: Optional[str] = None,
        depends_on_job_id: Optional[int] = None,
        priority: int = 100,
        max_attempts: int = 3,
    ) -> JobRecord:
        """
        Idempotent: bir xil (episode_id, stage, input_hash) uchun ikkinchi
        chaqiruv mavjud jobni qaytaradi, yangisini yaratmaydi.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO jobs
                    (episode_id, stage, input_hash, depends_on_job_id, priority, max_attempts)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (episode_id, stage, input_hash) DO NOTHING
                RETURNING *
                """,
                episode_id, stage, input_hash, depends_on_job_id, priority, max_attempts,
            )
            if row is None:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM jobs
                    WHERE episode_id = $1 AND stage = $2 AND input_hash IS NOT DISTINCT FROM $3
                    """,
                    episode_id, stage, input_hash,
                )
            return JobRecord.from_row(row)

    # ── Claim / lease ────────────────────────────────────────────────────

    async def claim_next_job(
        self,
        worker_id: str,
        stages: Iterable[str],
        lease_seconds: int,
    ) -> Optional[JobRecord]:
        """
        Atomik ravishda navbatdagi eng ustuvor jobni ushlaydi.
        `FOR UPDATE SKIP LOCKED` orqali bir nechta worker bir xil jobni
        ikki marta ushlamasligini kafolatlaydi.
        """
        stages_list = list(stages)
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'queued' AND stage = ANY($1::text[])
                    ORDER BY priority DESC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    stages_list,
                )
                if row is None:
                    return None

                updated = await conn.fetchrow(
                    """
                    UPDATE jobs
                    SET status = 'processing',
                        leased_by = $2,
                        lease_expires_at = $3,
                        started_at = COALESCE(started_at, now())
                    WHERE id = $1
                    RETURNING *
                    """,
                    row["id"], worker_id, lease_expires_at,
                )
                return JobRecord.from_row(updated)

    async def renew_lease(self, job_id: int, worker_id: str, lease_seconds: int) -> bool:
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE jobs
                SET lease_expires_at = $3
                WHERE id = $1 AND leased_by = $2 AND status = 'processing'
                """,
                job_id, worker_id, lease_expires_at,
            )
            return result.endswith("1")

    # ── Yakunlash ────────────────────────────────────────────────────────

    async def complete_job(self, job_id: int, worker_id: str) -> bool:
        """
        Faqat shu job hali ham `worker_id` tomonidan lease qilingan bo'lsa
        yakunlaydi — reap qilingan va boshqa workerga o'tgan jobni "zombi"
        worker tomonidan yakunlanishining oldini oladi.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE jobs
                SET status = 'completed', finished_at = now()
                WHERE id = $1 AND leased_by = $2 AND status = 'processing'
                """,
                job_id, worker_id,
            )
            success = result.endswith("1")
            if not success:
                logger.warning(
                    "complete_job muvaffaqiyatsiz: job=%s worker=%s (lease boshqa workerga "
                    "o'tgan yoki job allaqachon terminal holatda)", job_id, worker_id,
                )
            return success

    async def fail_job(
        self,
        job_id: int,
        worker_id: str,
        error: str,
        error_kind: str = JobErrorKind.EXCEPTION.value,
    ) -> JobRecord:
        """
        attempts++ ; agar attempts < max_attempts VA error_kind != 'oom'
        bo'lsa -> queued (retry), aks holda -> failed.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT * FROM jobs WHERE id = $1 AND leased_by = $2 FOR UPDATE
                    """,
                    job_id, worker_id,
                )
                if row is None:
                    raise ValueError(
                        f"fail_job: job {job_id} worker {worker_id} tomonidan lease "
                        f"qilinmagan (allaqachon reap qilingan bo'lishi mumkin)"
                    )

                new_attempts = row["attempts"] + 1
                should_retry = (
                    new_attempts < row["max_attempts"]
                    and error_kind != JobErrorKind.OOM.value
                )
                new_status = JobStatus.QUEUED.value if should_retry else JobStatus.FAILED.value

                updated = await conn.fetchrow(
                    """
                    UPDATE jobs
                    SET attempts = $2,
                        status = $3,
                        error = $4,
                        error_kind = $5,
                        leased_by = CASE WHEN $3 = 'queued' THEN NULL ELSE leased_by END,
                        lease_expires_at = CASE WHEN $3 = 'queued' THEN NULL ELSE lease_expires_at END,
                        finished_at = CASE WHEN $3 = 'failed' THEN now() ELSE finished_at END
                    WHERE id = $1
                    RETURNING *
                    """,
                    job_id, new_attempts, new_status, error, error_kind,
                )
                return JobRecord.from_row(updated)

    async def cancel_job(self, job_id: int) -> JobRecord:
        """
        Jobni bekor qiladi va unga bog'liq barcha downstream jobларни —
        `depends_on_job_id` zanjiri bo'ylab necha bosqich uzoqlikda bo'lishidan
        qat'iy nazar — rekursiv ravishda kaskadli bekor qiladi.

        Rekursiv CTE `depends_on_job_id` bo'yicha to'liq bog'liqlik zanjirini
        (ingestion -> transcription -> diarization -> analysis -> mixing -> QC
        kabi necha bosqich bo'lishidan qat'iy nazar) bitta atomik so'rovda
        topadi va yangilaydi. Faqat terminal bo'lmagan holatdagi (queued,
        processing) jobлар o'zgartiriladi — completed/failed/cancelled
        jobларга hech qachon tegilmaydi.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Berilgan job mavjudmi tekshirib, keyin butun zanjirni bitta
                # atomik so'rovda bekor qilamiz (o'zi ham, barcha rekursiv
                # downstream avlodlari ham).
                exists = await conn.fetchval("SELECT 1 FROM jobs WHERE id = $1", job_id)
                if exists is None:
                    return None  # type: ignore[return-value]

                rows = await conn.fetch(
                    """
                    WITH RECURSIVE descendants AS (
                        SELECT id FROM jobs WHERE id = $1
                        UNION
                        SELECT j.id
                        FROM jobs j
                        JOIN descendants d ON j.depends_on_job_id = d.id
                    )
                    UPDATE jobs
                    SET status = 'cancelled', finished_at = now()
                    WHERE id IN (SELECT id FROM descendants)
                      AND status NOT IN ('completed', 'failed', 'cancelled')
                    RETURNING *
                    """,
                    job_id,
                )

                target_row = next((r for r in rows if r["id"] == job_id), None)
                if target_row is not None:
                    return JobRecord.from_row(target_row)

                # Berilgan job allaqachon terminal holatda edi (o'zgartirilmadi) —
                # joriy holatini o'qib qaytaramiz.
                row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
                return JobRecord.from_row(row)

    # ── O'qish ───────────────────────────────────────────────────────────

    async def get_job(self, job_id: int) -> Optional[JobRecord]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
            return JobRecord.from_row(row) if row else None

    async def get_jobs_for_episode(self, episode_id: int) -> list[JobRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM jobs WHERE episode_id = $1 ORDER BY created_at ASC",
                episode_id,
            )
            return [JobRecord.from_row(row) for row in rows]
