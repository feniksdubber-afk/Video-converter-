"""
Foydalanuvchi sozlamalari — SQLite (doimiy saqlash) + context.user_data (tezkor kesh).

API:
    await ensure_loaded(user_id, context)   — DBdan keshga yuklaydi (1 marta per session)
    get(context, key)                        — sync, keshdan o'qiydi
    await set_(user_id, context, key, value) — kesh + DB ga yozadi
    await reset(user_id, context)            — default ga qaytaradi
    summary(context)                         — sozlamalar matni (sync)
"""
from utils.db import db_load, db_set, db_reset, DEFAULTS


async def ensure_loaded(user_id: int, context) -> None:
    """
    Agar bu sessiyada hali yuklanmagan bo'lsa, DBdan sozlamalarni yuklaydi.
    Har safar chaqirsa ham xavfsiz (ikkinchi marta shart yo'q).
    """
    if context.user_data.get("_settings_loaded"):
        return
    data = await db_load(user_id)
    context.user_data["settings"] = data
    context.user_data["_settings_loaded"] = True


def get(context, key: str):
    """Keshdan o'qiydi. ensure_loaded dan keyin ishlatiladi."""
    settings = context.user_data.get("settings", {})
    return settings.get(key, DEFAULTS.get(key))


async def set_(user_id: int, context, key: str, value) -> None:
    """Kesh va DBga yozadi."""
    context.user_data.setdefault("settings", {})[key] = value
    await db_set(user_id, key, value)


async def reset(user_id: int, context) -> None:
    """Default sozlamalarni qaytaradi — kesh va DB."""
    await db_reset(user_id)
    context.user_data["settings"] = dict(DEFAULTS)


def summary(context) -> str:
    """Sozlamalar matnini qaytaradi (sync, keshdan)."""
    s = context.user_data.get("settings", {})
    upload  = s.get("upload_mode",      DEFAULTS["upload_mode"])
    rename  = s.get("rename_file",      DEFAULTS["rename_file"])
    thumb   = s.get("custom_thumbnail", DEFAULTS["custom_thumbnail"])
    sample  = s.get("sample_duration",  DEFAULTS["sample_duration"])
    split   = s.get("split_duration",   DEFAULTS["split_duration"])
    largefd = s.get("large_file_dest",  DEFAULTS["large_file_dest"])
    sr_par  = s.get("sr_parallel",      DEFAULTS["sr_parallel"])

    upload_label = {"document": "📄 Dokument", "video": "🎬 Video", "audio": "🎵 Audio"}.get(upload, upload)
    rename_label = "✅ Yoqiq" if rename else "❌ O'chiq"
    thumb_label  = "✅ Bor" if thumb else "❌ Yo'q"
    largefd_label = {
        "auto": "🔄 Avto (2GB+ → Premium bo'lsa Telegram, aks holda R2/Gofile)",
        "telegram": "✈️ Telegram (Premium, 4GB)",
        "r2": "☁️ R2",
        "gofile": "🌐 Gofile",
    }.get(largefd, largefd)
    sr_par_label = "✅ Yoqiq (aqlli: Premium bo'lsa 4, aks holda 2 parallel)" if sr_par else "❌ O'chiq (ketma-ket)"
    return (
        f"⚙️ *Joriy sozlamalar:*\n\n"
        f"📤 Upload rejimi: {upload_label}\n"
        f"✏️ Fayl nomini o'zgartirish: {rename_label}\n"
        f"🖼 Custom thumbnail: {thumb_label}\n"
        f"🎬 Sample davomiyligi: `{sample}` soniya\n"
        f"✂️ Split davomiyligi: `{split}` soniya\n"
        f"📦 2GB+ fayllar: {largefd_label}\n"
        f"\n📥 *Save Restricted:*\n"
        f"🔀 Parallel yuklash: {sr_par_label}\n"
        f"⚡ Tezlik: `Avtomatik (aqlli)` — bot o'zi eng tezini tanlaydi,\n"
        f"   flood tez-tez tushsagina o'zi sekinlashtiradi.\n"
    )
