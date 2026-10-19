"""
disk_guard.py -- disk to'lib qolishidan himoya.

MUAMMO (4 soatlik "Formatga tayyorlash" qotishi): `/status` TEMP_DIR'da atigi
6 GB ko'rsatgan, lekin diskning o'zi 72.4/74.8 GB (bo'sh 0 bayt) edi. Qolgan
~66 GB local `telegram-bot-api --local` serverining `/data/tgbotapi/<token>/...`
papkasida (Telegram'dan yuklab olingan ASL videolar -- server ularni hech qachon
o'zi o'chirmaydi) va dubbing vaqtinchalik papkasida to'planib qolgan. Disk 0
bo'lganda ffmpeg yozish operatsiyasida osilib qoladi.

Bu modul:
  * har qanday papka hajmini hisoblaydi (status uchun),
  * eski fayllarni xavfsiz o'chiradi (telegram-bot-api'ning o'z baza fayllariga
    TEGMAYDI -- faqat token papkasi ichidagi quyi papkalardagi media fayllar),
  * `ensure_free_space()` -- katta amaldan oldin joy yetarli ekanini
    tekshiradi, kerak bo'lsa shoshilinch tozalaydi.
"""

import logging
import os
import shutil
import time

from config import TEMP_DIR

logger = logging.getLogger(__name__)

TGBOTAPI_DIR = os.environ.get("TGBOTAPI_DIR", "/data/tgbotapi")
DUBBING_TEMP_DIR = os.environ.get("DUBBING_TEMP_DIR", "/tmp/afsona_dubbing")

# Disk bu qiymatdan kam bo'sh qolsa avtomatik shoshilinch tozalash ishga tushadi.
MIN_FREE_BYTES = int(float(os.environ.get("MIN_FREE_GB", "3")) * 1024 ** 3)


class DiskFullError(Exception):
    """Diskda yetarli joy yo'q (tozalashdan keyin ham). Qayta urinish
    foydasiz -- shuning uchun `_RETRYABLE_EXC`ga KIRMAYDI."""


def fmt_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def free_bytes(path: str = TEMP_DIR) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 1 << 60  # o'lchab bo'lmasa to'smaymiz


def dir_size(path: str) -> tuple[int, int]:
    """(fayllar soni, umumiy hajm) -- rekursiv."""
    count = total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
                count += 1
            except OSError:
                pass
    return count, total


def _remove_old(root: str, max_age: float, *, recursive: bool, min_depth: int = 0,
                skip_names: tuple = ()) -> tuple[int, int]:
    now = time.time()
    removed = freed = 0
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return 0, 0
    for cur, dirs, files in os.walk(root):
        depth = 0 if cur == root else cur[len(root):].count(os.sep)
        if not recursive:
            dirs[:] = []
        for f in files:
            if depth < min_depth or f in skip_names:
                continue
            p = os.path.join(cur, f)
            try:
                st = os.lstat(p)
                if now - st.st_mtime > max_age:
                    os.remove(p)
                    removed += 1
                    freed += st.st_size
            except OSError:
                pass
    return removed, freed


def cleanup_all(*, temp_age: float, tgapi_age: float) -> tuple[int, int]:
    """TEMP_DIR + dubbing temp + telegram-bot-api media fayllarini tozalaydi.
    Qaytaradi: (o'chirilgan fayllar, bo'shagan baytlar)."""
    n = b = 0
    for r, f in (
        _remove_old(TEMP_DIR, temp_age, recursive=False),
        _remove_old(DUBBING_TEMP_DIR, temp_age, recursive=True),
        # /data/tgbotapi/<token>/db.* kabi baza fayllari token papkasining
        # O'ZIDA (depth 0) -- ularga tegmaymiz. Media quyi papkalarda
        # (videos/, documents/, ...) -- depth >= 1. `tmp/` ham shu qatorda.
        _remove_old(TGBOTAPI_DIR, tgapi_age, recursive=True, min_depth=2),
    ):
        n += r
        b += f
    if n:
        logger.info("🧹 disk_guard: %s ta fayl, %s bo'shatildi", n, fmt_size(b))
    return n, b


def emergency_cleanup() -> tuple[int, int]:
    """Disk to'lganda: 20 daqiqadan eski hamma narsa o'chiriladi."""
    return cleanup_all(temp_age=20 * 60, tgapi_age=20 * 60)


def ensure_free_space(needed_bytes: int) -> None:
    """Kamida `needed_bytes + MIN_FREE_BYTES` bo'sh joy bo'lishini ta'minlaydi.
    Yetmasa avval shoshilinch tozalaydi, baribir yetmasa DiskFullError."""
    need = int(needed_bytes) + MIN_FREE_BYTES
    if free_bytes() >= need:
        return
    emergency_cleanup()
    free = free_bytes()
    if free < need:
        raise DiskFullError(
            f"Diskda joy yetarli emas: bo'sh {fmt_size(free)}, kerak ~{fmt_size(need)}. "
            "Admin /tozalash yuborsin yoki Volume hajmini oshiring."
        )


def remove_local_botapi_source(path: str) -> None:
    """Local Bot API bergan ASL faylni (nusxa olingach) o'chiradi --
    faqat TGBOTAPI_DIR ichidagi fayllar uchun."""
    try:
        real = os.path.realpath(path)
        base = os.path.realpath(TGBOTAPI_DIR) + os.sep
        if real.startswith(base) and os.path.isfile(real):
            os.remove(real)
    except OSError:
        pass
