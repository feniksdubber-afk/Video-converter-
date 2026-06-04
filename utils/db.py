"""
SQLite orqali foydalanuvchi sozlamalarini saqlash.
Railway da /data volume, local da ./data papkasi ishlatiladi.
"""
import aiosqlite
import os
from config import DB_PATH

DEFAULTS = {
    "upload_mode":       "document",
    "rename_file":       0,
    "custom_thumbnail":  None,
    "sample_duration":   30,
    "split_duration":    60,
}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id          INTEGER PRIMARY KEY,
    upload_mode      TEXT    NOT NULL DEFAULT 'document',
    rename_file      INTEGER NOT NULL DEFAULT 0,
    custom_thumbnail TEXT             DEFAULT NULL,
    sample_duration  INTEGER NOT NULL DEFAULT 30,
    split_duration   INTEGER NOT NULL DEFAULT 60,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


async def init_db():
    """Bot ishga tushganda bitta marta chaqiriladi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_TABLE)
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
    }


async def db_set(user_id: int, key: str, value) -> None:
    """Bitta sozlamani DBga saqlaydi (INSERT OR REPLACE + partial update)."""
    if key not in DEFAULTS:
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
                "(user_id, upload_mode, rename_file, custom_thumbnail, sample_duration, split_duration) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    defaults["upload_mode"],
                    int(defaults["rename_file"]),
                    defaults["custom_thumbnail"],
                    defaults["sample_duration"],
                    defaults["split_duration"],
                ),
            )
        await db.commit()


async def db_reset(user_id: int) -> None:
    """User sozlamalarini default ga qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_settings "
            "(user_id, upload_mode, rename_file, custom_thumbnail, sample_duration, split_duration) "
            "VALUES (?, 'document', 0, NULL, 30, 60)",
            (user_id,),
        )
        await db.commit()
