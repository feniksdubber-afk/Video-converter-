"""
Markaziy navbat tizimi — og'ir amallar (FFmpeg konvertatsiya, R2'ga yuklash)
bir vaqtning o'zida faqat MAX_CONCURRENT dona parallel ishlaydi. Qolganlari
FIFO tartibida navbatda kutadi, o'z holatini ("N-o'rinda") ko'radi va
istalgan payt "❌ Navbatdan chiqish" tugmasi bilan chiqib ketishi mumkin.

Ishlatilishi:

    ticket = new_ticket()
    ok = await acquire_slot(ticket, status_msg, label="MP4'ga o'tkazish")
    if not ok:
        return  # foydalanuvchi navbatda ekanida bekor qildi
    try:
        ... og'ir amalni bajarish ...
    finally:
        release_slot()
"""

import asyncio
import itertools
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def _default_max_concurrent() -> int:
    """
    Bir vaqtda nechta og'ir amal (FFmpeg/yuklash) parallel ishlashi mumkinligini
    aniqlaydi. `.env` dagi MAX_CONCURRENT_TASKS ustunlik qiladi; bo'lmasa,
    server CPU yadrolari soniga qarab avtomatik hisoblanadi (yarmi, min 1,
    max 4 — FFmpeg CPU-talab bo'lgani uchun ko'p parallel vazifa foydali emas).
    """
    env_val = os.environ.get("MAX_CONCURRENT_TASKS", "").strip()
    if env_val.isdigit() and int(env_val) > 0:
        return int(env_val)
    try:
        cpu_count = os.cpu_count() or 1
    except Exception:
        cpu_count = 1
    return max(1, min(4, cpu_count // 2 or 1))


MAX_CONCURRENT = _default_max_concurrent()
_POLL_TIMEOUT = 3    # navbat holatini necha soniyada bir yangilab turish (fallback)

_waiting: list[dict] = []   # FIFO: [{"id", "user_id", "cancelled"}, ...]
_running = 0
_wake = asyncio.Event()
_counter = itertools.count(1)

logger.info("Navbat tizimi: MAX_CONCURRENT=%d", MAX_CONCURRENT)


def new_ticket() -> int:
    return next(_counter)


def _cancel_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Navbatdan chiqish", callback_data=f"queue_cancel_{ticket_id}"),
    ]])


def cancel_ticket(ticket_id: int) -> bool:
    """queue_cancel_{id} tugmasi bosilganda chaqiriladi."""
    for entry in _waiting:
        if entry["id"] == ticket_id:
            entry["cancelled"] = True
            _wake.set()
            return True
    return False


def queue_snapshot() -> tuple[int, int, int]:
    """(band joylar, navbatdagilar soni, umumiy sig'im) -- diagnostika/status uchun."""
    return _running, len(_waiting), MAX_CONCURRENT


async def acquire_slot(ticket_id: int, user_id: int, status_msg, label: str = "Vazifa") -> bool:
    """
    Navbatga turadi va o'z galini kutadi.
    True  -> gal keldi, og'ir amalni boshlash mumkin (release_slot() chaqirishni unutmang).
    False -> foydalanuvchi navbatda turib bekor qildi.
    """
    global _running

    # Tezkor yo'l: navbat bo'sh va joy bor -- kutmasdan darhol boshlaydi.
    if _running < MAX_CONCURRENT and not _waiting:
        _running += 1
        return True

    entry = {"id": ticket_id, "user_id": user_id, "cancelled": False}
    _waiting.append(entry)
    last_shown = None

    try:
        while True:
            if entry["cancelled"]:
                return False

            # Navbat boshida turgan va bo'sh slot bo'lsa — joy olamiz.
            # Joy olgan zahoti _waiting ro'yxatidan chiqaramiz, aks holda
            # MAX_CONCURRENT > 1 bo'lganda ham faqat bitta kishi ilgarilab,
            # qolgan bo'sh slotlar band qilinmay qoladi.
            if _waiting and _waiting[0] is entry and _running < MAX_CONCURRENT:
                _running += 1
                _waiting.remove(entry)
                return True

            position = (_waiting.index(entry) + 1) if entry in _waiting else 0
            if position != last_shown:
                last_shown = position
                try:
                    await status_msg.edit_text(
                        f"🕒 *{label}*\n\n"
                        f"Navbatda kutyapsiz — *{position}-o'rin*.\n"
                        f"Boshqa vazifa tugashi bilan avtomatik boshlanadi.",
                        reply_markup=_cancel_keyboard(ticket_id),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

            _wake.clear()
            try:
                await asyncio.wait_for(_wake.wait(), timeout=_POLL_TIMEOUT)
            except asyncio.TimeoutError:
                pass
    finally:
        if entry in _waiting:
            _waiting.remove(entry)


def release_slot() -> None:
    global _running
    _running = max(0, _running - 1)
    _wake.set()
