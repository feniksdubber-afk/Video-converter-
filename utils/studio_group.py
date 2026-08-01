"""
Studiya guruhlari: har bir studiya o'ziga tegishli Telegram guruhini
(Topics/Forum yoqilgan) botga bog'laydi. Shu guruhda har bir film/serial
uchun bot avtomatik alohida topic ochadi va kontentni shu yerda saqlaydi.

Saqlanadigan fayllar:
  studio_groups.json  -> slug -> {chat_id, title, bound_by, bound_at}
  studio_topics.json  -> slug -> {"m_{movieId}" | "s_{seriesId}": topic_id}
"""

import json
import logging
import os
import time

from config import DATA_DIR

logger = logging.getLogger(__name__)

_GROUPS_FILE = os.path.join(DATA_DIR, "studio_groups.json")
_TOPICS_FILE = os.path.join(DATA_DIR, "studio_topics.json")

_groups: dict[str, dict] = {}   # slug -> {chat_id, title, bound_by, bound_at}
_topics: dict[str, dict] = {}   # slug -> {content_key -> topic_id}
_loaded = False


def _load() -> None:
    global _groups, _topics, _loaded
    if os.path.isfile(_GROUPS_FILE):
        try:
            with open(_GROUPS_FILE, encoding="utf-8") as f:
                _groups = json.load(f)
        except (OSError, json.JSONDecodeError):
            _groups = {}
    if os.path.isfile(_TOPICS_FILE):
        try:
            with open(_TOPICS_FILE, encoding="utf-8") as f:
                _topics = json.load(f)
        except (OSError, json.JSONDecodeError):
            _topics = {}
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def _save_groups() -> None:
    try:
        with open(_GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(_groups, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("studio_groups saqlash xato: %s", e)


def _save_topics() -> None:
    try:
        with open(_TOPICS_FILE, "w", encoding="utf-8") as f:
            json.dump(_topics, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("studio_topics saqlash xato: %s", e)


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


def set_topic_id(slug: str, content_key: str, topic_id: int) -> None:
    _ensure_loaded()
    _topics.setdefault(slug, {})[content_key] = topic_id
    _save_topics()


_POSTED_FILE = os.path.join(DATA_DIR, "studio_posted.json")
_posted: dict[str, dict] = {}   # slug -> {content_key: [sub_key, ...]}
_posted_loaded = False


def _load_posted() -> None:
    global _posted, _posted_loaded
    if os.path.isfile(_POSTED_FILE):
        try:
            with open(_POSTED_FILE, encoding="utf-8") as f:
                _posted = json.load(f)
        except (OSError, json.JSONDecodeError):
            _posted = {}
    _posted_loaded = True


def _save_posted() -> None:
    try:
        with open(_POSTED_FILE, "w", encoding="utf-8") as f:
            json.dump(_posted, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("studio_posted saqlash xato: %s", e)


def is_episode_posted(slug: str, content_key: str, sub_key: str) -> bool:
    if not _posted_loaded:
        _load_posted()
    return sub_key in _posted.get(slug, {}).get(content_key, [])


def mark_episode_posted(slug: str, content_key: str, sub_key: str) -> None:
    if not _posted_loaded:
        _load_posted()
    lst = _posted.setdefault(slug, {}).setdefault(content_key, [])
    if sub_key not in lst:
        lst.append(sub_key)
        _save_posted()
