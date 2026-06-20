"""
SQLite orqali foydalanuvchi sozlamalarini saqlash.
Railway da /data volume, local da ./data papkasi ishlatiladi.
"""
import aiosqlite
import json
import os
from config import DB_PATH

DEFAULTS = {
    "upload_mode":       "document",
    "rename_file":       0,
    "custom_thumbnail":  None,
    "sample_duration":   30,
    "split_duration":    60,
    # "auto" — R2 sozlangan bo'lsa R2, aks holda Gofile (eski xulq-atvor)
    # "telegram" — Premium userbot orqali Telegram'ga (4GB gacha)
    # "r2" — majburan R2
    # "gofile" — majburan Gofile
    "large_file_dest":   "auto",
    # ── Save Restricted yuklab olish sozlamalari ──────────────────────────
    # 1 = parallel (bir vaqtda 2 ta fayl), 0 = ketma-ket (1 ta fayl)
    # Parallel rejimda flood wait ehtimoli yuqori — sekin bo'lsa o'chiring.
    "sr_parallel":       1,
    # Har bir chunk (bo'lak) yuklanib bo'lgandan keyin kutish (soniya).
    # 0.0 = to'liq tezlik; 0.5 = tavsiya; 1.0 = sekin/xavfsiz; 2.0+ = juda sekin.
    # Telegram flood limit bilan kurashish uchun ko'paytiring.
    "sr_chunk_delay":    0.0,
}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id          INTEGER PRIMARY KEY,
    upload_mode      TEXT    NOT NULL DEFAULT 'document',
    rename_file      INTEGER NOT NULL DEFAULT 0,
    custom_thumbnail TEXT             DEFAULT NULL,
    sample_duration  INTEGER NOT NULL DEFAULT 30,
    split_duration   INTEGER NOT NULL DEFAULT 60,
    large_file_dest  TEXT    NOT NULL DEFAULT 'auto',
    sr_parallel      INTEGER NOT NULL DEFAULT 1,
    sr_chunk_delay   REAL    NOT NULL DEFAULT 0.0,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_BATCH_TABLE = """
CREATE TABLE IF NOT EXISTS batch_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    steps      TEXT    NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_SAVED_MEDIA_TABLE = """
CREATE TABLE IF NOT EXISTS saved_media (
    source_chat_id   INTEGER NOT NULL,
    source_msg_id    INTEGER NOT NULL,
    dest_chat_id     INTEGER,
    dest_thread_id   INTEGER,
    saved_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_chat_id, source_msg_id, dest_thread_id)
)
"""


async def init_db():
    """Bot ishga tushganda bitta marta chaqiriladi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_BATCH_TABLE)
        await db.execute(_CREATE_SAVED_MEDIA_TABLE)
        # Migratsiya: eski (yangilanishdan oldingi) bazalarda yangi ustun
        # bo'lmasligi mumkin — bo'lsa xato yutiladi (ustun allaqachon bor).
        try:
            await db.execute(
                "ALTER TABLE user_settings ADD COLUMN large_file_dest "
                "TEXT NOT NULL DEFAULT 'auto'"
            )
        except Exception:
            pass
        try:
            await db.execute(
                "ALTER TABLE user_settings ADD COLUMN sr_parallel "
                "INTEGER NOT NULL DEFAULT 1"
            )
        except Exception:
            pass
        try:
            await db.execute(
                "ALTER TABLE user_settings ADD COLUMN sr_chunk_delay "
                "REAL NOT NULL DEFAULT 0.0"
            )
        except Exception:
            pass
        await db.commit()


# ── Batch template DB funksiyalari ────────────────────────────────────────────

async def db_load_batch_templates(user_id: int) -> list[dict]:
    """Foydalanuvchining barcha batch shablonlarini yuklaydi."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, steps FROM batch_templates WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
    return [
        {"id": row["id"], "name": row["name"], "steps": json.loads(row["steps"])}
        for row in rows
    ]


async def db_save_batch_template(user_id: int, name: str, steps: list[str]) -> int:
    """Yangi batch shablonini saqlaydi. ID qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO batch_templates (user_id, name, steps) VALUES (?, ?, ?)",
            (user_id, name, json.dumps(steps))
        )
        await db.commit()
        return cursor.lastrowid


