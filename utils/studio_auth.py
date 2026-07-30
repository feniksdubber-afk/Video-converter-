"""
Studiya menejerlari uchun login/parol tizimi.

Har bir studiya uchun bot xavfsiz parol generatsiya qiladi (admin buni
menejerga qo'lda beradi). Menejer /start orqali "Studiya nomi Parol"
formatida bir marta kiritadi -> to'g'ri bo'lsa uning Telegram ID'si
shu studiyaga abadiy bog'lanadi (qayta kiritish talab qilinmaydi).

Ma'lumot DATA_DIR/studios.json faylida saqlanadi. Parol hech qachon ochiq
holda saqlanmaydi -- faqat tuz (salt) + SHA-256 xesh saqlanadi.
"""

import json
import logging
import os
import re
import secrets
import hashlib

from config import DATA_DIR

logger = logging.getLogger(__name__)

_STUDIOS_FILE = os.path.join(DATA_DIR, "studios.json")

_studios: dict[str, dict] = {}
_loaded = False


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "studio"


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _save() -> None:
    try:
        with open(_STUDIOS_FILE, "w", encoding="utf-8") as f:
            json.dump(_studios, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("studios.json saqlash xato: %s", e)


def _load() -> None:
    global _studios, _loaded
    if os.path.isfile(_STUDIOS_FILE):
        try:
            with open(_STUDIOS_FILE, encoding="utf-8") as f:
                _studios = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("studios.json o'qish xato: %s", e)
            _studios = {}
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def create_studio(name: str, api_token: str = "") -> tuple[str, str]:
    """Yangi studiya yaratadi. (slug, ochiq_parol) qaytaradi -- parol faqat
    shu chaqiruvda ko'rinadi, keyin qayta olib bo'lmaydi."""
    _ensure_loaded()
    base_slug = _slugify(name)
    slug = base_slug
    i = 2
    while slug in _studios:
        slug = f"{base_slug}-{i}"
        i += 1

    password = secrets.token_urlsafe(9)  # ~12 xavfsiz belgi
    salt = secrets.token_hex(8)
    _studios[slug] = {
        "name": name,
        "salt": salt,
        "password_hash": _hash_password(password, salt),
        "telegram_id": None,
        "api_token": api_token,
    }
    _save()
    logger.info("Studiya yaratildi: %s (%s)", name, slug)
    return slug, password


def verify_login(name_or_slug: str, password: str, telegram_id: int) -> str | None:
    """Login/parolni tekshiradi. To'g'ri bo'lsa telegram_id ni shu studiyaga
    bog'lab, slug qaytaradi. Noto'g'ri bo'lsa None qaytaradi."""
    _ensure_loaded()
    key = _slugify(name_or_slug)
    studio = _studios.get(key)
    slug = key

    if not studio:
        for s_slug, s in _studios.items():
            if s.get("name", "").strip().lower() == name_or_slug.strip().lower():
                studio, slug = s, s_slug
                break

    if not studio:
        return None
    if _hash_password(password, studio["salt"]) != studio["password_hash"]:
        return None

    studio["telegram_id"] = telegram_id
    _save()
    logger.info("Studiya menejeri kirdi: %s -> telegram_id=%s", slug, telegram_id)
    return slug


def get_studio_for_user(telegram_id: int) -> dict | None:
    _ensure_loaded()
    for slug, s in _studios.items():
        if s.get("telegram_id") == telegram_id:
            return {"slug": slug, **s}
    return None


def is_studio_manager(telegram_id: int) -> bool:
    return get_studio_for_user(telegram_id) is not None


def unbind_studio(slug: str) -> bool:
    """Studiyani hech qanday Telegram ID'ga bog'lanmagan holga qaytaradi
    (masalan menejer telefonini almashtirganda, admin qayta login qildirish
    uchun ishlatadi)."""
    _ensure_loaded()
    if slug in _studios:
        _studios[slug]["telegram_id"] = None
        _save()
        return True
    return False


def delete_studio(slug: str) -> bool:
    _ensure_loaded()
    if slug in _studios:
        del _studios[slug]
        _save()
        return True
    return False


def set_api_token(slug: str, token: str) -> bool:
    _ensure_loaded()
    if slug in _studios:
        _studios[slug]["api_token"] = token
        _save()
        return True
    return False


def list_studios() -> list[dict]:
    _ensure_loaded()
    return [{"slug": k, **v} for k, v in _studios.items()]
