"""
atomic_json.py — JSON holat fayllarini xavfsiz (atomic) yozish/o'qish.

Muammo: oddiy `open(path, "w")` + `json.dump(...)` bilan yozish atomik emas.
Agar bot process shu yozish davomida to'xtab qolsa (crash, OOM, deploy
paytida SIGTERM, server o'chishi), fayl **yarim yozilgan** holda qolib
ketadi. Keyingi o'qishda `json.JSONDecodeError` chiqadi va chaqiruvchi kod
odatda buni yutib, bo'sh `{}` bilan davom etadi — natijada studiya
bog'lanishlari, tokenlar, R2 pending yozuvlari kabi muhim holat MA'LUMOTI
BUTUNLAY YO'QOLADI.

Yechim: avval vaqtinchalik faylga to'liq yozib, so'ng `os.replace()` bilan
asl faylga almashtiramiz. `os.replace()` POSIX tizimlarida atomik — fayl
har doim yo eski, yoki to'liq yangi holatda bo'ladi, hech qachon yarim
yozilgan holatda emas.
"""

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def load_json(path: str, default=None):
    """JSON faylni o'qiydi. Fayl yo'q yoki buzilgan bo'lsa `default` qaytadi."""
    if default is None:
        default = {}
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("JSON o'qish xato (%s): %s — standart qiymat ishlatiladi.", path, e)
        return default


def save_json(path: str, data) -> bool:
    """
    JSON faylni atomik tarzda yozadi: avval `<path>.tmp` ga to'liq yozib,
    so'ng `os.replace()` bilan asl faylga almashtiradi. Shu bilan bot
    yozish o'rtasida to'xtab qolsa ham, asl fayl hech qachon yarim
    yozilgan/buzilgan holatda qolmaydi.
    """
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
        return True
    except OSError as e:
        logger.warning("JSON saqlash xato (%s): %s", path, e)
        return False
