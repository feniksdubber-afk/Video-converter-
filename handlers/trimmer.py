import os
import re
from telegram import Update
from telegram.ext import ContextTypes
from utils.keyboards import main_menu_keyboard, cancel_keyboard
from utils.sender import send_file
from utils.ffmpeg_utils import trim_video, get_video_duration


def _format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _parse_time(text: str) -> str | None:
    text = text.strip()
    if re.match(r"^\d{1,2}:\d{2}:\d{2}$", text):
        return text
    if re.match(r"^\d{1,2}:\d{2}$", text):
        return "00:" + text
    if re.match(r"^\d+(\.\d+)?$", text):
        secs = float(text)
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = secs % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return None


async def show_trim_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    video_path = context.user_data.get("video_path")
    duration = get_video_duration(video_path) if video_path else 0
    dur_str = _format_duration(duration) if duration > 0 else "Noma'lum"

    context.user_data["state"] = "trim_start"
    await query.edit_message_text(
        f"✂️ *Video Kesish*\n\n"
        f"⏱ Davomiylik: {dur_str}\n\n"
        f"*Boshlanish vaqtini kiriting:*\n"
        f"Format: `HH:MM:SS` yoki `MM:SS` yoki soniyalar\n"
        f"Misol: `00:01:30` yoki `1:30` yoki `90`",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )


async def handle_trim_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = update.message.text.strip()

    if state == "trim_start":
        parsed = _parse_time(text)
        if not parsed:
            await update.message.reply_text(
                "❌ Noto'g'ri format. Qaytadan kiriting:\n"
                "Misol: `00:01:30` yoki `1:30` yoki `90`",
                reply_markup=cancel_keyboard(),
                parse_mode="Markdown",
            )
            return

        context.user_data["trim_start"] = parsed
        context.user_data["state"] = "trim_end"
        await update.message.reply_text(
            f"✅ Boshlanish: `{parsed}`\n\n"
            f"*Tugash vaqtini kiriting:*\n"
            f"Format: `HH:MM:SS` yoki `MM:SS` yoki soniyalar",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )

    elif state == "trim_end":
        parsed = _parse_time(text)
        if not parsed:
            await update.message.reply_text(
                "❌ Noto'g'ri format. Qaytadan kiriting.",
                reply_markup=cancel_keyboard(),
                parse_mode="Markdown",
            )
            return

        start = context.user_data.get("trim_start", "00:00:00")
        video_path = context.user_data.get("video_path")

        if not video_path or not os.path.exists(video_path):
            await update.message.reply_text("❌ Video topilmadi. Qaytadan video yuboring.")
            context.user_data["state"] = None
            return

        context.user_data["state"] = None
        status = await update.message.reply_text(
            f"⏳ *Kesish bajarilmoqda...*\n"
            f"⏱ `{start}` → `{parsed}`\n\nKuting...",
            parse_mode="Markdown",
        )

        ok, output_path, err = trim_video(video_path, start, parsed)

        if ok and os.path.exists(output_path):
            video_name = context.user_data.get("video_name", "video")
            base = os.path.splitext(video_name)[0]
            out_name = f"{base}_trimmed.mp4"

            await status.edit_text("✅ Tayyor! Yuborilmoqda...")
            await send_file(update.message, output_path, out_name, f"✅ Video muvaffaqiyatli kesildi!\n⏱ {start} → {parsed}", context=context)
            os.remove(output_path)
            await update.message.reply_text("Boshqa amal?", reply_markup=main_menu_keyboard())
        else:
            await status.edit_text(
                f"❌ Kesishda xato:\n`{err}`",
                parse_mode="Markdown",
            )
            await update.message.reply_text("Qaytadan?", reply_markup=main_menu_keyboard())
