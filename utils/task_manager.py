"""
Markaziy vazifa boshqaruvi — FFmpeg, yuklash va save jarayonlarini bekor qilish.

Har foydalanuvchi uchun bitta faol vazifa (user_id bo'yicha).
"""

import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# user_id → {"proc": asyncio.subprocess.Process | None, "cancelled": bool, "label": str}
_tasks: dict[int, dict] = {}


def progress_keyboard(show_cancel: bool = True) -> InlineKeyboardMarkup | None:
    if not show_cancel:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Bekor qilish", callback_data="task_cancel"),
    ]])


def register_task(user_id: int, proc=None, label: str = "") -> None:
    _tasks[user_id] = {"proc": proc, "cancelled": False, "label": label}


def set_task_proc(user_id: int, proc) -> None:
    if user_id in _tasks:
        _tasks[user_id]["proc"] = proc


def is_cancelled(user_id: int) -> bool:
    t = _tasks.get(user_id)
    return bool(t and t.get("cancelled"))


async def cancel_task(user_id: int) -> bool:
    t = _tasks.get(user_id)
    if not t:
        return False
    t["cancelled"] = True
    proc = t.get("proc")
    if proc and proc.returncode is None:
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception as e:
            logger.warning("proc kill xato: %s", e)
    return True


def clear_task(user_id: int) -> None:
    _tasks.pop(user_id, None)


def get_task_label(user_id: int) -> str:
    t = _tasks.get(user_id)
    return t.get("label", "") if t else ""


def resolve_user_id(context=None, update=None, query=None, fallback: int = 0) -> int:
    """Handler dan user_id olish — context, update yoki query."""
    if query and getattr(query, "from_user", None):
        return query.from_user.id
    if update and update.effective_user:
        return update.effective_user.id
    if context and getattr(context, "user_data", None):
        return context.user_data.get("_user_id", fallback)
    return fallback
