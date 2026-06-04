import os
import aiohttp
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from config import TEMP_DIR, BOT_TOKEN


async def video_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle received video/document files."""
    message = update.message
    file = None
    file_name = "video"

    if message.video:
        file = message.video
        file_name = message.video.file_name or "video.mp4"
    elif message.document:
        doc = message.document
        mime = doc.mime_type or ""
        if not (mime.startswith("video/") or doc.file_name and
                any(doc.file_name.lower().endswith(ext) for ext in
                    [".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".wmv"])):
            await message.reply_text(
                "❌ Bu fayl video emas. Iltimos video fayl yuboring."
            )
            return
        file = doc
        file_name = doc.file_name or "video.mp4"
    else:
        await message.reply_text(
            "❌ Noto'g'ri fayl. Iltimos video yuboring."
        )
        return

    if file.file_size and file.file_size > 2_000_000_000:
        await message.reply_text(
            "❌ Fayl juda katta (2 GB dan ortiq). "
            "Kichikroq fayl yuboring."
        )
        return

    status_msg = await message.reply_text("⏳ Video yuklanmoqda...")

    try:
        ext = os.path.splitext(file_name)[1].lstrip(".").lower() or "mp4"
        local_path = os.path.join(TEMP_DIR, f"{file.file_unique_id}.{ext}")

        # 20MB dan kichik — oddiy usul
        if file.file_size and file.file_size <= 20 * 1024 * 1024:
            tg_file = await file.get_file()
            await tg_file.download_to_drive(local_path)
        else:
            # 20MB dan katta — bot token orqali to'g'ridan-to'g'ri yuklash
            await _download_large_file(file.file_id, local_path, status_msg)

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


async def _download_large_file(file_id: str, local_path: str, status_msg):
    """Download large files (>20MB) using bot token directly."""
    # 1. file_path olish
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id}
        ) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise Exception("Fayl ma'lumotini olishda xato")
            file_path = data["result"]["file_path"]

        # 2. Faylni yuklab olish
        download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        async with session.get(download_url) as resp:
            if resp.status != 200:
                raise Exception(f"Yuklash xatosi: HTTP {resp.status}")

            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            last_reported = 0

            with open(local_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                    f.write(chunk)
                    downloaded += len(chunk)
                    # Har 10% da progress ko'rsatish
                    if total > 0:
                        percent = int(downloaded / total * 100)
                        if percent - last_reported >= 10:
                            last_reported = percent
                            mb = downloaded / 1024 / 1024
                            try:
                                await status_msg.edit_text(
                                    f"⏳ Yuklanmoqda... {percent}%\n"
                                    f"({mb:.1f} MB)"
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
