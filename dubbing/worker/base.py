"""
dubbing/worker/base.py — Worker lifecycle (Step 1: faqat placeholder handler).

Step 1 doirasida bu yerda HAQIQIY media-processing engine ulanmagan.
`WorkerLoop` faqat quyidagi tsiklni sinovdan o'tkazish uchun mo'ljallangan:

    claim_next_job -> stage handler chaqirish -> complete_job / fail_job

Haqiqiy engine'lar (ingestion, transcription, ...) keyingi bosqichlarda
`register_handler()` orqali ulanadi — bu modulning o'zi o'zgarmaydi.

IZOLYATSIYA: bu modul mavjud botning utils/task_queue.py yoki boshqa hech
bir mavjud modulini import qilmaydi.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from typing import Awaitable, Callable, Dict, Optional

import asyncpg

from dubbing.manager.job_manager import JobManager
from dubbing.models.enums import JobErrorKind
from dubbing.models.types import JobRecord

logger = logging.getLogger("dubbing.worker")

StageHandler = Callable[[JobRecord], Awaitable[None]]


def _classify_exception(exc: BaseException) -> str:
    if isinstance(exc, MemoryError):
        return JobErrorKind.OOM.value
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return JobErrorKind.TIMEOUT.value
    return JobErrorKind.EXCEPTION.value


class WorkerLoop:
    """
    Bitta worker instansiyasining hayot sikli.

    `stages`: bu worker qaysi bosqich(lar)ni ushlashi mumkinligi.
    `lease_seconds`: har bir job uchun boshlang'ich lease muddati.
    `poll_interval_seconds`: navbat bo'sh bo'lganda keyingi urinishgacha kutish.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        stages: list[str],
        lease_seconds: int,
        poll_interval_seconds: float,
        worker_id: Optional[str] = None,
    ):
        self._pool = pool
        self._job_manager = JobManager(pool)
        self._stages = stages
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._handlers: Dict[str, StageHandler] = {}
        self._stop_event = asyncio.Event()

    def register_handler(self, stage: str, handler: StageHandler) -> None:
        self._handlers[stage] = handler

    def request_stop(self) -> None:
        self._stop_event.set()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except NotImplementedError:
                # Ba'zi platformalarda (masalan Windows) qo'llab-quvvatlanmaydi;
                # worker baribir stop_event orqali to'xtatilishi mumkin.
                pass

    async def run_once(self) -> bool:
        """
        Bitta jobni ushlab, uni qayta ishlaydi. Job topilmasa False qaytaradi.
        Testlar uchun qulay — to'liq loop'ni ishga tushirmasdan bitta
        iteratsiyani sinash imkonini beradi.
        """
        job = await self._job_manager.claim_next_job(
            self.worker_id, self._stages, self._lease_seconds,
        )
        if job is None:
            return False

        handler = self._handlers.get(job.stage)
        if handler is None:
            await self._job_manager.fail_job(
                job.id, self.worker_id,
                error=f"'{job.stage}' bosqichi uchun handler ro'yxatdan o'tmagan",
                error_kind=JobErrorKind.EXCEPTION.value,
            )
            return True

        try:
            await handler(job)
        except Exception as exc:  # noqa: BLE001 — worker istalgan engine xatosini ushlashi kerak
            error_kind = _classify_exception(exc)
            logger.exception("Job %s ishlov berishda xato (stage=%s)", job.id, job.stage)
            await self._job_manager.fail_job(job.id, self.worker_id, str(exc), error_kind)
        else:
            await self._job_manager.complete_job(job.id, self.worker_id)
        return True

    async def run(self) -> None:
        logger.info(
            "WorkerLoop boshlandi: worker_id=%s stages=%s", self.worker_id, self._stages,
        )
        while not self._stop_event.is_set():
            try:
                found = await self.run_once()
            except Exception:
                logger.exception("WorkerLoop tsiklida kutilmagan xato")
                found = False
            if not found:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        logger.info("WorkerLoop to'xtatildi: worker_id=%s", self.worker_id)
