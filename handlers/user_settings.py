"""
Foydalanuvchi sozlamalari — context.user_data ichida saqlanadi.
Bot restart bo'lsa tozalanadi (DB yo'q, yengil yechim).
"""

DEFAULTS = {
    "upload_mode": "document",   # "document" | "video" | "audio"
    "rename_file": False,         # True = faylni qayta nomlash
    "custom_thumbnail": None,     # file_id yoki None
    "sample_duration": 30,        # Generate Sample uchun soniya
    "split_duration": 60,         # Video Splitter uchun soniya
}


def get(context, key: str):
    settings = context.user_data.setdefault("settings", {})
    return settings.get(key, DEFAULTS[key])


def set_(context, key: str, value):
    context.user_data.setdefault("settings", {})[key] = value


def reset(context):
    context.user_data["settings"] = dict(DEFAULTS)


def summary(context) -> str:
    s = context.user_data.get("settings", {})
    upload = s.get("upload_mode", DEFAULTS["upload_mode"])
    rename = s.get("rename_file", DEFAULTS["rename_file"])
    thumb = "✅ Bor" if s.get("custom_thumbnail") else "❌ Yo'q"
    sample = s.get("sample_duration", DEFAULTS["sample_duration"])
    split = s.get("split_duration", DEFAULTS["split_duration"])

    upload_label = {"document": "📄 Dokument", "video": "🎬 Video", "audio": "🎵 Audio"}.get(upload, upload)
    rename_label = "✅ Yoqiq" if rename else "❌ O'chiq"
    return (
        f"⚙️ *Joriy sozlamalar:*\n\n"
        f"📤 Upload rejimi: {upload_label}\n"
        f"✏️ Fayl nomini o'zgartirish: {rename_label}\n"
        f"🖼 Custom thumbnail: {thumb}\n"
        f"🎬 Sample davomiyligi: `{sample}` soniya\n"
        f"✂️ Split davomiyligi: `{split}` soniya\n"
    )
