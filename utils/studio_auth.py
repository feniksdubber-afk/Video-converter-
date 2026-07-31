"""
Studiya menejerlarini boshqarish (parolsiz, avtomatik aniqlash).

Studiya a'zoligi asosiy AfsonaMovieBot platformasida (studio_members jadvali)
saqlanadi -- shu yerda faqat ikkita narsa saqlanadi:

  1. BOG'LASH (binding) -- foydalanuvchi bir nechta studiyaga menejer bo'lsa,
     u qaysi studiya nomidan ishlashni TANLAGANDA shu tanlov eslab qolinadi
     (qayta-qayta so'ralmasligi uchun). DATA_DIR/studio_bindings.json.

  2. API TOKEN -- har bir studiya uchun Afsona platformasiga yuklash uchun
     kerakli CLI upload tokeni (admin mini-app'dan olib shu yerga saqlaydi).
     DATA_DIR/studio_tokens.json.
"""

import json
import logging
import os

from config import DATA_DIR
from utils.shared_db import get_manager_studios

logger = logging.getLogger(__name__)

_BINDINGS_FILE = os.path.join(DATA_DIR, "studio_bindings.json")
_TOKENS_FILE = os.path.join(DATA_DIR, "studio_tokens.json")

_bindings: dict[str, dict] = {}   # str(telegram_id) -> {id, slug, name}
_tokens: dict[str, str] = {}      # slug -> api_token
_loaded = False


def _load() -> None:
    global _bindings, _tokens, _loaded
    if os.path.isfile(_BINDINGS_FILE):
        try:
            with open(_BINDINGS_FILE, encoding="utf-8") as f:
                _bindings = json.load(f)
        except (OSError, json.JSONDecodeError):
            _bindings = {}
    if os.path.isfile(_TOKENS_FILE):
        try:
            with open(_TOKENS_FILE, encoding="utf-8") as f:
                _tokens = json.load(f)
        except (OSError, json.JSONDecodeError):
            _tokens = {}
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load()


def _save_bindings() -> None:
    try:
        with open(_BINDINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(_bindings, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("studio_bindings saqlash xato: %s", e)


def _save_tokens() -> None:
    try:
        with open(_TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(_tokens, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("studio_tokens saqlash xato: %s", e)


# ── Bog'lash (binding) ────────────────────────────────────────────────────

def bind_user(telegram_id: int, studio: dict) -> None:
    """Foydalanuvchini shu studiyaga bog'laydi (eslab qoladi)."""
    _ensure_loaded()
    _bindings[str(telegram_id)] = {
        "id": studio["id"], "slug": studio["slug"], "name": studio["name"],
    }
    _save_bindings()


def get_bound_studio(telegram_id: int) -> dict | None:
    """Avval bog'langan studiyani qaytaradi (agar hali asosiy platformada
    ham manager sifatida qolgan bo'lsa). API tokeni ham qo'shib qaytariladi."""
    _ensure_loaded()
    entry = _bindings.get(str(telegram_id))
    if not entry:
        return None
    # Hali ham platformada shu studiyaga menejermi -- tekshirib qo'yamiz
    # (studiyadan chetlashtirilgan bo'lsa avtomatik bekor bo'ladi).
    current = get_manager_studios(telegram_id)
    if not any(s["id"] == entry["id"] for s in current):
        _bindings.pop(str(telegram_id), None)
        _save_bindings()
        return None
    return {**entry, "api_token": _tokens.get(entry["slug"], "")}


def clear_binding(telegram_id: int) -> bool:
    _ensure_loaded()
    if str(telegram_id) in _bindings:
        del _bindings[str(telegram_id)]
        _save_bindings()
        return True
    return False


def is_studio_manager(telegram_id: int) -> bool:
    return get_bound_studio(telegram_id) is not None


# ── API token boshqaruvi (admin /studiya_token orqali) ────────────────────

def set_api_token(slug: str, token: str) -> None:
    _ensure_loaded()
    _tokens[slug] = token
    _save_tokens()


def list_tokens() -> dict[str, str]:
    _ensure_loaded()
    return dict(_tokens)
