"""
AfsonaMovieBot (asosiy platforma) bilan umumiy SQLite bazadan FAQAT O'QISH.

Bu modul orqali videokonverter bot berilgan Telegram ID qaysi studiya(lar)ga
"manager" sifatida biriktirilganini avtomatik aniqlaydi -- alohida
login/parol tizimi shart emas, chunki manejerlik allaqachon asosiy
platformada (mini-app / studio_members jadvalida) belgilangan.

Jadval zanjiri:
  verified_profiles.user_id (= Telegram ID)
      -> studio_members.verified_profile_id  (role = 'manager')
          -> studios.id / slug / name

Eslatma (performance): bu funksiya SINXRON SQLite so'rovi qiladi va
event loop'ni bloklaydi -- lokal, faqat o'qish uchun (mode=ro) fayl bo'lgani
uchun odatda millisekund ichida tugaydi, shuning uchun alohida oqim/executor
shart emas. Lekin bir nechta studiya menejeri bir vaqtda ko'plab video
yuklayotganda (har video xabari uchun bir marta chaqiriladi), bir xil
foydalanuvchi uchun qisqa vaqt ichida bir nechta marta chaqirilishi mumkin --
shuning uchun natija _CACHE_TTL soniya davomida keshlanadi va behuda
qayta-qayta bazaga murojaat qilinmaydi.
"""

import logging
import sqlite3
import time

from config import SHARED_DB_PATH

logger = logging.getLogger(__name__)

_QUERY = """
    SELECT s.id, s.slug, s.name
    FROM studio_members sm
    JOIN verified_profiles vp ON vp.id = sm.verified_profile_id
    JOIN users u              ON u.id  = vp.user_id
    JOIN studios s             ON s.id  = sm.studio_id
    WHERE u.tg_id = ? AND sm.role = 'manager' AND s.is_active = 1
    ORDER BY s.name
"""

_CACHE_TTL = 15.0  # soniya
_cache: dict[int, tuple[float, list[dict]]] = {}  # telegram_id -> (yaratilgan_vaqt, natija)


def get_manager_studios(telegram_id: int) -> list[dict]:
    """Berilgan Telegram ID qaysi studiya(lar)ga menejer sifatida
    biriktirilganini qaytaradi. Baza topilmasa yoki xato bo'lsa -- bo'sh ro'yxat.
    Natija qisqa muddat (_CACHE_TTL) keshlanadi -- ko'p menejer bir vaqtda
    faol ishlaganda ortiqcha bazaga murojaatlarni kamaytiradi."""
    now = time.monotonic()
    cached = _cache.get(telegram_id)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    if not SHARED_DB_PATH:
        logger.warning("SHARED_DB_PATH sozlanmagan -- studiya avtomatik aniqlanmaydi.")
        return []

    try:
        # mode=ro -- yozishga hech qachon urinilmaydi, faqat o'qish uchun ulanish.
        conn = sqlite3.connect(f"file:{SHARED_DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(_QUERY, (telegram_id,)).fetchall()
            result = [dict(r) for r in rows]
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning("Umumiy baza o'qishda xato (%s): %s", SHARED_DB_PATH, e)
        # Xato holatida eski keshni (agar bo'lsa) qaytarish -- vaqtinchalik
        # baza muammosi tufayli menejerni to'satdan "aniqlanmadi" qilib
        # qo'ymaslik uchun. Kesh umuman bo'lmasa -- bo'sh ro'yxat.
        return cached[1] if cached else []

    _cache[telegram_id] = (now, result)
    return result


def invalidate_manager_cache(telegram_id: int | None = None) -> None:
    """Keshni tozalaydi -- odatda kerak emas (TTL o'zi 15s da yangilanadi),
    lekin studiya a'zoligi darhol o'zgarganini aks ettirish kerak bo'lsa
    (masalan admin darhol menejerni olib tashlagach) qo'lda chaqirish mumkin."""
    if telegram_id is None:
        _cache.clear()
    else:
        _cache.pop(telegram_id, None)
