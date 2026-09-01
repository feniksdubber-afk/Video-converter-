"""
Studiya guruhi topic'lari orqali ommaviy yuklash uchun navbat.

Oqim: manager topic ichiga video(lar)ni tashlaydi (serial bo'lsa caption'ga
"fasl-qism", masalan "2-5" deb yozadi) -- bot HAR BIRINI darhol ishlamaydi,
faqat shu yerga "navbatga" yozib qo'yadi. Manager /joylash buyrug'ini
bergach, handlers/studio_topic_upload.py shu navbatni ketma-ket qayta ishlaydi.

Saqlanadi: studio_topic_queue.json -> slug -> topic_id(str) -> [item, ...]
item: {message_id, season, episode, file_id, added_at}
Film uchun (kind="m") season=episode=0 (bitta yagona item).
"""

import logging
import os
import time

from config import DATA_DIR
from utils.atomic_json import load_json, save_json

logger = logging.getLogger(__name__)

_FILE = os.path.join(DATA_DIR, "studio_topic_queue.json")

_queue: dict[str, dict[str, list[dict]]] = {}
_loaded = False


def _load() -> None:
    global _queue, _loaded
    _queue = load_json(_FILE, default={})
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def _save() -> None:
    save_json(_FILE, _queue)


def add_item(
    slug: str, topic_id: int, message_id: int, season: int, episode: int, file_id: str,
) -> tuple[bool, str]:
    """Navbatga bitta video qo'shadi.
    Qaytaradi: (muvaffaqiyatli, xato_matni_yoki_bosh_satr).
    Bir xil fasl+qism allaqachon navbatda bo'lsa -- rad etiladi (xato bilan)."""
    _ensure_loaded()
    tkey = str(topic_id)
    items = _queue.setdefault(slug, {}).setdefault(tkey, [])
    for it in items:
        if it["season"] == season and it["episode"] == episode:
            return False, (
                f"{season}-fasl {episode}-qism allaqachon navbatda "
                f"(xabar #{it['message_id']}). Avval o'shani /navbat orqali "
                f"tekshiring yoki to'g'ri raqamni yozing."
            )
    items.append({
        "message_id": message_id,
        "season": season,
        "episode": episode,
        "file_id": file_id,
        "added_at": int(time.time()),
    })
    _save()
    return True, ""


def get_queue(slug: str, topic_id: int) -> list[dict]:
    _ensure_loaded()
    items = _queue.get(slug, {}).get(str(topic_id), [])
    return sorted(items, key=lambda it: (it["season"], it["episode"]))


def queue_totals() -> tuple[int, int]:
    """Barcha studiyalar bo'yicha jami navbat holati.
    Qaytaradi: (jami video, band topic'lar soni) -- /status kabi umumiy
    diagnostika uchun, har bir studiyani alohida-alohida so'ramasdan."""
    _ensure_loaded()
    total_items = 0
    active_topics = 0
    for topics in _queue.values():
        for items in topics.values():
            if items:
                active_topics += 1
                total_items += len(items)
    return total_items, active_topics


def clear_queue(slug: str, topic_id: int) -> None:
    _ensure_loaded()
    _queue.get(slug, {}).pop(str(topic_id), None)
    _save()


def remove_item(slug: str, topic_id: int, message_id: int) -> bool:
    _ensure_loaded()
    items = _queue.get(slug, {}).get(str(topic_id))
    if not items:
        return False
    before = len(items)
    items[:] = [it for it in items if it["message_id"] != message_id]
    if len(items) != before:
        _save()
        return True
    return False
