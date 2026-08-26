"""
Studiya guruhlari: har bir studiya o'ziga tegishli Telegram guruhini
(Topics/Forum yoqilgan) botga bog'laydi. Shu guruhda har bir film/serial
uchun bot avtomatik alohida topic ochadi va kontentni shu yerda saqlaydi.

Saqlanadigan fayllar:
  studio_groups.json  -> slug -> {chat_id, title, bound_by, bound_at}
  studio_topics.json  -> slug -> {"m_{movieId}" | "s_{seriesId}": topic_id}
  studio_posted.json  -> slug -> {content_key: {sub_key: message_id}}
"""

import logging
import os
import time

from config import DATA_DIR
from utils.atomic_json import load_json, save_json

logger = logging.getLogger(__name__)

_GROUPS_FILE = os.path.join(DATA_DIR, "studio_groups.json")
_TOPICS_FILE = os.path.join(DATA_DIR, "studio_topics.json")

_groups: dict[str, dict] = {}   # slug -> {chat_id, title, bound_by, bound_at}
_topics: dict[str, dict] = {}   # slug -> {content_key -> topic_id}
_loaded = False


def _load() -> None:
    global _groups, _topics, _loaded
    _groups = load_json(_GROUPS_FILE, default={})
    _topics = load_json(_TOPICS_FILE, default={})
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def _save_groups() -> None:
    save_json(_GROUPS_FILE, _groups)


def _save_topics() -> None:
    save_json(_TOPICS_FILE, _topics)


def get_group(slug: str) -> dict | None:
    _ensure_loaded()
    return _groups.get(slug)


def get_slug_by_chat_id(chat_id: int) -> str | None:
    _ensure_loaded()
    for slug, g in _groups.items():
        if g.get("chat_id") == chat_id:
            return slug
    return None


def bind_group(slug: str, chat_id: int, title: str, bound_by: int) -> None:
    _ensure_loaded()
    _groups[slug] = {
        "chat_id": chat_id,
        "title": title,
        "bound_by": bound_by,
        "bound_at": int(time.time()),
    }
    _save_groups()


def unbind_group(slug: str) -> bool:
    _ensure_loaded()
    if slug in _groups:
        del _groups[slug]
        _save_groups()
        _topics.pop(slug, None)
        _save_topics()
        return True
    return False


def get_topic_id(slug: str, content_key: str) -> int | None:
    _ensure_loaded()
    return _topics.get(slug, {}).get(content_key)


def get_content_key_by_topic(slug: str, topic_id: int) -> str | None:
    """Topic_id -> content_key teskari qidiruvi (masalan topic ichida
    yuborilgan videoni qaysi film/serialga tegishli ekanini bilish uchun)."""
    _ensure_loaded()
    for key, tid in _topics.get(slug, {}).items():
        if tid == topic_id:
            return key
    return None


def list_groups() -> dict[str, dict]:
    """Barcha bog'langan studiya guruhlari: slug -> {chat_id, title, ...}."""
    _ensure_loaded()
    return dict(_groups)


def set_topic_id(slug: str, content_key: str, topic_id: int) -> None:
    _ensure_loaded()
    _topics.setdefault(slug, {})[content_key] = topic_id
    _save_topics()


def clear_topic_id(slug: str, content_key: str) -> None:
    """Topic Telegram'da o'chirilgan bo'lsa chaqiriladi -- keshni tozalab,
    ensure_topic() keyingi safar yangisini yaratishiga imkon beradi."""
    _ensure_loaded()
    if slug in _topics and content_key in _topics[slug]:
        del _topics[slug][content_key]
        _save_topics()


_POSTED_FILE = os.path.join(DATA_DIR, "studio_posted.json")
# slug -> {content_key: {sub_key: message_id}}
# Eski format slug -> {content_key: [sub_key, ...]} edi -- _load_posted() buni
# avtomatik {sub_key: None} ga o'giradi (message_id noma'lum, tekshirib bo'lmaydi).
_posted: dict[str, dict] = {}
_posted_loaded = False


def _load_posted() -> None:
    global _posted, _posted_loaded
    raw = load_json(_POSTED_FILE, default={})
    migrated = {}
    for slug, content_map in raw.items():
        migrated[slug] = {}
        for content_key, subs in content_map.items():
            if isinstance(subs, list):
                # eski format -- message_id noma'lum
                migrated[slug][content_key] = {sub_key: None for sub_key in subs}
            elif isinstance(subs, dict):
                migrated[slug][content_key] = subs
    _posted = migrated
    _posted_loaded = True


def _save_posted() -> None:
    save_json(_POSTED_FILE, _posted)


def is_episode_posted(slug: str, content_key: str, sub_key: str) -> bool:
    if not _posted_loaded:
        _load_posted()
    return sub_key in _posted.get(slug, {}).get(content_key, {})


def get_posted_message_id(slug: str, content_key: str, sub_key: str) -> int | None:
    """sub_key oldin joylangan bo'lsa, uning Telegram message_id'sini qaytaradi
    (mavjudligini/formatini tekshirish uchun). Eski yozuvlarda None bo'lishi mumkin."""
    if not _posted_loaded:
        _load_posted()
    return _posted.get(slug, {}).get(content_key, {}).get(sub_key)


def mark_episode_posted(slug: str, content_key: str, sub_key: str, message_id: int | None = None) -> None:
    if not _posted_loaded:
        _load_posted()
    d = _posted.setdefault(slug, {}).setdefault(content_key, {})
    d[sub_key] = message_id
    _save_posted()


def unmark_episode_posted(slug: str, content_key: str, sub_key: str) -> None:
    """Qismni 'yuborilmagan' holatiga qaytaradi -- masalan xabar Telegramda
    o'chirilgan yoki noto'g'ri formatda (document) ekani aniqlanganda."""
    if not _posted_loaded:
        _load_posted()
    d = _posted.get(slug, {}).get(content_key, {})
    d.pop(sub_key, None)
    _save_posted()
