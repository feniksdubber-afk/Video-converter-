"""
video_history.py — Foydalanuvchi videolari tarixini boshqaradi.

Tuzilma:
    context.user_data["video_history"] = [
        {"path": "/tmp/abc.mkv", "name": "video_1.mkv",       "label": "Asl video"},
        {"path": "/tmp/xyz.mkv", "name": "video_1_trimmed.mkv","label": "✂️ Kesilgan"},
        ...
    ]
    context.user_data["video_history_index"] = 1   ← hozir ishlatilayotgan versiya

Har doim video_path va video_name ham sinxronlanadi.
"""

import os


def push_version(context, path: str, name: str, label: str):
    """
    Yangi versiyani tarixga qo'shadi va joriy indeksni unga ko'chiradi.
    Eski joriy versiya (disk) o'chirilmaydi — foydalanuvchi qaytib tanlashi mumkin.
    """
    history: list = context.user_data.setdefault("video_history", [])
    index: int = context.user_data.get("video_history_index", len(history) - 1)

    # Joriy indeksdan keyin kelgan barcha versiyalarni o'chir
    # (foydalanuvchi eski versiyaga qaytib boshqa yo'nalishda tahrirlagan bo'lsa)
    for entry in history[index + 1:]:
        _safe_remove(entry["path"])
    del history[index + 1:]

    history.append({"path": path, "name": name, "label": label})
    new_index = len(history) - 1
    context.user_data["video_history_index"] = new_index

    # video_path / video_name ni ham yangilash
    context.user_data["video_path"] = path
    context.user_data["video_name"] = name


def init_history(context, path: str, name: str):
    """
    Birinchi video kelganda tarixni yangi boshlatadi.
    Avvalgi tarixdagi barcha fayllarni o'chiradi.
    """
    old_history: list = context.user_data.get("video_history", [])
    for entry in old_history:
        _safe_remove(entry["path"])

    context.user_data["video_history"] = [
        {"path": path, "name": name, "label": "📁 Asl video"}
    ]
    context.user_data["video_history_index"] = 0
    context.user_data["video_path"] = path
    context.user_data["video_name"] = name


def get_history(context) -> list:
    return context.user_data.get("video_history", [])


def get_current_index(context) -> int:
    history = get_history(context)
    return context.user_data.get("video_history_index", len(history) - 1)


def switch_to(context, index: int) -> bool:
    """
    Berilgan indeksdagi versiyaga o'tadi.
    Fayl mavjud bo'lsa True, bo'lmasa False qaytaradi.
    """
    history = get_history(context)
    if index < 0 or index >= len(history):
        return False
    entry = history[index]
    if not os.path.exists(entry["path"]):
        return False
    context.user_data["video_history_index"] = index
    context.user_data["video_path"] = entry["path"]
    context.user_data["video_name"] = entry["name"]
    return True


def cleanup_except_current(context):
    """Joriy versiyadan boshqalarini diskdan o'chiradi."""
    history = get_history(context)
    index = get_current_index(context)
    for i, entry in enumerate(history):
        if i != index:
            _safe_remove(entry["path"])
    if 0 <= index < len(history):
        context.user_data["video_history"] = [history[index]]
        context.user_data["video_history_index"] = 0


def _safe_remove(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
