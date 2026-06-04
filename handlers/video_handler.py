import os
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from config import TEMP_DIR, BOT_TOKEN, API_ID, API_HASH
from pyrogram import Client

_pyrogram_client = None


async def get_pyrogram_client() -> Client:
    global _pyrogram_client
    if _pyrogram_client is None or not _pyrogram_client.is_connected:
        _pyrogram_client = Client(
            "bot_session",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workdir=TEMP_DIR,
        )
        await _pyrogram_client.start()
    return _pyrogram_client


def _progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    return "[" + "█" * filled + "░" * (length - filled) + "]"


async def video_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    file = None
    file_name = "video"

    if message.video:
        file = message.video
        file_name = message.video.file_name or "video.mp4"
    elif message.document:
        doc = message.document
        mime = doc.mime_type or ""
        VIDEO_EXTS = [
            ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v",
            ".ts", ".wmv", ".mpeg", ".mpg", ".3gp", ".3g2", ".ogv",
            ".rm", ".rmvb", ".divx", ".vob", ".mts", ".m2ts", ".f4v",
            ".asf", ".amv", ".mxf", ".roq", ".nsv", ".yuv", ".dv",
        ]
        if not (mime.startswith("video/") or (doc.file_name and
                any(doc.file_name.lower().endswith(ext) for ext in VIDEO_EXTS))):
            await message.reply_text("❌ Bu fayl video emas. Iltimos video fayl yuboring.")
            return
        file = doc
        file_name = doc.file_name or "video.mp4"
    else:
        await message.reply_text("❌ Noto'g'ri fayl. Iltimos video yuboring.")
        return

    if file.file_size and file.file_size > 2_000_000_000:
        await message.reply_text("❌ Fayl juda katta (2 GB dan ortiq).")
        return

    status_msg = await message.reply_text("⏳ Video yuklanmoqda... 0%")

    try:
        ext = os.path.splitext(file_name)[1].lstrip(".").lower() or "mp4"
        local_path = os.path.join(TEMP_DIR, f"{file.file_unique_id}.{ext}")

        if file.file_size and file.file_size <= 20 * 1024 * 1024:
            # 20MB gacha — PTB oddiy usuli
            await status_msg.edit_text("⬇️ *Yuklanmoqda...*\n\n`[████████████]` `100%`\n_(kichik fayl)_", parse_mode="Markdown")
            tg_file = await file.get_file()
            await tg_file.download_to_drive(local_path)
        else:
            # 20MB dan katta — Pyrogram MTProto + progress
            await _download_via_pyrogram(file.file_id, file.file_size, local_path, status_msg)

        context.user_data["video_path"] = local_path
        context.user_data["video_name"] = file_name
        context.user_data["state"] = None

        await status_msg.edit_text(
            f"✅ *Video qabul qilindi!*\n\n"
            f"📁 Fayl: `{file_name}`\n"
            f"📦 Hajmi: {_format_size(file.file_size)}\n\n"
            f"Quyidagi amallardan birini tanlang:",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ Video yuklashda xato: {str(e)}\n"
            "Qaytadan urinib ko'ring."
        )


async def _download_via_pyrogram(file_id: str, file_size: int, local_path: str, status_msg):
    import asyncio
    client = await get_pyrogram_client()
    total_mb = (file_size or 0) / 1024 / 1024
    last_percent = [0]

    async def progress(current, total):
        if total == 0:
            return
        percent = min(int(current / total * 100), 99)
        if percent - last_percent[0] >= 5:
            last_percent[0] = percent
            cur_mb = current / 1024 / 1024
            bar = _progress_bar(percent)
            try:
                await status_msg.edit_text(
                    f"⬇️ *Yuklanmoqda...*\n\n"
                    f"{bar} `{percent}%`\n"
                    f"`{cur_mb:.1f}` / `{total_mb:.1f}` MB",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    spinners = ["⬇️", "⏬", "⬇️", "📥"]
    spinner_state = [0]

    async def file_watcher():
        """Har 5 sekundda fayl hajmini o'lchaб progress ko'rsatadi"""
        while True:
            await asyncio.sleep(5)
            try:
                cur = os.path.getsize(local_path) if os.path.exists(local_path) else 0
                cur_mb = cur / 1024 / 1024
                if file_size and file_size > 0:
                    percent = min(int(cur / file_size * 100), 99)
                    bar = _progress_bar(percent)
                    text = (
                        f"⬇️ *Yuklanmoqda...*\n\n"
                        f"{bar} `{percent}%`\n"
                        f"`{cur_mb:.1f}` / `{total_mb:.1f}` MB"
                    )
                else:
                    icon = spinners[spinner_state[0] % len(spinners)]
                    spinner_state[0] += 1
                    text = f"{icon} *Yuklanmoqda...* `{cur_mb:.1f}` MB"
                try:
                    await status_msg.edit_text(text, parse_mode="Markdown")
                except Exception:
                    pass
            except Exception:
                break

    watcher_task = asyncio.create_task(file_watcher())
    try:
        await client.download_media(file_id, file_name=local_path, progress=progress)
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass

    try:
        await status_msg.edit_text(
            f"⬇️ *Yuklanmoqda...*\n\n{_progress_bar(100)} `100%`",
            parse_mode="Markdown",
        )
    except Exception:
        pass


def _format_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "Noma'lum"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} GB"