async def db_delete_batch_template(user_id: int, template_id: int) -> None:
    """Batch shablonini o'chiradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM batch_templates WHERE id = ? AND user_id = ?",
            (template_id, user_id)
        )
        await db.commit()


async def db_load(user_id: int) -> dict:
    """User sozlamalarini DBdan yuklaydi (yoki default qaytaradi)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        return dict(DEFAULTS)

    return {
        "upload_mode":       row["upload_mode"],
        "rename_file":       bool(row["rename_file"]),
        "custom_thumbnail":  row["custom_thumbnail"],
        "sample_duration":   row["sample_duration"],
        "split_duration":    row["split_duration"],
        "large_file_dest":   row["large_file_dest"] if "large_file_dest" in row.keys() else "auto",
    }


# SQL injection dan himoya: faqat shu ustun nomlari ruxsat etiladi
_ALLOWED_COLUMNS: frozenset[str] = frozenset(DEFAULTS.keys())


async def db_set(user_id: int, key: str, value) -> None:
    """Bitta sozlamani DBga saqlaydi (INSERT OR REPLACE + partial update)."""
    if key not in _ALLOWED_COLUMNS:
        # Noto'g'ri kalit — logga yozamiz va chiqib ketamiz
        import logging
        logging.getLogger(__name__).warning("db_set: noto'g'ri kalit rad etildi: %r", key)
        return
    if isinstance(value, bool):
        value = int(value)

    async with aiosqlite.connect(DB_PATH) as db:
        # Avval mavjud yozuvni tekshiramiz
        async with db.execute(
            "SELECT user_id FROM user_settings WHERE user_id = ?", (user_id,)
        ) as cur:
            exists = await cur.fetchone()

        if exists:
            await db.execute(
                f"UPDATE user_settings SET {key} = ?, updated_at = CURRENT_TIMESTAMP "
                f"WHERE user_id = ?",
                (value, user_id),
            )
        else:
            # Yangi foydalanuvchi — default bilan qo'shamiz
            defaults = dict(DEFAULTS)
            defaults[key] = value
            await db.execute(
                "INSERT INTO user_settings "
                "(user_id, upload_mode, rename_file, custom_thumbnail, sample_duration, split_duration, large_file_dest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    defaults["upload_mode"],
                    int(defaults["rename_file"]),
                    defaults["custom_thumbnail"],
                    defaults["sample_duration"],
                    defaults["split_duration"],
                    defaults["large_file_dest"],
                ),
            )
        await db.commit()


async def db_reset(user_id: int) -> None:
    """User sozlamalarini default ga qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_settings "
            "(user_id, upload_mode, rename_file, custom_thumbnail, sample_duration, split_duration, large_file_dest) "
            "VALUES (?, 'document', 0, NULL, 30, 60, 'auto')",
            (user_id,),
        )
        await db.commit()


# ── /save dublikat oldini olish ──────────────────────────────────────────────
# dest_thread_id NULL bo'lsa PRIMARY KEY solishtirishda muammo chiqarmasligi
# uchun 0 ga normallashtiramiz (forum bo'lmagan chat uchun thread_id 0 bo'ladi).

def _norm_thread(dest_thread_id: int | None) -> int:
    return dest_thread_id if dest_thread_id is not None else 0


async def is_already_saved(source_chat_id: int, source_msg_id: int, dest_thread_id: int | None) -> bool:
    """Shu xabar shu manzilga (topicga) avval saqlanganmi — tekshiradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM saved_media WHERE source_chat_id = ? AND source_msg_id = ? AND dest_thread_id = ?",
            (source_chat_id, source_msg_id, _norm_thread(dest_thread_id)),
        ) as cursor:
            row = await cursor.fetchone()
    return row is not None


async def mark_saved(
    source_chat_id: int, source_msg_id: int,
    dest_chat_id: int | None, dest_thread_id: int | None,
) -> None:
    """Muvaffaqiyatli yuborilgan xabarni dublikat-tekshiruv jadvaliga yozadi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO saved_media "
            "(source_chat_id, source_msg_id, dest_chat_id, dest_thread_id) VALUES (?, ?, ?, ?)",
            (source_chat_id, source_msg_id, dest_chat_id, _norm_thread(dest_thread_id)),
        )
        await db.commit()
