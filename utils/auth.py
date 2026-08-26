"""
Foydalanuvchi ruxsati — faqat whitelist dagi TG ID lar botdan foydalanadi.

ALLOWED_USER_IDS / ADMIN_USER_IDS env dan boshlang'ich ro'yxat olinadi.
/allow va /deny orqali DATA_DIR/allowed_users.json yangilanadi.
"""

import logging
import os
from config import DATA_DIR, ALLOWED_USER_IDS_ENV, ADMIN_USER_IDS_ENV
from utils.atomic_json import load_json, save_json

logger = logging.getLogger(__name__)

_ALLOWED_FILE = os.path.join(DATA_DIR, "allowed_users.json")

# Kesh — har restart da env bilan sinxronlanadi
_allowed: set[int] = set()
_admins: set[int] = set()
_loaded = False


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def _save_allowed() -> None:
    save_json(_ALLOWED_FILE, sorted(_allowed))


def _load_allowed() -> None:
    global _allowed, _admins, _loaded
    _admins = _parse_ids(ADMIN_USER_IDS_ENV)
    _allowed = _parse_ids(ALLOWED_USER_IDS_ENV)

    data = load_json(_ALLOWED_FILE, default=[])
    if isinstance(data, list):
        try:
            _allowed.update(int(x) for x in data)
        except (TypeError, ValueError) as e:
            logger.warning("allowed_users formatida xato: %s", e)

    # Adminlar har doim ruxsatli
    _allowed.update(_admins)

    if not _allowed and _admins:
        logger.warning("ALLOWED_USER_IDS bo'sh — faqat adminlar ishlaydi.")

    _save_allowed()
    _loaded = True
    logger.info("Auth: %d ruxsatli, %d admin", len(_allowed), len(_admins))


def reload_auth() -> None:
    global _loaded
    _loaded = False
    _load_allowed()


def _ensure_loaded() -> None:
    if not _loaded:
        _load_allowed()


def is_admin(user_id: int) -> bool:
    _ensure_loaded()
    return user_id in _admins


def is_allowed(user_id: int) -> bool:
    _ensure_loaded()
    if not _allowed:
        # Hech kim sozlanmagan — faqat admin (agar bor bo'lsa)
        return user_id in _admins
    return user_id in _allowed


def list_allowed() -> list[int]:
    _ensure_loaded()
    return sorted(_allowed)


def list_admins() -> list[int]:
    _ensure_loaded()
    return sorted(_admins)


def allow_user(user_id: int) -> bool:
    _ensure_loaded()
    if user_id in _allowed:
        return False
    _allowed.add(user_id)
    _save_allowed()
    return True


def deny_user(user_id: int) -> bool:
    _ensure_loaded()
    if is_admin(user_id):
        return False  # adminni o'chirib bo'lmaydi
    if user_id not in _allowed:
        return False
    _allowed.discard(user_id)
    _save_allowed()
    return True
