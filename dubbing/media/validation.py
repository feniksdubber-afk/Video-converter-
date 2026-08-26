"""
dubbing/media/validation.py — ingestion uchun kirish faylini tekshirish.

Bu tekshiruvlar ffprobe chaqirilishidan OLDIN amalga oshiriladi — buzilgan
yoki mos kelmaydigan fayllarga ffmpeg/ffprobe resurslarini sarflamaslik
uchun.
"""

from __future__ import annotations

import os

from dubbing.config import DUBBING_MAX_INPUT_BYTES

ALLOWED_EXTENSIONS = {".mkv", ".mp4", ".mov", ".avi"}


class InvalidInputMediaError(ValueError):
    """Kirish media fayli ingestion uchun yaroqsiz."""


def validate_input_file(path: str) -> None:
    """
    Faylni tekshiradi: mavjudligi, nol bo'lmagan hajmi, ruxsat etilgan
    kengaytma, va maksimal hajm chegarasi. Muammo bo'lsa
    InvalidInputMediaError ko'taradi (ValueError subclass — WorkerLoop uni
    'exception' sifatida tasniflaydi va retry qoidalariga muvofiq
    ishlaydi).
    """
    if not os.path.isfile(path):
        raise InvalidInputMediaError(f"Kirish fayli topilmadi: {path}")

    size = os.path.getsize(path)
    if size == 0:
        raise InvalidInputMediaError(f"Kirish fayli bo'sh: {path}")
    if size > DUBBING_MAX_INPUT_BYTES:
        raise InvalidInputMediaError(
            f"Kirish fayli hajmi chegaradan katta: {size} > {DUBBING_MAX_INPUT_BYTES} bayt"
        )

    ext = os.path.splitext(path)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise InvalidInputMediaError(
            f"Qo'llab-quvvatlanmaydigan fayl kengaytmasi: '{ext}' "
            f"(ruxsat etilgan: {sorted(ALLOWED_EXTENSIONS)})"
        )
