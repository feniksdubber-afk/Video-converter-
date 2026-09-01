"""
/status — faqat admin uchun server va bot holati diagnostikasi.

Ko'rsatadi:
  - Navbat holati (band/kutayotgan/sig'im)
  - TEMP_DIR hajmi va fayllar soni
  - Ruxsatli foydalanuvchilar/adminlar soni
  - R2 sozlanganmi
"""

import os
import shutil

from telegram import Update
from telegram.ext import ContextTypes

from config import TEMP_DIR, R2_BUCKET
from utils.auth import is_admin, list_allowed, list_admins


def _fmt_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def _dir_stats(path: str) -> tuple[int, int]:
    """(fayllar soni, umumiy hajm baytlarda) — faqat to'g'ridan-to'g'ri fayllar."""
    count = 0
    total = 0
    try:
        for fname in os.listdir(path):
            fpath = os.path.join(path, fname)
            try:
                if os.path.isfile(fpath):
                    count += 1
                    total += os.path.getsize(fpath)
            except Exception:
                pass
    except Exception:
        pass
    return count, total


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Faqat admin.")
        return

    from utils.task_queue import queue_snapshot

    running, waiting, capacity = queue_snapshot()

    temp_count, temp_bytes = _dir_stats(TEMP_DIR)

    disk_line = ""
    try:
        usage = shutil.disk_usage(TEMP_DIR)
        disk_line = (
            f"\n💽 *Disk:* {_fmt_size(usage.used)} / {_fmt_size(usage.total)} "
            f"(bo'sh: {_fmt_size(usage.free)})"
        )
    except Exception:
        pass

    allowed = list_allowed()
    admins = list_admins()

    r2_status = "✅ sozlangan" if R2_BUCKET else "❌ sozlanmagan"

    # ── Studiya yuklash tizimi: bir nechta studiya menejeri bir vaqtda
    # ishlayotganda umumiy holatni ko'rish uchun (bog'langan guruhlar,
    # tokenli studiyalar, navbatdagi videolar, hozir faol /joylash'lar). ──
    studio_lines = ""
    try:
        from utils.studio_group import list_groups
        from utils.studio_auth import list_tokens
        from utils.studio_topic_queue import queue_totals
        from handlers.studio_topic_upload import active_joylash_count

        groups = list_groups()
        tokens = list_tokens()
        queued_videos, queued_topics = queue_totals()
        active_joylash = active_joylash_count()

        studio_lines = (
            "\n\n🏢 *Studiya yuklash tizimi:*\n"
            f"  • Bog'langan guruhlar: {len(groups)}\n"
            f"  • Token sozlangan studiyalar: {len(tokens)}\n"
            f"  • Navbatda: {queued_videos} video ({queued_topics} topic'da)\n"
            f"  • Hozir faol /joylash: {active_joylash}"
        )
    except Exception:
        # Studiya moduli sozlanmagan/xato bo'lsa /status umuman ishlamay
        # qolmasligi kerak -- shu bo'lim shunchaki ko'rsatilmaydi.
        pass

    text = (
        "📊 *Bot holati*\n\n"
        f"🧵 *Navbat:*\n"
        f"  • Band: {running}/{capacity}\n"
        f"  • Kutmoqda: {waiting}\n\n"
        f"🗂 *Vaqtinchalik fayllar (TEMP_DIR):*\n"
        f"  • Fayllar: {temp_count}\n"
        f"  • Hajm: {_fmt_size(temp_bytes)}"
        f"{disk_line}\n\n"
        f"👥 *Foydalanuvchilar:*\n"
        f"  • Ruxsatli: {len(allowed)}\n"
        f"  • Admin: {len(admins)}"
        f"{studio_lines}\n\n"
        f"☁️ *R2 saqlash:* {r2_status}"
    )

    await update.message.reply_text(text, parse_mode="Markdown")
