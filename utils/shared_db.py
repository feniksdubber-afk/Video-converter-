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
"""

import logging
import sqlite3

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


def get_manager_studios(telegram_id: int) -> list[dict]:
    """Berilgan Telegram ID qaysi studiya(lar)ga menejer sifatida
    biriktirilganini qaytaradi. Baza topilmasa yoki xato bo'lsa -- bo'sh ro'yxat."""
    if not SHARED_DB_PATH:
        logger.warning("SHARED_DB_PATH sozlanmagan -- studiya avtomatik aniqlanmaydi.")
        return []

    try:
        # mode=ro -- yozishga hech qachon urinilmaydi, faqat o'qish uchun ulanish.
        conn = sqlite3.connect(f"file:{SHARED_DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(_QUERY, (telegram_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning("Umumiy baza o'qishda xato (%s): %s", SHARED_DB_PATH, e)
        return []
