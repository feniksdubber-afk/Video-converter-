"""
dubbing/media/tempfiles.py — DUBBING_TEMP_DIR ostida job-scoped vaqtinchalik
papkalar.

IZOLYATSIYA: bu modul faqat dubbing.config.DUBBING_TEMP_DIR'dan foydalanadi.
Mavjud botning asosiy vaqtinchalik-papka sozlamasi yoki uning ffmpeg
vaqtinchalik-fayl helperi bilan HECH QANDAY aloqasi yo'q.
"""

from __future__ import annotations

import os
import shutil

from dubbing.config import DUBBING_TEMP_DIR


def job_work_dir(stage: str, job_id: int) -> str:
    """
    Berilgan stage/job uchun DUBBING_TEMP_DIR/<stage>/<job_id>/ papkasini
    yaratadi (mavjud bo'lmasa) va yo'lini qaytaradi.
    """
    path = os.path.join(DUBBING_TEMP_DIR, stage, str(job_id))
    os.makedirs(path, exist_ok=True)
    return path


def cleanup_job_work_dir(stage: str, job_id: int) -> None:
    """Muvaffaqiyatli yakunlangan job uchun vaqtinchalik papkani o'chiradi.
    Xato holatlarda BU FUNKSIYA chaqirilmasligi kerak — debugging uchun
    papka DUBBING_TEMP_DIR/<stage>/<job_id>/ ostida qoldiriladi."""
    path = os.path.join(DUBBING_TEMP_DIR, stage, str(job_id))
    shutil.rmtree(path, ignore_errors=True)
