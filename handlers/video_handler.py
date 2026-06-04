import os
from telegram import Update, Message
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard
from config import TEMP_DIR


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
        tg_file = await file.get_file()
        ext = os.path.splitext(file_name)[1].lstrip(".").lower() or "mp4"
        local_path = os.path.join(TEMP_DIR, f"{file.file_unique_id}.{ext}")
        await tg_file.download_to_drive(local_path)

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


def _format_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "Noma'lum"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} GB"
